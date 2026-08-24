"""Game-grain dataset builders -- parsed payloads -> season frames.

This is the reshape stage proper (the WBB/MBB `-data` standard): every
game-grain dataset is built from the raw repo's **parsed + enriched**
``mfb/json/{contest_id}.json.gz`` payloads (stage 03), never from raw HTML.
Reference datasets (teams / schedule / rosters) still re-key the raw tree's
parquet via ``cli.build_dataset``'s glob path -- the raw repo owns reference
discovery; this module owns everything derived from game pages.

Frames are reconstructed against the sdv-py parser schemas (the payloads were
produced by those parsers, so the columns agree by construction); json's
stringified dates/ids are cast back per schema. ``pbp_cfbfastr`` is built
per game with the graduated :func:`sportsdataverse.cfb.to_cfbfastr`, fed the
payload's own frames (``drive_titles`` included since the stage-03 re-parse).
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Iterator

import polars as pl

from ncaa_mfb_data_build._logging import get_logger

log = get_logger()

#: Game-grain datasets this module builds (release name -> payload key).
PAYLOAD_DATASETS = {
    "pbp": "pbp",
    "drives": "drives",
    "linescore": "linescore",
    "team_stats": "team_stats",
    "officials": "officials",
}


def parsed_path(raw: Path, contest_id: str) -> Path:
    return raw / "mfb" / "json" / f"{contest_id}.json.gz"


def season_contest_ids(raw: Path, season: int) -> "list[str]":
    """Captured contests for a season -- the raw bundle tree is ground truth."""
    ay_dir = raw / "mfb" / "raw" / str(season + 1)
    if not ay_dir.is_dir():
        return []
    return sorted(p.name.removesuffix(".json.gz") for p in ay_dir.glob("*.json.gz"))


def iter_payloads(raw: Path, season: int) -> "Iterator[dict[str, Any]]":
    """Parsed payloads for a season; a missing/corrupt one logs and skips."""
    for cid in season_contest_ids(raw, season):
        p = parsed_path(raw, cid)
        if not p.is_file():
            log.warning("no parsed payload for contest %s (run raw stage 03)", cid)
            continue
        try:
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                yield json.load(fh)
        except Exception as exc:  # noqa: BLE001 -- one bad payload must not sink the season
            log.warning("unreadable payload %s: %s", p.name, exc)


def _frame(rows: "list[dict]", schema: "dict[str, pl.DataType]") -> pl.DataFrame:
    """Rows (json round-tripped) -> frame with the parser's schema and order."""
    if not rows:
        return pl.DataFrame(schema=schema)
    df = pl.DataFrame(rows, infer_schema_length=None)
    casts = {k: v for k, v in schema.items() if k in df.columns}
    return df.cast(casts).select([k for k in schema if k in df.columns])


def _stamp(df: pl.DataFrame, payload: "dict[str, Any]") -> pl.DataFrame:
    """espn_game_id from the payload's enrichment block (null when unmatched)."""
    return df.with_columns(pl.lit(payload.get("espn_game_id"), dtype=pl.Utf8).alias("espn_game_id"))


def build_game_dataset(name: str, season: int, raw: Path) -> pl.DataFrame:
    """One game-grain dataset for a season, concatenated across payloads."""
    from sportsdataverse.cfb.cfb_ncaa_box import (
        DRIVES_SCHEMA,
        LINESCORE_SCHEMA,
        OFFICIALS_SCHEMA,
        TEAM_STATS_SCHEMA,
    )
    from sportsdataverse.cfb.cfb_ncaa_pbp import PBP_SCHEMA

    schemas = {
        "pbp": PBP_SCHEMA,
        "drives": DRIVES_SCHEMA,
        "linescore": LINESCORE_SCHEMA,
        "team_stats": TEAM_STATS_SCHEMA,
        "officials": OFFICIALS_SCHEMA,
    }
    schema = schemas[name]
    frames = []
    for payload in iter_payloads(raw, season):
        df = _frame(payload.get(PAYLOAD_DATASETS[name]) or [], schema)
        if df.height:
            frames.append(_stamp(df, payload))
    if not frames:
        return pl.DataFrame(schema={**schema, "espn_game_id": pl.Utf8})
    return pl.concat(frames, how="diagonal_relaxed")


def build_player_stats(season: int, raw: Path) -> pl.DataFrame:
    """All player-stat categories, diagonal-concatenated with ``category``."""
    frames = []
    for payload in iter_payloads(raw, season):
        for cat, rows in (payload.get("player_stats") or {}).items():
            if not rows:
                continue
            df = pl.DataFrame(rows, infer_schema_length=None).with_columns(
                pl.lit(cat, dtype=pl.Utf8).alias("category")
            )
            frames.append(_stamp(df, payload))
    if not frames:
        return pl.DataFrame(
            schema={"contest_id": pl.Utf8, "category": pl.Utf8, "espn_game_id": pl.Utf8}
        )
    return pl.concat(frames, how="diagonal_relaxed")


def build_pbp_cfbfastr(season: int, raw: Path) -> pl.DataFrame:
    """cfbfastR-named play frame, per game via the graduated mapper."""
    from sportsdataverse.cfb import to_cfbfastr
    from sportsdataverse.cfb.cfb_ncaa_box import (
        DRIVES_SCHEMA,
        LINESCORE_SCHEMA,
        SCORING_SUMMARY_SCHEMA,
    )
    from sportsdataverse.cfb.cfb_ncaa_pbp import DRIVE_TITLES_SCHEMA, PBP_SCHEMA

    frames = []
    for payload in iter_payloads(raw, season):
        pbp = _frame(payload.get("pbp") or [], PBP_SCHEMA)
        if not pbp.height:
            continue
        df = to_cfbfastr(
            pbp,
            season=payload.get("season"),
            drives=_frame(payload.get("drives") or [], DRIVES_SCHEMA),
            linescore=_frame(payload.get("linescore") or [], LINESCORE_SCHEMA),
            drive_titles=_frame(payload.get("drive_titles") or [], DRIVE_TITLES_SCHEMA),
            ot_drives=_frame(payload.get("drives") or [], DRIVES_SCHEMA),
            scoring_summary=_frame(payload.get("scoring_summary") or [], SCORING_SUMMARY_SCHEMA),
        )
        if df.height:
            frames.append(_stamp(df, payload))
    if not frames:
        return pl.DataFrame(schema={"game_id": pl.Utf8, "espn_game_id": pl.Utf8})
    return pl.concat(frames, how="diagonal_relaxed")


def build_qa(season: int, pbp_cfbfastr: pl.DataFrame, linescore: pl.DataFrame) -> pl.DataFrame:
    """Final-score QA: computed pbp final vs official linescore final.

    Ported from the raw repo's stage 05 (the build moved here with the game
    datasets). ``final_score_match`` is name-keyed; ``scores_match`` is the
    name-blind check that separates name-variant/attribution artifacts from
    real score gaps. ``ot_game`` marks the known stats.ncaa.org OT-omission gap.
    """
    from sportsdataverse.cfb.cfb_ncaa_cfbfastr import _norm_team

    out_schema = {
        "game_id": pl.Int64,
        "computed_final": pl.Utf8,
        "official_final": pl.Utf8,
        "final_score_match": pl.Boolean,
        "scores_match": pl.Boolean,
        "ot_game": pl.Boolean,
    }
    if not pbp_cfbfastr.height or not linescore.height:
        return pl.DataFrame(schema=out_schema)
    last = (
        pbp_cfbfastr.group_by("game_id", maintain_order=True)
        .last()
        .select("game_id", "pos_team", "pos_team_score", "def_pos_team", "def_pos_team_score")
    )
    official = linescore.group_by("contest_id", "team").agg(pl.col("final").max())
    ot_games = set(
        linescore.filter(pl.col("period").str.contains("OT")).get_column("contest_id").to_list()
    )
    rows = []
    for r in last.to_dicts():
        o = {
            _norm_team(x["team"]): x["final"]
            for x in official.filter(pl.col("contest_id") == str(r["game_id"])).to_dicts()
            if x["team"]
        }
        comp = {r["pos_team"]: r["pos_team_score"], r["def_pos_team"]: r["def_pos_team_score"]}
        match = all(o.get(_norm_team(t)) == s for t, s in comp.items()) if o else None
        rows.append(
            {
                "game_id": r["game_id"],
                "computed_final": ", ".join(f"{t} {s}" for t, s in comp.items()),
                "official_final": ", ".join(f"{t} {s}" for t, s in o.items()),
                "final_score_match": match,
                "scores_match": sorted(comp.values()) == sorted(o.values()) if o else None,
                "ot_game": str(r["game_id"]) in ot_games,
            }
        )
    return pl.DataFrame(rows, schema=out_schema)
