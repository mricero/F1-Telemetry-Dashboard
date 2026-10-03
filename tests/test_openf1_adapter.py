"""OpenF1 team radio (FEAT-05), offline. Rows mirror the real endpoint's shape."""

import pandas as pd
import pytest
import requests

from f1dash.data import openf1_adapter as of1

SESSIONS = [
    {
        "session_key": 9158,
        "session_name": "Race",
        "session_type": "Race",
        "date_start": "2023-09-03T13:00:00+00:00",
        "date_end": "2023-09-03T15:00:00+00:00",
        "meeting_key": 1217,
        "country_name": "Italy",
        "location": "Monza",
        "year": 2023,
    },
    {
        "session_key": 9200,
        "session_name": "Race",
        "date_start": "2023-09-17T12:00:00+00:00",
        "country_name": "Singapore",
        "year": 2023,
    },
]
RADIO = [
    {
        "date": "2023-09-03T13:10:30.500000+00:00",
        "driver_number": 1,
        "meeting_key": 1217,
        "session_key": 9158,
        "recording_url": "https://livetiming.formula1.com/static/2023/Italy/TeamRadio/MAXVER01.mp3",
    },
    {
        "date": "2023-09-03T13:05:00+00:00",
        "driver_number": 44,
        "meeting_key": 1217,
        "session_key": 9158,
        "recording_url": "https://livetiming.formula1.com/static/2023/Italy/TeamRadio/LEWHAM01.mp3",
    },
]
DRIVERS = pd.DataFrame({"driver_number": ["1", "44"], "name_acronym": ["VER", "HAM"]})


class _Response:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self._payload


def test_session_is_matched_by_nearest_date():
    key = of1.find_session_key(SESSIONS, pd.Timestamp("2023-09-03 15:00"))
    assert key == 9158


def test_no_session_within_a_day_is_none():
    assert of1.find_session_key(SESSIONS, pd.Timestamp("2023-10-01")) is None


def test_country_decides_without_a_date():
    assert of1.find_session_key(SESSIONS, None, "singapore") == 9200


def test_frame_is_sorted_with_acronyms_and_session_clock_seconds():
    frame = of1.radio_frame(RADIO, DRIVERS, 3600.0, "2023-09-03T13:00:00+00:00")
    assert list(frame["Driver"]) == ["HAM", "VER"]
    assert list(frame["Time"]) == [3900.0, 4230.5]
    assert frame["Url"].iloc[0].endswith("LEWHAM01.mp3")


def test_unknown_driver_falls_back_to_number_and_missing_start_gives_nan_time():
    frame = of1.radio_frame(RADIO, None, None, None)
    assert list(frame["Driver"]) == ["44", "1"]
    assert frame["Time"].isna().all()


def test_empty_rows_give_the_schema():
    assert list(of1.radio_frame([], None, None, None).columns) == of1.COLUMNS


def test_before_2023_makes_no_request(monkeypatch):
    monkeypatch.setattr(of1.requests, "get", lambda *a, **k: pytest.fail("requested"))
    assert of1.get_team_radio(2022, "Race").empty


def test_get_team_radio_never_sends_credentials(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None, headers=None):
        calls.append((url, params, headers))
        return _Response(payload=SESSIONS if url.endswith("/sessions") else RADIO)

    monkeypatch.setattr(of1.requests, "get", fake_get)
    frame = of1.get_team_radio(
        2023, "Race", pd.Timestamp("2023-09-03 14:00"), "Italy", DRIVERS, 0.0
    )
    assert list(frame["Driver"]) == ["HAM", "VER"]
    assert calls[1][1] == {"session_key": 9158}
    assert all("Authorization" not in (h or {}) for _, _, h in calls)


def test_404_means_no_rows(monkeypatch):
    monkeypatch.setattr(of1.requests, "get", lambda *a, **k: _Response(404))
    assert of1.get_team_radio(2024, "Race", pd.Timestamp("2024-01-01")).empty


def test_429_is_retried_then_succeeds(monkeypatch):
    answers = iter([_Response(429, headers={"Retry-After": "0"}), _Response(payload=[])])
    monkeypatch.setattr(of1.requests, "get", lambda *a, **k: next(answers))
    monkeypatch.setattr(of1.time, "sleep", lambda s: None)
    assert of1._get("sessions", {}) == []


def test_long_retry_after_fails_fast(monkeypatch):
    monkeypatch.setattr(
        of1.requests, "get", lambda *a, **k: _Response(429, headers={"Retry-After": "3600"})
    )
    with pytest.raises(ConnectionError, match="rate limit"):
        of1._get("sessions", {})


def test_network_failure_is_a_connection_error(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(of1.requests, "get", boom)
    with pytest.raises(ConnectionError):
        of1._get("sessions", {})
