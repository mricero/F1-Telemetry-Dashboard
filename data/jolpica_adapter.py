"""Jolpica F1 Adapter - Free historical data via Ergast-compatible API.

Two upstream constraints shape this module:

* **Pagination.** Responses default to ``limit=30`` (max 100) and report
  ``MRData.total``/``offset``. Without paging, ``seasons`` returned 30 of ~77
  and a race's lap times were cut to the first 30 of ~1200 rows.
* **Rate limits.** Unauthenticated use is capped at 4 requests/second burst
  and 500/hour, and the API answers 429 with a ``Retry-After`` header.
"""

import time
import requests
import pandas as pd
from collections import deque
from functools import wraps
from typing import Callable, Deque, Dict, List, Optional


def _retry_after_seconds(response, fallback: float) -> float:
    """Seconds to wait after a 429, honouring the ``Retry-After`` header."""
    header = (getattr(response, "headers", None) or {}).get("Retry-After")
    if header is None:
        return fallback
    try:
        return max(0.0, float(header))
    except (TypeError, ValueError):  # HTTP-date form: back off instead
        return fallback


def _instance_memo(maxsize: int = 64):
    """Per-instance memoization decorator.

    Unlike ``functools.lru_cache`` on methods, this does not retain adapter
    instances (and their requests.Session) in a global cache, and each
    DataSourceManager gets its own cache lifetime.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(self, *args):
            key = (fn.__name__, args)
            cache = self._memo
            if key in cache:
                return cache[key]
            value = fn(self, *args)
            if len(cache) >= maxsize:
                cache.clear()  # simple reset policy for a UI-lifetime cache
            cache[key] = value
            return value

        return wrapper

    return decorator


class JolpicaAdapter:
    """Free historical data via Jolpica F1 API (Ergast-compatible)."""

    BASE_URL = "https://api.jolpi.ca/ergast/f1"

    # Jolpica's documented limits.
    PAGE_LIMIT = 100  # maximum items per response
    BURST_REQUESTS = 4  # requests allowed per BURST_WINDOW
    BURST_WINDOW = 1.0  # seconds
    MAX_RETRIES = 3  # attempts after a 429 before giving up
    BACKOFF_BASE = 1.0  # seconds, doubled per retry when Retry-After is absent

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "F1-Telemetry-Dashboard/1.0"})
        self._memo: Dict = {}
        self._request_times: Deque[float] = deque(maxlen=self.BURST_REQUESTS)

    def _throttle(self) -> None:
        """Token bucket: never exceed BURST_REQUESTS per BURST_WINDOW."""
        if len(self._request_times) == self._request_times.maxlen:
            wait = self.BURST_WINDOW - (time.monotonic() - self._request_times[0])
            if wait > 0:
                time.sleep(wait)
        self._request_times.append(time.monotonic())

    def _fetch(self, endpoint: str, params: Dict = None) -> Dict:
        """Generic fetch with throttling, 429 backoff and error handling."""
        url = f"{self.BASE_URL}/{endpoint}"
        delay = self.BACKOFF_BASE
        for attempt in range(self.MAX_RETRIES):
            self._throttle()
            response = None
            try:
                response = self.session.get(url, params=params, timeout=15)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                if getattr(response, "status_code", None) != 429:
                    raise ConnectionError(f"Failed to fetch {endpoint}: {e}")
                if attempt == self.MAX_RETRIES - 1:
                    break
                time.sleep(_retry_after_seconds(response, delay))
                delay *= 2
        raise ConnectionError(
            f"Failed to fetch {endpoint}: Jolpica rate limit not cleared after "
            f"{self.MAX_RETRIES} attempts"
        )

    def _fetch_paged(
        self, endpoint: str, extract: Callable[[Dict], list], params: Optional[Dict] = None
    ) -> list:
        """Every item of a paged endpoint, following MRData.total.

        ``extract`` pulls the list out of one page's payload; the lists are
        concatenated in page order.
        """
        collected: list = []
        offset = 0
        while True:
            page = self._fetch(
                endpoint, {**(params or {}), "limit": self.PAGE_LIMIT, "offset": offset}
            )
            collected.extend(extract(page))
            total = int(page.get("MRData", {}).get("total", 0) or 0)
            offset += self.PAGE_LIMIT
            if offset >= total:
                return collected

    def _fetch_race_list(self, endpoint: str, key: str) -> Dict:
        """A single-race payload whose ``Races[0][key]`` list is fully paged.

        Lap times (~1 200 rows) and busy races' pit stops both overflow one
        page, and the callers index ``RaceTable.Races[0]``, so the merged list
        is written back into the first page's shape.
        """
        first: Optional[Dict] = None

        def extract(page: Dict) -> list:
            nonlocal first
            if first is None:
                first = page
            races = page.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            return races[0].get(key, []) if races else []

        items = self._fetch_paged(endpoint, extract)
        if first is None:
            return {"MRData": {"RaceTable": {"Races": []}}}
        races = first.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        if races:
            races[0][key] = items
        return first

    @_instance_memo(maxsize=32)
    def get_seasons(self) -> List[int]:
        """Get all available seasons (~77, i.e. well past one page)."""
        seasons = self._fetch_paged(
            "seasons.json",
            lambda page: page["MRData"]["SeasonTable"]["Seasons"],
        )
        return [int(s["season"]) for s in seasons]

    @_instance_memo(maxsize=32)
    def get_schedule(self, year: int) -> pd.DataFrame:
        """Get race schedule for a year."""
        data = self._fetch(f"{year}.json")
        races = data["MRData"]["RaceTable"]["Races"]

        if not races:
            return pd.DataFrame()

        rows = []
        for race in races:
            circuit = race["Circuit"]
            rows.append(
                {
                    "round": int(race["round"]),
                    "race_name": race["raceName"],
                    "circuit_name": circuit["circuitName"],
                    "circuit_id": circuit["circuitId"],
                    "location": circuit["Location"]["locality"],
                    "country": circuit["Location"]["country"],
                    "date": race["date"],
                    "time": race.get("time", ""),
                    "url": race["url"],
                    "year": year,
                }
            )

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        return df

    @_instance_memo(maxsize=64)
    def get_session_results(self, year: int, round_num: int) -> Dict:
        """Get race results."""
        return self._fetch(f"{year}/{round_num}/results.json")

    @_instance_memo(maxsize=64)
    def get_qualifying_results(self, year: int, round_num: int) -> Dict:
        """Get qualifying results."""
        return self._fetch(f"{year}/{round_num}/qualifying.json")

    @_instance_memo(maxsize=64)
    def get_practice_results(self, year: int, round_num: int, session: str = "1") -> Dict:
        """Get practice results (session: 1, 2, or 3)."""
        return self._fetch(f"{year}/{round_num}/{session}/practice.json")

    @_instance_memo(maxsize=32)
    def get_driver_standings(self, year: int) -> Dict:
        return self._fetch(f"{year}/driverStandings.json")

    @_instance_memo(maxsize=32)
    def get_constructor_standings(self, year: int) -> Dict:
        return self._fetch(f"{year}/constructorStandings.json")

    @_instance_memo(maxsize=64)
    def get_driver_info(self, year: int) -> Dict:
        return self._fetch(f"{year}/drivers.json")

    @_instance_memo(maxsize=64)
    def get_constructor_info(self, year: int) -> Dict:
        return self._fetch(f"{year}/constructors.json")

    @_instance_memo(maxsize=64)
    def get_lap_times(self, year: int, round_num: int) -> Dict:
        """Get lap times for a race (paged: a race is ~1 200 timing rows)."""
        return self._fetch_race_list(f"{year}/{round_num}/laps.json", "Laps")

    @_instance_memo(maxsize=64)
    def get_pit_stops(self, year: int, round_num: int) -> Dict:
        """Get pit stops for a race (paged: busy races exceed one page)."""
        return self._fetch_race_list(f"{year}/{round_num}/pitstops.json", "PitStops")

    def get_driver_standings_df(self, year: int) -> pd.DataFrame:
        """Get driver standings as DataFrame."""
        data = self.get_driver_standings(year)
        standings = data["MRData"]["StandingsTable"]["StandingsLists"][0]["DriverStandings"]

        rows = []
        for s in standings:
            driver = s["Driver"]
            rows.append(
                {
                    "position": int(s["position"]),
                    "points": float(s["points"]),
                    "wins": int(s["wins"]),
                    "driver_id": driver["driverId"],
                    "code": driver["code"],
                    "given_name": driver["givenName"],
                    "family_name": driver["familyName"],
                    "date_of_birth": driver["dateOfBirth"],
                    "nationality": driver["nationality"],
                    "constructor": (
                        s["Constructors"][0]["constructorId"] if s["Constructors"] else None
                    ),
                    "constructor_name": s["Constructors"][0]["name"] if s["Constructors"] else None,
                }
            )

        return pd.DataFrame(rows)

    def get_race_results_df(self, year: int, round_num: int) -> pd.DataFrame:
        """Get race results as DataFrame."""
        data = self.get_session_results(year, round_num)
        races = data["MRData"]["RaceTable"]["Races"]

        if not races:
            return pd.DataFrame()

        race = races[0]
        results = race["Results"]

        rows = []
        for r in results:
            driver = r["Driver"]
            constructor = r["Constructor"]
            rows.append(
                {
                    "position": int(r["position"]) if r["position"].isdigit() else r["position"],
                    "driver_id": driver["driverId"],
                    "code": driver["code"],
                    "given_name": driver["givenName"],
                    "family_name": driver["familyName"],
                    "constructor_id": constructor["constructorId"],
                    "constructor_name": constructor["name"],
                    "grid": int(r["grid"]),
                    "laps": int(r["laps"]),
                    "status": r["status"],
                    "points": float(r["points"]),
                    "time": r.get("Time", {}).get("time") if "Time" in r else None,
                    "fastest_lap": (
                        r.get("FastestLap", {}).get("rank") if "FastestLap" in r else None
                    ),
                    "fastest_lap_time": (
                        r.get("FastestLap", {}).get("Time", {}).get("time")
                        if "FastestLap" in r
                        else None
                    ),
                }
            )

        return pd.DataFrame(rows)

    def get_schedule_df(self, year: int) -> pd.DataFrame:
        """Alias for get_schedule."""
        return self.get_schedule(year)

    def is_race_weekend(self, year: int = None) -> bool:
        """Whether a race falls within three days of now.

        Deliberately coarse, and **not** a live-session test: it says nothing
        about session times, so it reads True all week. Use
        :func:`data.fastf1_adapter.live_session_now` to decide whether
        anything is actually on air (LIVE-15).
        """
        from datetime import datetime

        if year is None:
            year = datetime.now().year

        schedule = self.get_schedule(year)
        # Jolpica dates are timezone-aware (UTC), so use timezone-aware now
        now = pd.Timestamp.now(tz="UTC")

        for _, race in schedule.iterrows():
            race_date = race["date"]
            # Check if within 3 days (Fri-Sun)
            if abs((race_date - now).total_seconds()) < 3 * 86400:
                return True
        return False

    @_instance_memo(maxsize=64)
    def get_lap_times_df(self, year: int, round_num: int) -> pd.DataFrame:
        """Get lap times as DataFrame."""
        data = self.get_lap_times(year, round_num)
        races = data["MRData"]["RaceTable"]["Races"]

        if not races:
            return pd.DataFrame()

        race = races[0]
        laps = race.get("Laps", [])

        rows = []
        for lap in laps:
            lap_num = int(lap["number"])
            for timing in lap.get("Timings", []):
                driver = timing.get("driverId", "")
                rows.append(
                    {
                        "driver_id": driver,
                        "lap_number": lap_num,
                        "lap_time": timing.get("time", ""),
                        "position": timing.get("position", ""),
                    }
                )

        return pd.DataFrame(rows)

    @_instance_memo(maxsize=64)
    def get_pit_stops_df(self, year: int, round_num: int) -> pd.DataFrame:
        """Get pit stops as DataFrame."""
        data = self.get_pit_stops(year, round_num)
        races = data["MRData"]["RaceTable"]["Races"]

        if not races:
            return pd.DataFrame()

        race = races[0]
        pit_stops = race.get("PitStops", [])

        rows = []
        for stop in pit_stops:
            rows.append(
                {
                    "driver_id": stop.get("driverId", ""),
                    "lap": int(stop.get("lap", 0)),
                    "stop": int(stop.get("stop", 0)),
                    "time": stop.get("time", ""),
                    "duration": stop.get("duration", ""),
                }
            )

        return pd.DataFrame(rows)

    @_instance_memo(maxsize=64)
    def get_qualifying_df(self, year: int, round_num: int) -> pd.DataFrame:
        """Get qualifying results as DataFrame."""
        data = self.get_qualifying_results(year, round_num)
        races = data["MRData"]["RaceTable"]["Races"]

        if not races:
            return pd.DataFrame()

        race = races[0]
        results = race.get("QualifyingResults", [])

        rows = []
        for r in results:
            driver = r["Driver"]
            constructor = r["Constructor"]
            rows.append(
                {
                    "position": int(r["position"]) if r["position"].isdigit() else r["position"],
                    "driver_id": driver["driverId"],
                    "code": driver["code"],
                    "given_name": driver["givenName"],
                    "family_name": driver["familyName"],
                    "constructor_id": constructor["constructorId"],
                    "constructor_name": constructor["name"],
                    "q1": r.get("Q1", ""),
                    "q2": r.get("Q2", ""),
                    "q3": r.get("Q3", ""),
                }
            )

        return pd.DataFrame(rows)
