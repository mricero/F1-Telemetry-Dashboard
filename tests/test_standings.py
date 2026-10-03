"""Standings parsing, round lookup and the live points projection (FEAT-06)."""

import pandas as pd
import pytest

from f1dash.processing.standings import (
    parse_constructor_standings,
    parse_driver_standings,
    points_for,
    project_standings,
    round_for_event,
    unplaced_drivers,
)
from f1dash.ui.standings import live_order


def _driver(position, code, given, family, team_id, team, points, wins=0):
    return {
        "position": str(position),
        "positionText": str(position),
        "points": str(points),
        "wins": str(wins),
        "Driver": {
            "driverId": family.lower(),
            "code": code,
            "givenName": given,
            "familyName": family,
        },
        "Constructors": [{"constructorId": team_id, "name": team}],
    }


def _constructor(position, team_id, team, points, wins=0):
    return {
        "position": str(position),
        "points": str(points),
        "wins": str(wins),
        "Constructor": {"constructorId": team_id, "name": team},
    }


# Jolpica's shape: MRData.StandingsTable.StandingsLists[0].
DRIVERS = {
    "MRData": {
        "StandingsTable": {
            "season": "2024",
            "round": "5",
            "StandingsLists": [
                {
                    "season": "2024",
                    "round": "5",
                    "DriverStandings": [
                        _driver(1, "VER", "Max", "Verstappen", "red_bull", "Red Bull", 110, 4),
                        _driver(2, "PER", "Sergio", "Perez", "red_bull", "Red Bull", 85),
                        _driver(3, "LEC", "Charles", "Leclerc", "ferrari", "Ferrari", 76),
                        _driver(4, "NOR", "Lando", "Norris", "mclaren", "McLaren", 70, 1),
                    ],
                }
            ],
        }
    }
}
CONSTRUCTORS = {
    "MRData": {
        "StandingsTable": {
            "StandingsLists": [
                {
                    "ConstructorStandings": [
                        _constructor(1, "red_bull", "Red Bull", 195, 4),
                        _constructor(2, "ferrari", "Ferrari", 100),
                        _constructor(3, "mclaren", "McLaren", 70, 1),
                    ]
                }
            ]
        }
    }
}


def test_points_scales_and_no_fastest_lap_point():
    assert [points_for(p) for p in (1, 2, 10, 11)] == [25, 18, 1, 0]
    assert [points_for(p, sprint=True) for p in (1, 8, 9)] == [8, 1, 0]


def test_parse_driver_standings():
    frame = parse_driver_standings(DRIVERS)
    assert list(frame["Code"]) == ["VER", "PER", "LEC", "NOR"]
    assert frame.iloc[0].to_dict() == {
        "Position": 1,
        "Code": "VER",
        "Driver": "Max Verstappen",
        "Team": "Red Bull",
        "ConstructorId": "red_bull",
        "Points": 110,
        "Wins": 4,
    }


def test_empty_payloads_give_empty_frames_with_columns():
    empty = {"MRData": {"StandingsTable": {"StandingsLists": []}}}
    assert parse_driver_standings(empty).empty
    assert "Points" in parse_constructor_standings(empty).columns
    assert parse_driver_standings(None).empty


def test_projection_moves_points_and_places():
    drivers = parse_driver_standings(DRIVERS)
    teams = parse_constructor_standings(CONSTRUCTORS)
    # Norris wins, Leclerc 2nd, Verstappen 3rd, Perez retired (not in order).
    new_drivers, new_teams = project_standings(drivers, teams, ["NOR", "LEC", "VER"])

    by_code = new_drivers.set_index("Code")
    assert by_code.loc["NOR", "Projected"] == 95
    assert by_code.loc["LEC", "Projected"] == 94
    assert by_code.loc["VER", "Projected"] == 125
    assert by_code.loc["PER", "Gain"] == 0
    assert list(new_drivers["Code"]) == ["VER", "NOR", "LEC", "PER"]
    assert by_code.loc["NOR", "Change"] == 2  # 4th -> 2nd
    assert by_code.loc["PER", "Change"] == -2  # 2nd -> 4th

    by_team = new_teams.set_index("Team")
    assert by_team.loc["Red Bull", "Gain"] == 15  # Verstappen only
    assert by_team.loc["Ferrari", "Projected"] == 118
    assert by_team.loc["McLaren", "Projected"] == 95
    assert list(new_teams["Team"]) == ["Red Bull", "Ferrari", "McLaren"]


def test_sprint_projection_uses_the_sprint_scale():
    drivers = parse_driver_standings(DRIVERS)
    teams = parse_constructor_standings(CONSTRUCTORS)
    new_drivers, _ = project_standings(drivers, teams, ["LEC"], sprint=True)
    assert new_drivers.set_index("Code").loc["LEC", "Gain"] == 8


def test_projection_ignores_codes_without_a_standings_row():
    drivers = parse_driver_standings(DRIVERS)
    teams = parse_constructor_standings(CONSTRUCTORS)
    new_drivers, new_teams = project_standings(drivers, teams, ["NEW", "VER"])
    assert new_drivers.set_index("Code").loc["VER", "Gain"] == 18
    assert new_teams.set_index("Team").loc["Red Bull", "Gain"] == 18


SCHEDULE = pd.DataFrame(
    {
        "round": [1, 2, 3],
        "race_name": ["Bahrain Grand Prix", "Saudi Arabian Grand Prix", "Australian Grand Prix"],
        "date": pd.to_datetime(["2024-03-02", "2024-03-09", "2024-03-24"], utc=True),
    }
)


@pytest.mark.parametrize(
    "gp, date, expected",
    [
        ("Anything", "2024-03-01 12:30:00", 1),  # Friday of round 1, by date
        ("Anything", pd.Timestamp("2024-03-23 05:00"), 3),  # naive Timestamp
        ("Saudi Arabian Grand Prix", None, 2),  # live: name only
        ("FORMULA 1 SAUDI ARABIAN GRAND PRIX 2024", None, 2),
        ("Atlantis Grand Prix", None, None),
        (None, None, None),
    ],
)
def test_round_for_event(gp, date, expected):
    assert round_for_event(SCHEDULE, gp, date) == expected


def test_round_for_event_without_a_schedule():
    assert round_for_event(pd.DataFrame(), "Bahrain Grand Prix") is None
    assert round_for_event(None, "Bahrain Grand Prix") is None


def test_live_order_skips_stopped_cars():
    table = pd.DataFrame(
        {
            "Driver": ["LEC", "VER", "NOR"],
            "Position": [2, 1, 3],
            "Status": ["ON TRACK", "ON TRACK", "OUT"],
        }
    )
    assert live_order({"standings": table}) == ["VER", "LEC"]
    assert live_order({}) == []


ENTRANTS = pd.DataFrame(
    {
        "name_acronym": ["BEA", "COL", "ANT"],
        "full_name": ["Oliver Bearman", "Franco Colapinto", "Kimi Antonelli"],
        "team_name": ["Red Bull Racing", "Alpine", "Mercedes"],
    }
)


def test_a_driver_missing_from_the_standings_starts_at_zero_in_a_known_team():
    drivers = parse_driver_standings(DRIVERS)
    teams = parse_constructor_standings(CONSTRUCTORS)
    # BEA replaces Perez at "Red Bull Racing" (Jolpica: "Red Bull") and wins.
    new_drivers, new_teams = project_standings(drivers, teams, ["BEA", "VER"], entrants=ENTRANTS)

    bea = new_drivers.set_index("Code").loc["BEA"]
    assert (bea["Points"], bea["Gain"], bea["Projected"]) == (0, 25, 25)
    assert bea["Driver"] == "Oliver Bearman"
    assert bea["ConstructorId"] == "red_bull"
    assert new_teams.set_index("Team").loc["Red Bull", "Gain"] == 43  # BEA 25 + VER 18
    assert len(new_teams) == 3  # no duplicate team
    assert unplaced_drivers(new_drivers) == []


def test_a_team_missing_from_the_standings_is_added_at_zero():
    drivers = parse_driver_standings(DRIVERS)
    teams = parse_constructor_standings(CONSTRUCTORS)
    new_drivers, new_teams = project_standings(drivers, teams, ["COL"], entrants=ENTRANTS)

    alpine = new_teams.set_index("Team").loc["Alpine"]
    assert (alpine["Points"], alpine["Gain"], alpine["Projected"]) == (0, 25, 25)
    assert len(new_teams) == 4
    assert new_drivers.set_index("Code").loc["COL", "ConstructorId"] == alpine["ConstructorId"]
    assert unplaced_drivers(new_drivers) == []


def test_a_driver_with_no_known_team_stays_out_of_the_constructor_table():
    drivers = parse_driver_standings(DRIVERS)
    teams = parse_constructor_standings(CONSTRUCTORS)
    for entrants in (None, ENTRANTS[ENTRANTS["name_acronym"] != "ANT"]):
        new_drivers, new_teams = project_standings(drivers, teams, ["ANT"], entrants=entrants)
        ant = new_drivers.set_index("Code").loc["ANT"]
        assert (ant["Projected"], ant["ConstructorId"]) == (25, "")
        assert len(new_teams) == 3
        assert new_teams["Gain"].sum() == 0
        assert unplaced_drivers(new_drivers) == ["ANT"]


def test_missing_drivers_outside_the_points_are_not_added():
    drivers = parse_driver_standings(DRIVERS)
    teams = parse_constructor_standings(CONSTRUCTORS)
    order = ["VER", "PER", "LEC", "NOR"] + [f"X{i:02d}" for i in range(7)] + ["BEA"]
    new_drivers, _ = project_standings(drivers, teams, order, entrants=ENTRANTS)
    assert "BEA" not in set(new_drivers["Code"])
