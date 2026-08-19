"""CLI -- ``ncaa_mfb_data_build build --dataset {ds|all} --season YYYY``.

Scaffold stage: re-key the raw repo's per-season parquet onto the release
layout. Publishing (release assets on sportsdataverse/sportsdataverse-data) is
deliberately NOT here yet -- port ``publish.py`` from ncaa-wbb-hoops-data when
the first release is cut.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import polars as pl

from ncaa_mfb_data_build.config import REGISTRY, DatasetSpec, raw_root

_CAT_RE = re.compile(r"player_stats_(.+)\.parquet$")


def build_dataset(spec: DatasetSpec, season: int, base: Path, raw: Path) -> pl.DataFrame:
    """Read the raw files for ``(spec, season)``, stamp ``season``, write the release parquet."""
    files = sorted((raw / "mfb").glob(spec.raw_glob.format(season=season)))
    if not files:
        raise FileNotFoundError(f"{spec.name} {season}: no {spec.raw_glob!r} under {raw / 'mfb'}")
    frames = []
    for f in files:
        df = pl.read_parquet(f)
        if spec.name == "player_stats":
            df = df.with_columns(pl.lit(_CAT_RE.search(f.name).group(1)).alias("category"))
        frames.append(df)
    df = pl.concat(frames, how="diagonal_relaxed")
    if "season" not in df.columns:
        df = df.with_columns(pl.lit(season, dtype=pl.Int64).alias("season"))
    out = base / "mfb" / spec.name / "parquet" / f"{spec.tag}_{season}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)
    print(f"{spec.name} {season}: {df.height} rows -> {out}", flush=True)
    return df


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="raw per-season parquet -> mfb/{dataset}/parquet/")
    b.add_argument("--dataset", default="all", choices=["all", *REGISTRY])
    b.add_argument("--season", type=int, required=True, help="ENDING year: 2026 = fall-2025")
    b.add_argument("--base", default=str(Path(__file__).resolve().parents[2]), help="this repo's root")
    b.add_argument("--raw-root", default=None, help=f"override ${'NCAA_MFB_RAW_ROOT'} / ../ncaa-mfb-football-raw")
    args = ap.parse_args(argv)

    raw = Path(args.raw_root) if args.raw_root else raw_root()
    names = list(REGISTRY) if args.dataset == "all" else [args.dataset]
    for name in names:
        build_dataset(REGISTRY[name], args.season, Path(args.base), raw)
    return 0
