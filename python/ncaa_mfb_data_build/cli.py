"""CLI -- ``ncaa_mfb_data_build build --dataset {ds|all} --season YYYY [--publish|--dry-run]``.

Build re-keys the raw repo's per-season parquet onto the release layout
(``mfb/{dataset}/parquet/ncaa_mfb_{dataset}_{season}.parquet`` via
``io.write_dataset``, which also stages the csv.gz release asset and upserts
the manifest). ``--publish`` uploads parquet + csv.gz + rds to the
``ncaa_mfb_{dataset}`` release on sportsdataverse/sportsdataverse-data
(``publish.py``, ported from ncaa-wbb-hoops-data).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import polars as pl

from ncaa_mfb_data_build._logging import get_logger
from ncaa_mfb_data_build.config import REGISTRY, DatasetSpec, raw_root
from ncaa_mfb_data_build.io import write_dataset

log = get_logger()

_CAT_RE = re.compile(r"player_stats_(.+)\.parquet$")

#: Datasets built from parsed payloads (builders.py); the rest re-key raw parquet.
GAME_GRAIN = {
    "pbp",
    "pbp_cfbfastr",
    "drives",
    "linescore",
    "team_stats",
    "player_stats",
    "officials",
}


def build_dataset(
    spec: DatasetSpec,
    season: int,
    base: Path,
    raw: Path,
    *,
    release: bool = False,
) -> pl.DataFrame:
    """Read the raw files for ``(spec, season)``, stamp ``season``, write via ``io``.

    ``season`` is the STARTING year (football convention); the raw tree is keyed
    by the ENDING academic year, so raw paths substitute ``ay = season + 1``.

    Game-grain datasets build from the raw repo's parsed+enriched payloads
    (``mfb/json/``, stage 03) via :mod:`ncaa_mfb_data_build.builders` -- the
    ``-data``-owns-reshaping standard. Reference datasets (teams / schedule /
    rosters) still re-key the raw tree's parquet, which the raw repo owns.
    """
    if spec.name in GAME_GRAIN:
        from ncaa_mfb_data_build import builders

        if spec.name == "player_stats":
            df = builders.build_player_stats(season, raw)
        elif spec.name == "pbp_cfbfastr":
            df = builders.build_pbp_cfbfastr(season, raw)
        else:
            df = builders.build_game_dataset(spec.name, season, raw)
        if not df.height:
            raise FileNotFoundError(
                f"{spec.name} {season}: no parsed payloads under {raw / 'mfb' / 'json'}"
            )
    else:
        ay = season + 1
        files = sorted((raw / "mfb").glob(spec.raw_glob.format(season=ay)))
        if not files:
            raise FileNotFoundError(
                f"{spec.name} {season} (ay {ay}): no {spec.raw_glob!r} under {raw / 'mfb'}"
            )
        frames = []
        for f in files:
            fdf = pl.read_parquet(f)
            if spec.name == "player_stats":
                fdf = fdf.with_columns(pl.lit(_CAT_RE.search(f.name).group(1)).alias("category"))
            frames.append(fdf)
        df = pl.concat(frames, how="diagonal_relaxed")
    # ALWAYS stamp -- never trust an upstream `season` to agree with the asset
    # name (an asset NAMED _2026 whose rows said 2025 once made sdv-db's
    # season-key check silently ingest 0 rows). With the starting-year standard
    # this matches cfbfastR's own convention, but the stamp stays unconditional
    # so name and column can never drift again.
    df = df.with_columns(pl.lit(season, dtype=pl.Int64).alias("season"))
    write_dataset(df, spec, season, base=base, release=release)
    return df


def _build(args: argparse.Namespace) -> int:
    raw = Path(args.raw_root) if args.raw_root else raw_root()
    base = Path(args.base)
    names = list(REGISTRY) if args.dataset == "all" else [args.dataset]
    for name in names:
        spec = REGISTRY[name]
        build_dataset(spec, args.season, base, raw, release=args.publish or args.dry_run)
        if args.publish or args.dry_run:
            from ncaa_mfb_data_build import publish

            publish.publish_dataset(spec, args.season, base=base, dry_run=args.dry_run)
    if args.dataset == "all":
        _write_qa(args.season, base)
    return 0


def _write_qa(season: int, base: Path) -> None:
    """Final-score QA frame -> committed ``mfb/qa/`` (small, never released)."""
    from ncaa_mfb_data_build import builders

    cf_p = base / "mfb" / "pbp_cfbfastr" / "parquet" / f"ncaa_mfb_pbp_cfbfastr_{season}.parquet"
    ls_p = base / "mfb" / "linescore" / "parquet" / f"ncaa_mfb_linescore_{season}.parquet"
    if not (cf_p.is_file() and ls_p.is_file()):
        return
    qa = builders.build_qa(season, pl.read_parquet(cf_p), pl.read_parquet(ls_p))
    out = base / "mfb" / "qa" / f"qa_pbp_vs_linescore_{season}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    qa.write_parquet(out)
    ok = (qa.get_column("final_score_match") == True).sum()  # noqa: E712
    unv = qa.get_column("final_score_match").null_count()
    log.info(
        "qa %s: %d/%d exact, %d unverifiable, %d flagged",
        season,
        ok,
        qa.height,
        unv,
        qa.height - ok - unv,
    )


def _check(args: argparse.Namespace) -> int:
    """Compare each dataset's LOCALLY BUILT seasons against what the release holds.

    Semantics ported from ncaa-wbb-hoops-data: only ``built - live`` is fatal;
    ``GhUnavailable`` exits 2 (could-not-look is not there-are-gaps).
    """
    from ncaa_mfb_data_build.publish import (
        DEFAULT_REPO,
        GhUnavailable,
        published_seasons,
    )

    datasets = list(REGISTRY) if args.dataset == "all" else [args.dataset]
    base = Path(args.base)
    missing_total = 0
    for name in datasets:
        spec = REGISTRY[name]
        built = {
            int(p.stem.rsplit("_", 1)[1])
            for p in (base / "mfb" / spec.name / "parquet").glob(f"{spec.tag}_*.parquet")
            if p.stem.rsplit("_", 1)[1].isdigit()
        }
        try:
            live = published_seasons(spec, repo=args.repo or DEFAULT_REPO)
        except GhUnavailable as exc:
            log.error("cannot audit %s: %s", name, exc)
            return 2
        if args.porcelain:
            for s in sorted(live):
                print(f"{name} {s}")
            continue
        missing = sorted(built - live)
        extra = sorted(live - built)
        missing_total += len(missing)
        status = "OK  " if not missing else "GAP "
        log.info(
            "%s %-15s built=%d published=%d%s%s",
            status,
            name,
            len(built),
            len(live),
            f" MISSING={missing}" if missing else "",
            f" PUBLISHED_ONLY={extra}" if extra else "",
        )
    if missing_total:
        log.error("%d built season(s) are NOT on their release", missing_total)
    return 1 if missing_total else 0


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="ncaa_mfb_data_build", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="raw per-season parquet -> mfb/{dataset}/parquet/ [+ publish]")
    b.add_argument("--dataset", default="all", choices=["all", *REGISTRY])
    b.add_argument(
        "--season", type=int, required=True, help="STARTING year: 2025 = fall-2025 (ay 2026)"
    )
    b.add_argument(
        "--base", default=str(Path(__file__).resolve().parents[2]), help="this repo's root"
    )
    b.add_argument(
        "--raw-root",
        default=None,
        help=f"override ${'NCAA_MFB_RAW_ROOT'} / ../ncaa-mfb-football-raw",
    )
    g = b.add_mutually_exclusive_group()
    g.add_argument("--publish", action="store_true", help="upload parquet+csv+rds to the release")
    g.add_argument(
        "--dry-run", action="store_true", help="stage release assets, log would-be uploads"
    )
    b.set_defaults(func=_build)

    c = sub.add_parser("check", help="audit built seasons against what each release actually holds")
    c.add_argument("--dataset", default="all", choices=["all", *REGISTRY])
    c.add_argument("--base", default=str(Path(__file__).resolve().parents[2]))
    c.add_argument("--repo", default=None)
    c.add_argument(
        "--porcelain",
        action="store_true",
        help="print '<dataset> <season>' per published unit (resume index)",
    )
    c.set_defaults(func=_check)

    args = ap.parse_args(argv)
    return args.func(args)
