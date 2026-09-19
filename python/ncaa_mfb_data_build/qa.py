"""Report-only QA stage: per-game validation + pre-publish drift, published as ``ncaa_mfb_qa``.

Two gates, neither of which can fail a build in this revision
(:data:`BLOCKING` is ``False`` -- V2 ships report-only and ratchets):

* **per game** -- :func:`sportsdataverse.validation.validate_game` over each
  game's slice of ``pbp_cfbfastr``. The mapper output is cfbfastR-shaped, so
  the gate runs with ``league="cfb"`` and ``source="ncaa"``.

  **Most of the rule table evaluates, and every rule that does not is a
  declared skip.** The stats.ncaa.org mapper emits a 105-column cfbfastR frame,
  not an ESPN processor frame, so until sdv-py #556 only the flag family and
  the attribution INFO rules had their columns -- 13 of 89 -- and the rest
  skipped silently. #556 gave ``source="ncaa"`` a ``SOURCE_COLUMNS`` alias view
  (ytg, down, possession, flags, attribution, plays and the score walk derived
  as identities on the mapper's own columns) plus a ``NOT_APPLICABLE`` table
  that names, with a reason each, what the source cannot support. Today
  **42 rules evaluate and 55 are explicitly not applicable** -- 47 at #556, plus
  the eight V1b ``box.*`` rules scoped by #558 -- with nothing unaccounted for.
  Not applicable: the seven timeout rules (stats.ncaa.org carries no
  timeouts-remaining counters), the EP/WP/EPA rules (``to_cfbfastr`` runs no
  model on this path, by design), the box- and summary-backed rules (no
  advBoxScore and no ESPN summary), and the ESPN-only fields (type ids,
  ``statYardage``, ``downDistanceText``). A not-applicable rule is counted in
  the report's ``n_not_applicable`` and listed in ``not_applicable``; it is
  never faked into passing. The older final-score check
  (``mfb/qa/qa_pbp_vs_linescore_{season}.parquet``, committed and never
  released) is a different artefact and stays as it is.
* **per season, before publish** -- :func:`drift_findings` compares the finished
  season ``pbp_cfbfastr`` parquet against the **previously published** asset: column set and
  dtypes (``schema_contract``), null-rate rises (``null_rate``), columns that
  went constant (``constant_column``) and numeric mean shifts
  (``rate_anomaly``). The previously published asset is the contract on
  purpose: it is what the loader reads today, and sdv-py's declared loader
  schemas live under ``tools/``, which the wheel does not ship.

Both land in the season's ``ncaa_mfb_qa_{season}`` asset -- the per-game rows in
the parquet, the aggregate + drift findings in the ``_summary.json`` sidecar
published beside it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

log = logging.getLogger(__name__)

LEAGUE = "cfb"
SOURCE = "ncaa"

#: Share of a season's games allowed to carry an ``error`` finding. Seeded from
#: THIS repo's own measurement rather than the ESPN families': the whole 2025
#: season off ``ncaa_mfb_pbp_cfbfastr`` on sdv-py ``main`` @1686f904f is
#: 318/1,685 games error-free (18.9%), so 0.82. Lower it only with a ledger
#: entry as the open rules close.
#:
#: **Re-seeded 0.82 -> 0.85 from the full 2026 season.** The published
#: ``ncaa_mfb_qa_2026_summary.json`` (``0.1.4+c9215199``) reads 56/340 games
#: error-free -- ``error_share`` 0.8353, over 0.82, so ``threshold_exceeded``
#: was true on a report-only asset. Still one rule family, not noise:
#: ``flags.no_play_counted_as_attempt`` 334 games,
#: ``flags.no_play_yardage_credited`` 284, ``flags.int_without_pass`` 1. That
#: measurement predates #556, which makes 42 rules evaluate instead of 13, so
#: the next full season will read worse again before it reads better -- re-seed
#: on that measurement, do not pre-empt it here.
#: Ledger 2026-09-17 03:35 EDT, "V2 QA assets PUBLISHED", gotcha (3), and
#: 04:05 EDT, "NCAA-RULES -> PR #556".
MAX_ERROR_SHARE = 0.85

#: Report-only. The build logs the summary and publishes the asset; it never
#: fails on QA. Flipping this to ``True`` is the ratchet step, its own PR.
BLOCKING = False

#: One row per validated game. ``failed_rule_ids`` / ``warned_rule_ids`` are
#: comma-joined rather than ``List(Utf8)`` so the same frame serialises to
#: parquet, csv and rds unchanged across the three release families.
QA_SCHEMA: dict[str, pl.DataType] = {
    "game_id": pl.Int64,
    "league": pl.Utf8,
    "source": pl.Utf8,
    "season": pl.Int64,
    "processing_version": pl.Utf8,
    "n_rows": pl.Int64,
    "ok": pl.Boolean,
    "n_errors": pl.Int64,
    "n_warnings": pl.Int64,
    "failed_rule_ids": pl.Utf8,
    "warned_rule_ids": pl.Utf8,
    "built_at": pl.Utf8,
}


def processing_version() -> str:
    """``<sportsdataverse version>[+<git sha>]`` -- the library state that mapped the plays.

    This repo has no stamp of its own (it re-keys and maps; sdv-py's
    ``to_cfbfastr`` is the whole producer surface), and the lock pins sdv-py to
    a git commit, so the version string alone cannot tell two states apart.
    """
    from importlib import metadata

    try:
        version = metadata.version("sportsdataverse")
    except metadata.PackageNotFoundError:  # pragma: no cover -- editable oddities
        return "unknown"
    try:
        raw = metadata.distribution("sportsdataverse").read_text("direct_url.json") or "{}"
        sha = str(json.loads(raw).get("vcs_info", {}).get("commit_id", ""))[:8]
    except Exception:  # noqa: BLE001 -- PyPI install / missing file
        sha = ""
    return f"{version}+{sha}" if sha else version


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def qa_row(
    plays: pl.DataFrame,
    *,
    processing_version: str,
    summary: dict[str, Any] | None = None,
    box: dict[str, Any] | None = None,
    header: dict[str, Any] | None = None,
    source: str = SOURCE,
    league: str = LEAGUE,
) -> dict[str, Any]:
    """Validate one processed game and flatten the report into a QA row."""
    from sportsdataverse.validation import validate_game

    report = validate_game(plays, league, header=header, source=source, summary=summary, box=box)
    row = report.to_row()
    row["failed_rule_ids"] = ",".join(row["failed_rule_ids"])
    row["warned_rule_ids"] = ",".join(row["warned_rule_ids"])
    # the library reports the sdv-py version; the asset records the repo's full
    # stamp (`<sdv-py version>+<sha>.<SCHEMA_REV>`), which is what a season's
    # other parquet carry
    row["processing_version"] = processing_version
    row["built_at"] = _utc_now()
    return row


def season_qa_frame(pbp_cfbfastr: pl.DataFrame, *, processing_version: str) -> pl.DataFrame:
    """One QA row per ``game_id`` in a season's ``pbp_cfbfastr`` frame.

    Partitions rather than filters per id: one pass over the season instead of
    one full scan per game.
    """
    if pbp_cfbfastr.height == 0 or "game_id" not in pbp_cfbfastr.columns:
        return pl.DataFrame(schema=QA_SCHEMA)
    rows = [
        qa_row(g, processing_version=processing_version)
        for g in pbp_cfbfastr.partition_by("game_id", maintain_order=True)
    ]
    return pl.DataFrame([{k: r.get(k) for k in QA_SCHEMA} for r in rows], schema=QA_SCHEMA).sort(
        "game_id"
    )


def _finding(check: str, severity: str, message: str, **kw: Any) -> dict[str, Any]:
    from sportsdataverse.validation.findings import Finding, Severity

    return Finding(
        check, Severity(severity), LEAGUE, kw.pop("dataset", "pbp_cfbfastr"), message, **kw
    ).to_dict()


def drift_findings(new: pl.DataFrame, prev: pl.DataFrame | None) -> list[dict[str, Any]]:
    """Report-only drift of a finished season frame against the published one.

    ``prev is None`` (a first-published season, or an unreachable release) is
    not a finding: there is nothing to drift from. Returns the findings as
    dicts, in the harness ``Finding`` shape, for the summary sidecar.
    """
    if prev is None or new.height == 0:
        return []
    # The tolerances are sdv-py's packaged ``validation/thresholds.yaml``, read
    # through the constants #555 added so a caller needs no YAML parser. The
    # import is deferred, as every other sportsdataverse import in this module is.
    from sportsdataverse.validation.thresholds import MEAN_SHIFT_WARN, NULL_RATE_WARN

    null_warn, shift_warn = NULL_RATE_WARN, MEAN_SHIFT_WARN
    out: list[dict[str, Any]] = []
    new_s = {c: str(t) for c, t in new.schema.items()}
    prev_s = {c: str(t) for c, t in prev.schema.items()}

    for col in sorted(set(prev_s) - set(new_s)):
        out.append(
            _finding(
                "schema_contract",
                "error",
                f"column {col!r} dropped since the last release",
                locator={"column": col},
                expected=prev_s[col],
                actual=None,
            )
        )
    for col in sorted(set(new_s) - set(prev_s)):
        out.append(
            _finding(
                "schema_contract",
                "warn",
                f"column {col!r} is new since the last release",
                locator={"column": col},
                expected=None,
                actual=new_s[col],
            )
        )
    for col in sorted(set(new_s) & set(prev_s)):
        if new_s[col] != prev_s[col]:
            out.append(
                _finding(
                    "schema_contract",
                    "error",
                    f"dtype changed for {col!r}",
                    locator={"column": col},
                    expected=prev_s[col],
                    actual=new_s[col],
                )
            )

    shared = sorted(set(new_s) & set(prev_s))
    if shared:
        n_null = new.select([pl.col(c).null_count().alias(c) for c in shared]).row(0)
        p_null = prev.select([pl.col(c).null_count().alias(c) for c in shared]).row(0)
        for col, nn, pn in zip(shared, n_null, p_null):
            rate, prior = nn / new.height, pn / prev.height
            if rate > null_warn >= prior:
                out.append(
                    _finding(
                        "null_rate",
                        "warn",
                        f"{col!r} is {rate:.1%} null, was {prior:.1%}",
                        locator={"column": col},
                        expected=round(prior, 4),
                        actual=round(rate, 4),
                        metric=round(rate - prior, 4),
                    )
                )
        n_uniq = new.select([pl.col(c).n_unique().alias(c) for c in shared]).row(0)
        p_uniq = prev.select([pl.col(c).n_unique().alias(c) for c in shared]).row(0)
        for col, nu, pu in zip(shared, n_uniq, p_uniq):
            if nu <= 1 < pu:
                out.append(
                    _finding(
                        "constant_column",
                        "warn",
                        f"{col!r} went constant ({pu} distinct values last release)",
                        locator={"column": col},
                        expected=pu,
                        actual=nu,
                    )
                )

    numeric = [c for c in shared if new.schema[c].is_numeric() and prev.schema[c].is_numeric()]
    if numeric:
        n_mean = new.select([pl.col(c).mean().alias(c) for c in numeric]).row(0)
        p_mean = prev.select([pl.col(c).mean().alias(c) for c in numeric]).row(0)
        for col, nm, pm in zip(numeric, n_mean, p_mean):
            if nm is None or pm is None:
                continue
            denom = max(abs(pm), 1e-9)
            shift = abs(nm - pm) / denom
            if shift > shift_warn:
                out.append(
                    _finding(
                        "rate_anomaly",
                        "warn",
                        f"mean of {col!r} moved {shift:.1%} ({pm:.4g} -> {nm:.4g})",
                        locator={"column": col},
                        expected=pm,
                        actual=nm,
                        metric=round(shift, 4),
                    )
                )
    return out


def season_summary(
    qa: pl.DataFrame,
    season: int,
    *,
    processing_version: str,
    drift: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Games, error-free share, per-rule counts and the drift findings."""
    games = qa.height
    error_free = int(qa.get_column("ok").sum()) if games else 0
    share = error_free / games if games else None
    counts: dict[str, int] = {}
    for col in ("failed_rule_ids", "warned_rule_ids"):
        for cell in qa.get_column(col).drop_nulls().to_list() if games else []:
            for rule in filter(None, cell.split(",")):
                counts[rule] = counts.get(rule, 0) + 1
    error_share = 1 - share if share is not None else None
    return {
        "league": LEAGUE,
        "source": SOURCE,
        "season": int(season),
        "processing_version": processing_version,
        "built_at": _utc_now(),
        "games": games,
        "games_error_free": error_free,
        "error_free_share": round(share, 4) if share is not None else None,
        "error_share": round(error_share, 4) if error_share is not None else None,
        "max_error_share": MAX_ERROR_SHARE,
        "threshold_exceeded": (None if error_share is None else error_share > MAX_ERROR_SHARE),
        "blocking": BLOCKING,
        "counts_by_rule": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "drift": drift or [],
    }


#: Where a published season asset lives (the drift gate's reference).
RELEASE_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "{tag}/{stem}_{season}.parquet"
)


def published_url(tag: str, stem: str, season: int) -> str:
    return RELEASE_URL.format(tag=tag, stem=stem, season=int(season))


def summary_path(parquet_path: str | Path) -> Path:
    """The ``_summary.json`` sidecar beside a season's QA parquet."""
    p = Path(parquet_path)
    return p.with_name(f"{p.stem}_summary.json")


def published_frame(url: str, columns: list[str] | None = None) -> pl.DataFrame | None:
    """The previously published season asset, or ``None`` when it cannot be read.

    Best-effort by design: the drift gate is report-only, so a release that is
    not there yet (a first publish) or a network hiccup must not fail a build.
    """
    try:
        return pl.read_parquet(url, columns=columns)
    except Exception as exc:  # noqa: BLE001 -- no release yet / offline / transient
        log.info("drift gate: previous release unreadable (%s): %r", url, exc)
        return None


def write_summary(summary: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def log_summary(summary: dict[str, Any]) -> None:
    """The one line a build leaves behind (and the drift count)."""
    share = summary["error_free_share"]
    log.info(
        "qa %s %s: %d games, %d error-free (%s), threshold %.2f%s, blocking=%s, %d drift finding(s)",
        summary["league"],
        summary["season"],
        summary["games"],
        summary["games_error_free"],
        "n/a" if share is None else f"{share:.1%}",
        summary["max_error_share"],
        " EXCEEDED" if summary["threshold_exceeded"] else "",
        summary["blocking"],
        len(summary["drift"]),
    )
