"""Records, ties, cut-offs and the tower vocabulary in ``processing.timing``.

CORE-02 (no silent downcasting), REPLAY-18 (deleted laps), REPLAY-22
(cut-offs from the entry list), REPLAY-26 (ties) and UI-13 (vocabulary).
"""

import warnings
from datetime import timedelta

import pandas as pd
import pytest

from processing.replay_model import snapshot_at, tower_series
from processing.timing import (
    _flag_is_set,
    _laps_completed,
    _valid_laps,
    build_timing_rows,
    fastest_lap_row,
)
from tests.replay_fixtures import practice_session
from tests.test_timing import _drivers, _lap_rows, _laps, _quali_results, _race_laps, _results


def _end_order(session: dict) -> list[str]:
    series = tower_series(session)
    end = session["session_info"]["replay_clock"]["end"]
    return list(snapshot_at(session, end, series)["standings"]["Driver"])


def _delete(session: dict, code: str, lap: int, reason: str | None = None) -> None:
    laps = session["laps"]
    laps["Deleted"] = laps["Deleted"].astype(object)
    mask = (laps["Driver"] == code) & (laps["LapNumber"] == float(lap))
    laps.loc[mask, "Deleted"] = True
    if reason is not None:
        laps["DeletedReason"] = ""
        laps.loc[mask, "DeletedReason"] = reason


class TestNoSilentDowncasting:
    """CORE-02: flag columns that are all-None object must not downcast."""

    @staticmethod
    def _laps() -> pd.DataFrame:
        laps = _laps(_lap_rows("VER", [90.0, 91.0], (30.0, 30.0, 30.0)))
        laps["Deleted"] = pd.Series([None, None], dtype=object)
        laps["IsAccurate"] = pd.Series([None, True], dtype=object)
        laps["Retired"] = pd.Series([None, False], dtype=object)
        laps["IsInProgress"] = pd.Series([None, True], dtype=object)
        return laps

    @pytest.mark.parametrize("no_silent_downcasting", [False, True])
    def test_flags_read_without_future_warnings(self, no_silent_downcasting):
        laps = self._laps()
        with (
            pd.option_context("future.no_silent_downcasting", no_silent_downcasting),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("error", FutureWarning)
            assert len(_valid_laps(laps)) == 2
            assert _flag_is_set(laps, "Retired") is False
            assert _laps_completed(laps) == 1
            rows = build_timing_rows({"laps": laps, "drivers": _drivers(), "is_live": True})

            assert rows[0]["laps_completed"] == 1


class TestDeletedLaps:
    """REPLAY-18: a deleted lap wins neither the tower nor the records."""

    def test_the_results_tower_and_the_replay_end_state_agree(self):
        session = practice_session()
        _delete(session, "E", 2, "TRACK LIMITS AT TURN 4 LAP 2 10:07:31")

        rows = build_timing_rows(session)

        assert [row["code"] for row in rows] == _end_order(session)
        e_row = next(row for row in rows if row["code"] == "E")
        assert e_row["position"] == 3
        assert e_row["best_lap"] == "1:32.500"
        assert not e_row["is_overall_best"]
        assert rows[0]["code"] == "A" and rows[0]["is_overall_best"]

    def test_a_deleted_lap_still_shows_as_last_with_its_reason(self):
        session = practice_session()
        _delete(session, "E", 2, "TRACK LIMITS AT TURN 4")

        e_row = next(row for row in build_timing_rows(session) if row["code"] == "E")

        assert e_row["last_lap"] == "1:30.500"
        assert e_row["last_deleted"] is True
        assert e_row["last_deleted_reason"] == "TRACK LIMITS AT TURN 4"
        assert e_row["last_is_session_best"] is False

    def test_without_a_reason_column_the_lap_is_still_marked(self):
        session = practice_session()
        _delete(session, "E", 2)

        e_row = next(row for row in build_timing_rows(session) if row["code"] == "E")

        assert e_row["last_deleted"] is True
        assert e_row["last_deleted_reason"] is None

    def test_a_valid_last_lap_is_not_marked(self):
        rows = build_timing_rows(practice_session())

        assert not any(row["last_deleted"] for row in rows)

    def test_fastest_lap_row_skips_deleted_laps(self):
        session = practice_session()
        _delete(session, "E", 2)

        lap = fastest_lap_row(session["laps"], "E")

        assert lap["LapNumber"] == 1.0

    def test_replay_standings_carry_the_deletion(self):
        session = practice_session()
        _delete(session, "E", 2, "TRACK LIMITS")
        series = tower_series(session)

        snapshot = snapshot_at(session, 590.0, series)
        rows = build_timing_rows(snapshot)
        e_row = next(row for row in rows if row["code"] == "E")

        assert e_row["last_lap"] == "1:30.500"
        assert e_row["last_deleted"] is True
        assert e_row["last_deleted_reason"] == "TRACK LIMITS"


class TestTiedLaps:
    """REPLAY-26: an equal lap set later ranks behind."""

    @staticmethod
    def _tied() -> dict:
        session = practice_session()
        laps = session["laps"]
        a_two = (laps["Driver"] == "A") & (laps["LapNumber"] == 2.0)
        laps.loc[a_two, "LapTime"] = pd.Timedelta(90.5, unit="s")
        laps.loc[a_two, "Time"] = pd.Timedelta(382.5, unit="s")
        return session

    def test_the_first_to_set_the_time_is_p1_in_both_views(self):
        session = self._tied()
        # E is the first row of neither frame: A sets 1:30.500 at 382.5 s,
        # E at 503 s. Put E first in the laps to defeat any row-order tie-break.
        laps = session["laps"]
        session["laps"] = pd.concat([laps[laps["Driver"] == "E"], laps[laps["Driver"] != "E"]])

        rows = build_timing_rows(session)

        assert rows[0]["code"] == "A" and rows[1]["code"] == "E"
        assert rows[0]["is_overall_best"] and not rows[1]["is_overall_best"]
        assert _end_order(session)[:2] == ["A", "E"]


class TestQualifyingEntries:
    """REPLAY-22: cut-offs count the entry list, not cars with laps."""

    @staticmethod
    def _session() -> dict:
        codes = [f"D{i:02d}" for i in range(1, 23)]
        entries, groups = [], []
        for index, code in enumerate(codes[:-1]):  # D22 never ran
            q1 = 90.0 + index * 0.1
            q2 = 89.0 + index * 0.1 if index < 16 else None
            q3 = 88.0 + index * 0.1 if index < 10 else None
            entries.append((code, q1, q2, q3))
            groups.append(_lap_rows(code, [q1], (30.0, 30.0, 30.0)))
        entries.append((codes[-1], None, None, None))
        return {
            "session_info": {"session_type": "Q"},
            "laps": _laps(*groups),
            "results": _quali_results(*entries),
            "drivers": _drivers(*[(c, "Team", "#3671c6") for c in codes]),
            "telemetry": {},
            "is_live": False,
        }

    def test_the_q1_heading_sits_above_p17(self):
        rows = build_timing_rows(self._session())

        headed = {row["partition"]: row["position"] for row in rows if row.get("partition")}
        assert headed["Eliminated in Q1"] == 17
        assert headed["Eliminated in Q2"] == 11
        p16 = next(row for row in rows if row["position"] == 16)
        assert p16["segment"] == "Q2"

    def test_a_q2_time_proves_the_segment_was_reached(self):
        session = self._session()
        # A results table with a wrong position still cannot demote a Q2 runner.
        session["results"].loc[session["results"]["Abbreviation"] == "D16", "Position"] = 18.0

        row = next(row for row in build_timing_rows(session) if row["code"] == "D16")

        assert row["segment"] == "Q2"


class TestTowerVocabulary:
    """UI-13: the model's words follow guideline 5.6."""

    def test_qualifying_rows_have_no_classified_chip(self):
        session = TestQualifyingEntries._session()
        session["results"]["Status"] = "Finished"

        statuses = {row["status"] for row in build_timing_rows(session)}

        assert statuses == {"ON TRACK"}

    def test_a_disqualified_qualifier_keeps_the_dsq_chip(self):
        session = TestQualifyingEntries._session()
        session["results"].loc[0, "Status"] = "Disqualified"

        rows = build_timing_rows(session)

        assert next(r for r in rows if r["code"] == "D01")["status"] == "DSQ"

    def test_a_lapped_finisher_is_fin_with_the_laps_in_the_gap(self):
        session = {
            "session_info": {"session_type": "R"},
            "laps": _laps(
                _race_laps("VER", [90.0, 90.0, 90.0]),
                # Same lap count in the frame; only the official status knows.
                _race_laps("ALO", [95.0, 95.0, 95.0]),
            ),
            "results": _results(("VER", 1, "Finished", 270.0), ("ALO", 2, "+1 Lap", None)),
            "drivers": _drivers(("VER", "T", "#3671c6"), ("ALO", "T", "#229971")),
            "telemetry": {},
            "is_live": False,
        }

        rows = build_timing_rows(session)

        assert rows[1]["status"] == "FIN"
        assert rows[1]["gap"] == "+1 LAP"
        assert not any(row["status"][-1:] == "L" for row in rows)

    def test_live_pit_speed_is_missing(self):
        laps = _laps(_lap_rows("VER", [90.0], (30.0, 30.0, 30.0)))
        laps["InPit"] = True
        laps["PitInTime"] = timedelta(minutes=1)

        rows = build_timing_rows({"laps": laps, "drivers": _drivers(), "is_live": True})

        assert rows[0]["status"] == "IN PIT"
        assert rows[0]["speed_kmh"] is None
