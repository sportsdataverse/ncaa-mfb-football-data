#!/usr/bin/env bash
# Historical backfill: build + publish EVERY season of EVERY ncaa_mfb_* dataset.
#
# One-time (or re-runnable) driver behind the first full publish. Ordinary
# incremental work is `run_build.sh` / `run_publish.sh` for a single season --
# reach for this only when re-materialising the whole history.
#
#   bash scripts/run_historical_publish.sh                 # 2014..2026, all datasets
#   START=2015 END=2010 bash scripts/run_historical_publish.sh
#   DATASETS="pbp shots" bash scripts/run_historical_publish.sh
#   DRY_RUN=1 bash scripts/run_historical_publish.sh        # build + stage, no uploads
#
# Watch it live in another terminal:
#   tail -f logs/historical_publish_<timestamp>.log
#
# RESUMABLE: a (dataset, season) whose parquet already exists AND whose row
# count matches the committed manifest is skipped unless FORCE=1. Ctrl-C is
# safe -- the next run picks up where this one stopped.
#
# Knobs are env-only, so pace/scope can be retuned without editing this file.
set -uo pipefail          # NOT -e: one bad season must not kill the sweep

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

START="${START:-2026}"          # newest season (ending year)
END="${END:-2014}"              # oldest
DATASETS="${DATASETS:-}"        # empty = every dataset in config.REGISTRY order
FORCE="${FORCE:-0}"             # 1 = rebuild even when the parquet looks current
DRY_RUN="${DRY_RUN:-0}"         # 1 = no gh uploads (still builds + stages)

export PYTHONUNBUFFERED=1       # real-time log lines, no 4KB buffering lag
export PYTHONIOENCODING=utf-8   # cp1252 chokes on unicode in piped output
export NCAA_MFB_RAW_ROOT="${NCAA_MFB_RAW_ROOT:-$REPO_ROOT/../ncaa-mfb-football-raw}"

# rds needs an R install that HAS arrow. PATH's Rscript often is not one:
# on this box PATH resolves to R-4.5.3 (no arrow) while 4.6.1 has it, and the
# only symptom is a warning + a silently missing .rds asset. Pick explicitly.
if [ -z "${SDV_RSCRIPT:-}" ]; then
  for r in "C:/Program Files/R/R-4.6.1/bin/Rscript.exe" \
           "C:/Program Files/R/R-4.6.0/bin/Rscript.exe" \
           "C:/Program Files/R/R-4.3.1/bin/Rscript.exe"; do
    [ -f "$r" ] || continue
    if "$r" -e 'quit(status = !requireNamespace("arrow", quietly = TRUE))' 2>/dev/null; then
      export SDV_RSCRIPT="$r"; break
    fi
  done
fi
[ -n "${SDV_RSCRIPT:-}" ] \
  && echo "rds: using ${SDV_RSCRIPT}" \
  || echo "rds: WARNING no R install with arrow found -- .rds assets will be SKIPPED"

if [ "$DRY_RUN" != "1" ]; then
  GH_TOKEN="${GH_TOKEN:-${GITHUB_PAT:-${SDV_GH_TOKEN:-}}}"
  if [ -z "$GH_TOKEN" ]; then
    for renviron in "$HOME/.Renviron" "$HOME/Documents/.Renviron"; do
      [ -f "$renviron" ] || continue
      line="$(grep -E '^(GITHUB_PAT|SDV_GH_TOKEN)=' "$renviron" | head -n1)" || true
      [ -n "$line" ] || continue
      val="${line#*=}"; val="${val%$'\r'}"
      val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
      GH_TOKEN="$val"; break
    done
  fi
  [ -z "$GH_TOKEN" ] && { echo "no gh token (GH_TOKEN/GITHUB_PAT/SDV_GH_TOKEN/.Renviron)" >&2; exit 1; }
  export GH_TOKEN
fi

mkdir -p logs
TS="$(date +%Y%m%d_%H%M%S)"
LOG="logs/historical_publish_${TS}.log"
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

[ -z "$DATASETS" ] && DATASETS="$(uv run python -c 'from ncaa_mfb_data_build.config import REGISTRY; print(" ".join(REGISTRY))')"
MODE_FLAG="--publish"; [ "$DRY_RUN" = "1" ] && MODE_FLAG="--dry-run"

say "historical publish: seasons ${START}..${END}, mode=${MODE_FLAG}, force=${FORCE}"
say "datasets: ${DATASETS}"
say "raw root: ${NCAA_MFB_RAW_ROOT}"

ok=0; skip=0; fail=0; failed_list=""
sweep_start=$SECONDS

for (( season=START; season>=END; season-- )); do
  season_start=$SECONDS
  for ds in $DATASETS; do
    pq="mfb/${ds}/parquet/ncaa_mfb_${ds}_${season}.parquet"
    # Presence is not validity: only skip when the manifest agrees the file is
    # current for this (dataset, season). A 0-byte or orphaned parquet rebuilds.
    if [ "$FORCE" != "1" ] && [ -s "$pq" ] && \
       awk -F, -v d="$ds" -v s="$season" 'NR>1 && $1==d && $2==s {found=1} END {exit !found}' \
           "mfb/${ds}/manifest.csv" 2>/dev/null; then
      skip=$((skip+1)); continue
    fi
    t0=$SECONDS
    if uv run python -m ncaa_mfb_data_build build \
         --dataset "$ds" --season "$season" $MODE_FLAG >>"$LOG" 2>&1; then
      say "  OK   ${ds} ${season}  ($((SECONDS-t0))s)"
      ok=$((ok+1))
    else
      say "  FAIL ${ds} ${season}  ($((SECONDS-t0))s) -- see ${LOG}"
      fail=$((fail+1)); failed_list="${failed_list} ${ds}/${season}"
    fi
  done
  say "season ${season} done in $((SECONDS-season_start))s (ok=${ok} skip=${skip} fail=${fail})"
done

say "SWEEP COMPLETE in $((SECONDS-sweep_start))s -- ok=${ok} skipped=${skip} failed=${fail}"
[ -n "$failed_list" ] && say "failed:${failed_list}"
# Exit RED if anything failed, but only AFTER every other unit had its turn --
# one bad dataset-season must not hide the 180 that worked.
echo "EXIT=$([ "$fail" -eq 0 ] && echo 0 || echo 1)" | tee -a "$LOG"
exit $([ "$fail" -eq 0 ] && echo 0 || echo 1)
