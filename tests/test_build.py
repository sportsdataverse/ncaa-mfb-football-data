"""Hermetic build test: a synthetic raw tree in tmp_path -> release layout."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from ncaa_mfb_data_build.cli import main
from ncaa_mfb_data_build.config import REGISTRY


def _raw_tree(root: Path, season: int = 2026) -> Path:
    """``season`` here is the raw tree's key: the ENDING academic year (ay)."""
    m = root / "mfb"
    (m / "teams/parquet").mkdir(parents=True)
    (m / "schedules/parquet").mkdir(parents=True)
    (m / "rosters/parquet").mkdir(parents=True)
    (m / f"datasets/{season}").mkdir(parents=True)
    for d in (11, 12):
        pl.DataFrame({"team_id": [str(d)], "division": [d]}).write_parquet(
            m / f"teams/parquet/{season}_div{d}.parquet"
        )
    pl.DataFrame({"contest_id": ["1"]}).write_parquet(m / f"schedules/parquet/{season}.parquet")
    pl.DataFrame({"player_id": ["9"]}).write_parquet(m / f"rosters/parquet/{season}.parquet")
    for name in ("pbp", "team_stats", "drives", "officials", "linescore"):
        pl.DataFrame({"contest_id": ["1"]}).write_parquet(m / f"datasets/{season}/{name}.parquet")
    pl.DataFrame({"game_id": ["1"], "season": [season]}).write_parquet(
        m / f"datasets/{season}/pbp_cfbfastr.parquet"
    )
    pl.DataFrame({"contest_id": ["1"], "pass_attempts": [3]}).write_parquet(
        m / f"datasets/{season}/player_stats_passing.parquet"
    )
    pl.DataFrame({"contest_id": ["1"], "rush_yards": [7]}).write_parquet(
        m / f"datasets/{season}/player_stats_rushing.parquet"
    )
    return root


def test_build_all_rekeys_every_dataset(tmp_path: Path) -> None:
    # raw tree keyed ay 2026 (fall 2025); the released season is the START year 2025
    raw = _raw_tree(tmp_path / "raw", season=2026)
    base = tmp_path / "data"
    assert main(["build", "--season", "2025", "--base", str(base), "--raw-root", str(raw)]) == 0
    for name, spec in REGISTRY.items():
        out = base / "mfb" / name / "parquet" / f"ncaa_mfb_{name}_2025.parquet"
        assert out.is_file(), name
        df = pl.read_parquet(out)
        assert df.get_column("season").to_list() == [2025] * df.height
    teams = pl.read_parquet(base / "mfb/teams/parquet/ncaa_mfb_teams_2025.parquet")
    assert sorted(teams.get_column("division").to_list()) == [11, 12]
    ps = pl.read_parquet(base / "mfb/player_stats/parquet/ncaa_mfb_player_stats_2025.parquet")
    assert sorted(ps.get_column("category").to_list()) == ["passing", "rushing"]
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
