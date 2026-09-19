"""Offline tests for the report-only QA stage (``ncaa_mfb_data_build.qa``).

Real inputs only: three real games cut from the published
``ncaa_mfb_pbp_cfbfastr_2025`` asset (``tests/fixtures/``). No network -- the
published comparison frame is passed in, never downloaded.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from ncaa_mfb_data_build import qa
from ncaa_mfb_data_build.cli import _qa_sidecar

FIX = Path(__file__).parent / "fixtures" / "pbp_cfbfastr_3games_2025.parquet"


def _pbp() -> pl.DataFrame:
    return pl.read_parquet(FIX)


def test_one_row_per_game_with_the_documented_schema():
    df = qa.season_qa_frame(_pbp(), processing_version="0.1.4+test")
    assert df.height == _pbp().get_column("game_id").n_unique() == 3
    assert list(df.columns) == list(qa.QA_SCHEMA)
    assert df.schema == dict(qa.QA_SCHEMA)
    row = df.row(0, named=True)
    assert row["league"] == "cfb" and row["source"] == "ncaa"
    assert row["processing_version"] == "0.1.4+test"
    assert row["n_rows"] > 0 and row["ok"] == (row["n_errors"] == 0)


def test_rows_agree_with_validate_game_on_the_ncaa_source():
    from sportsdataverse.validation import validate_game

    pbp = _pbp()
    df = qa.season_qa_frame(pbp, processing_version="x")
    for game in pbp.partition_by("game_id", maintain_order=True):
        gid = game.get_column("game_id")[0]
        report = validate_game(game, "cfb", source="ncaa")
        row = df.filter(pl.col("game_id") == gid).row(0, named=True)
        assert row["ok"] is report.ok
        assert row["n_errors"] == len(report.errors)
        assert row["failed_rule_ids"] == ",".join(sorted(f.rule_id for f in report.errors))


def test_only_the_columns_the_mapper_emits_are_judged():
    """The mapper frame supports a subset of the rule table; absent columns skip."""
    from sportsdataverse.validation.pbp_invariants import evaluate

    game = _pbp().partition_by("game_id", maintain_order=True)[0]
    fired = {r.rule for r in evaluate(game, league="cfb")}
    assert fired, "no rule evaluated at all -- the frame or the mapper changed"
    assert {"flags.rush_and_pass", "flags.no_play_yardage_credited"} <= fired
    # the ESPN-only families need columns the mapper does not produce
    assert not {r for r in fired if r.startswith(("ep.", "wp.", "box.", "timeouts."))}


def test_empty_season_keeps_the_documented_schema():
    empty = qa.season_qa_frame(pl.DataFrame(), processing_version="x")
    assert empty.height == 0 and empty.schema == dict(qa.QA_SCHEMA)


def test_season_summary_sidecar(tmp_path):
    df = qa.season_qa_frame(_pbp(), processing_version="0.1.4+test")
    (tmp_path / "mfb" / "qa" / "parquet").mkdir(parents=True)
    path = _qa_sidecar(df, 2025, tmp_path)
    s = json.loads(path.read_text())
    assert s["games"] == 3 and s["season"] == 2025
    assert s["league"] == "cfb" and s["source"] == "ncaa"
    assert s["max_error_share"] == qa.MAX_ERROR_SHARE
    assert s["blocking"] is False, "V2 ships report-only"
    assert s["threshold_exceeded"] is (s["error_share"] > qa.MAX_ERROR_SHARE)
    assert s["drift"] == []  # no pbp_cfbfastr parquet under tmp_path
    assert sum(s["counts_by_rule"].values()) >= s["games"] - s["games_error_free"]


def test_drift_gate_reports_schema_null_constant_and_mean_shift():
    new = _pbp()
    prev = new.drop("period").with_columns(
        pl.col("game_id").cast(pl.Utf8),
        (pl.col("yards_gained") * 0.5).alias("yards_gained"),
        pl.Series("varies", [str(i % 7) for i in range(new.height)], dtype=pl.Utf8),
    )
    new = new.with_columns(pl.lit("one", dtype=pl.Utf8).alias("varies"))
    found = qa.drift_findings(new, prev)
    by = {(f["check"], f["locator"].get("column")) for f in found}
    assert ("schema_contract", "period") in by
    assert ("schema_contract", "game_id") in by
    assert ("constant_column", "varies") in by
    assert ("rate_anomaly", "yards_gained") in by
    assert qa.drift_findings(new, None) == []


def test_published_frame_is_best_effort(tmp_path):
    assert qa.published_frame(str(tmp_path / "no-such-release.parquet")) is None
