"""Dataset IO -- parquet writer (committed) + csv.gz release staging + manifest.

Format policy: the tree commits **parquet only**, under
``mfb/{dataset}/parquet/{stem}_{season}.parquet``. Release assets (csv.gz here,
``.rds`` via ``rds.py``) are staged under the gitignored
``mfb/_release_build/`` and only produced when ``release=True`` -- they are
never committed. A tiny per-dataset ``manifest.csv`` (committed) tracks one
row per ``(dataset, season)``, upserted on every write.

The release csv is **gzipped** -- see ``write_dataset`` for why (a plain pbp
season csv measures 99.0% of GitHub's 2 GiB per-asset limit). The committed
manifest is an ordinary small csv and is NOT gzipped.
"""

from __future__ import annotations

import gzip
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from ncaa_mfb_data_build._logging import get_logger, human_size
from ncaa_mfb_data_build.config import DatasetSpec

_LEAGUE = "mfb"

#: Extension for the staged release csv. Gzipped: see ``write_dataset``.
#: ``publish.py`` builds its upload list from this, so the two cannot drift.
CSV_SUFFIX = ".csv.gz"

log = get_logger()

_MANIFEST_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset": pl.Utf8,
    "season": pl.Int64,
    "row_count": pl.Int64,
    "generated_at_utc": pl.Utf8,
}


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def manifest_path(spec: DatasetSpec, base: Path) -> Path:
    return base / _LEAGUE / spec.name / "manifest.csv"


def _upsert_manifest(
    spec: DatasetSpec, season: int, row_count: int, base: Path
) -> Path:
    """Upsert one ``(dataset, season)`` row into the dataset's manifest, keep latest.

    Unlike an append log, this keeps exactly one row per season so idempotent
    rebuilds don't bloat the file: read existing (if present), drop any row
    for this ``(dataset, season)``, append the new row, sort by season.
    """
    f = manifest_path(spec, base)
    f.parent.mkdir(parents=True, exist_ok=True)
    row = pl.DataFrame(
        {
            "dataset": [spec.name],
            "season": [int(season)],
            "row_count": [int(row_count)],
            "generated_at_utc": [_utc_now_str()],
        },
        schema=_MANIFEST_SCHEMA,
    )
    if f.exists():
        existing = pl.read_csv(f, schema=_MANIFEST_SCHEMA)
        existing = existing.filter(
            ~((pl.col("dataset") == spec.name) & (pl.col("season") == int(season)))
        )
        row = pl.concat([existing, row], how="vertical")
    row = row.sort("season")
    row.write_csv(f)
    return f


def write_dataset(
    df: pl.DataFrame,
    spec: DatasetSpec,
    season: int,
    *,
    base: str | Path = ".",
    release: bool = False,
) -> list[Path]:
    """Write the committed parquet (always) + staged release csv.gz (if requested).

    Always writes ``{base}/mfb/{dataset}/parquet/{stem}_{season}.parquet``.
    When ``release=True`` also writes a GZIPPED csv to the gitignored
    ``{base}/mfb/_release_build/{dataset}/{stem}_{season}.csv.gz``. Upserts the
    committed ``{base}/mfb/{dataset}/manifest.csv`` row for every write.
    Returns the parquet path, plus the csv.gz path when ``release=True``.

    **Why gzip, not plain csv:** one season of ``pbp`` is ~3.1M rows and writes
    a **2,126,337,961-byte** plain csv -- 99.0% of GitHub's 2 GiB
    (2,147,483,648-byte) per-release-asset hard limit, so a season a hair longer
    than 2025-26 would fail to upload at all. Gzipped it is **99,701,928 bytes**
    (~95 MiB), 21.3x smaller, and the cliff is gone. Byte counts, not rounded
    GB/MB, because the whole point is a hard limit measured in bytes. `espn_cfb_model_pbp` already
    ships `.csv.gz` on this same release repo, so the extension is not novel
    for consumers.
    """
    base = Path(base)
    pq_dir = base / _LEAGUE / spec.name / "parquet"
    pq_dir.mkdir(parents=True, exist_ok=True)
    pq = pq_dir / f"{spec.tag}_{season}.parquet"
    df.write_parquet(pq)
    out = [pq]

    if release:
        csv_dir = base / _LEAGUE / "_release_build" / spec.name
        csv_dir.mkdir(parents=True, exist_ok=True)
        csv = csv_dir / f"{spec.tag}_{season}{CSV_SUFFIX}"
        # mtime=0 so re-running a build produces a byte-identical asset rather
        # than one that differs only by embedded timestamp.
        with gzip.GzipFile(csv, "wb", compresslevel=6, mtime=0) as fh:
            df.write_csv(fh)
        out.append(csv)

    manifest = _upsert_manifest(spec, season, df.height, base)
    log.info(
        "wrote %s (%s), %d rows x %d cols%s; manifest %s upserted",
        pq,
        human_size(pq.stat().st_size),
        df.height,
        df.width,
        f" + {out[1].name} (release)" if release else "",
        manifest.name,
    )
    return out
