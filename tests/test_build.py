"""Hermetic build test: a synthetic raw tree in tmp_path -> release layout.

Reference datasets re-key raw parquet; game-grain datasets build from parsed
payloads (``mfb/json/``). ``pbp_cfbfastr`` is exercised against real fixtures
in sdv-py (the graduated mapper's own suite), not synthetically here -- a
payload realistic enough for the mapper would be a fixture, not a synth.
"""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from ncaa_mfb_data_build import builders
from ncaa_mfb_data_build.cli import main

SEASON = 2025  # start year; raw tree is keyed ay = 2026
AY = 2026


def _payload(cid: str) -> dict:
    return {
        "contest_id": cid,
        "academic_year": AY,
        "season": SEASON,
        "espn_game_id": "401" + cid,
        "teams": [],
        "pbp": [{"contest_id": cid, "play_number": 1, "play_text": "Kickoff"}],
        "drive_titles": [],
        "drives": [{"contest_id": cid, "drive_number": 1, "team": "A"}],
        "linescore": [
            {"contest_id": cid, "team": "A", "home_away": "home", "period": "1", "final": 21},
            {"contest_id": cid, "team": "B", "home_away": "away", "period": "1", "final": 14},
        ],
        "scoring_summary": [],
        "team_stats": [{"contest_id": cid, "category": "Rushing", "stat": "Rush Attempts"}],
        "officials": [{"contest_id": cid, "official": "R. Johnson"}],
        "player_stats": {
            "passing": [{"contest_id": cid, "name": "QB One", "pass_attempts": "3"}],
            "rushing": [{"contest_id": cid, "name": "RB One", "rush_yards": "7"}],
        },
    }


def _raw_tree(root: Path) -> Path:
    m = root / "mfb"
    (m / "teams/parquet").mkdir(parents=True)
    (m / "schedules/parquet").mkdir(parents=True)
    (m / "rosters/parquet").mkdir(parents=True)
    (m / f"raw/{AY}").mkdir(parents=True)
    (m / "json").mkdir(parents=True)
    for d in (11, 12):
        pl.DataFrame({"team_id": [str(d)], "division": [d]}).write_parquet(
            m / f"teams/parquet/{AY}_div{d}.parquet"
        )
    pl.DataFrame({"contest_id": ["1"]}).write_parquet(m / f"schedules/parquet/{AY}.parquet")
    pl.DataFrame({"player_id": ["9"]}).write_parquet(m / f"rosters/parquet/{AY}.parquet")
    for cid in ("1", "2"):
        (m / f"raw/{AY}/{cid}.json.gz").write_bytes(gzip.compress(b"{}"))
        with gzip.open(m / f"json/{cid}.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(_payload(cid), fh)
    return root


GAME_GRAIN_TESTED = ["pbp", "drives", "linescore", "team_stats", "player_stats", "officials"]


def test_build_rekeys_reference_and_builds_game_grain(tmp_path: Path) -> None:
    raw = _raw_tree(tmp_path / "raw")
    base = tmp_path / "data"
    for name in ["teams", "schedule", "rosters", *GAME_GRAIN_TESTED]:
        assert (
            main(
                [
                    "build",
                    "--dataset",
                    name,
                    "--season",
                    str(SEASON),
                    "--base",
                    str(base),
                    "--raw-root",
                    str(raw),
                ]
            )
            == 0
        )
        out = base / "mfb" / name / "parquet" / f"ncaa_mfb_{name}_{SEASON}.parquet"
        assert out.is_file(), name
        df = pl.read_parquet(out)
        assert df.get_column("season").to_list() == [SEASON] * df.height
        if name in GAME_GRAIN_TESTED:
            # payload enrichment survives the build
            assert set(df.get_column("espn_game_id").to_list()) == {"4011", "4012"}
    teams = pl.read_parquet(base / f"mfb/teams/parquet/ncaa_mfb_teams_{SEASON}.parquet")
    assert sorted(teams.get_column("division").to_list()) == [11, 12]
    ps = pl.read_parquet(base / f"mfb/player_stats/parquet/ncaa_mfb_player_stats_{SEASON}.parquet")
    assert sorted(set(ps.get_column("category").to_list())) == ["passing", "rushing"]
    assert {"pass_attempts", "rush_yards"} <= set(ps.columns)


def test_missing_season_fails_loudly(tmp_path: Path) -> None:
    raw = _raw_tree(tmp_path / "raw")
    with pytest.raises(FileNotFoundError):
        main(
            [
                "build",
                "--dataset",
                "pbp",
                "--season",
                "2024",
                "--base",
                str(tmp_path),
                "--raw-root",
                str(raw),
            ]
        )


# ESPN's own week for real games (cfbfastR-cfb-data schedules), at the LOCAL date
# stats.ncaa.org prints. The two NDSU games are FCS playoff games, which ESPN files
# as regular-season weeks 15 and 16.
ESPN_WEEKS = [
    (date(2024, 8, 24), 2024, 1),  # 401635525 Florida State v Georgia Tech, Dublin
    (date(2024, 9, 2), 2024, 1),  # 401634306 Boston College at Florida State, Labor Day
    (date(2024, 12, 7), 2024, 15),  # 401729745 Abilene Christian at North Dakota State
    (date(2024, 12, 14), 2024, 16),  # 401729785 Mercer at North Dakota State
    (date(2025, 8, 23), 2025, 1),  # 401756846 Iowa State v Kansas State
    (date(2025, 9, 6), 2025, 2),  # 401767224 North Dakota State at Tennessee State
    (date(2025, 11, 28), 2025, 14),  # 401752924 Indiana at Purdue (Friday night)
    (date(2025, 12, 13), 2025, 16),  # 401762521 Army v Navy
]


@pytest.mark.parametrize(("game_date", "season", "week"), ESPN_WEEKS)
def test_espn_week_matches_espn(game_date: date, season: int, week: int) -> None:
    assert builders.espn_week(game_date, season) == week


def test_pbp_cfbfastr_gets_the_week_of_its_schedule_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sportsdataverse.cfb as cfb

    raw = _raw_tree(tmp_path / "raw")
    pl.DataFrame(
        {"contest_id": ["1", "1", "2"], "date": ["09/01/2025", "09/01/2025", "11/28/2025"]}
    ).write_parquet(raw / f"mfb/schedules/parquet/{AY}.parquet")
    seen: dict[str, object] = {}

    def fake_to_cfbfastr(pbp: pl.DataFrame, **kw: object) -> pl.DataFrame:
        cid = pbp.get_column("contest_id")[0]
        seen[cid] = kw["week"]
        return pl.DataFrame({"game_id": [cid], "week": [kw["week"]]})

    monkeypatch.setattr(cfb, "to_cfbfastr", fake_to_cfbfastr)
    df = builders.build_pbp_cfbfastr(SEASON, raw)
    assert seen == {"1": 1, "2": 14}
    assert df.sort("game_id").get_column("week").to_list() == [1, 14]


def test_no_schedule_date_leaves_week_null(tmp_path: Path) -> None:
    raw = _raw_tree(tmp_path / "raw")  # its schedule master has no date column
    assert builders.season_weeks(raw, SEASON) == {}
