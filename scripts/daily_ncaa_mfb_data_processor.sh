#!/usr/bin/env bash
# Rebuild + publish NCAA MFB season datasets from ncaa-mfb-football-raw.
#
# Usage: bash scripts/daily_ncaa_mfb_data_processor.sh -s 2026 [-e 2026] [--no-publish|--dry-run]
#   (-s/-e are STARTING years: 2026 = fall 2026 = raw academic year 2027)
#
# Entry point of .github/workflows/daily_ncaa_mfb_data.yml, which the raw repo's
# push dispatches. The raw repo is never cloned: NCAA_MFB_RAW_ROOT defaults to its
# raw.githubusercontent.com base and ingest.mirror_season fetches exactly one
# season's inputs (gitignored cache .ncaa_mfb_raw_cache/). Set
# NCAA_MFB_RAW_ROOT=/path/to/ncaa-mfb-football-raw to build from a checkout.
#
#   watch: tail -f logs/ncaa_mfb_data_<season>.log
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

START_YEAR=""; END_YEAR=""; PUBLISH="--publish"
while [ $# -gt 0 ]; do
  case "$1" in
    -s) START_YEAR="$2"; shift 2;;
    -e) END_YEAR="$2"; shift 2;;
    --no-publish) PUBLISH=""; shift;;
    --dry-run) PUBLISH="--dry-run"; shift;;
    *) echo "usage: $0 -s <start> [-e <end>] [--no-publish|--dry-run]" >&2; exit 2;;
  esac
done
END_YEAR=${END_YEAR:-$START_YEAR}
# `seq 2026 2025` and a malformed bound both yield an empty loop -- a green run
# that built nothing.
if ! [[ "$START_YEAR" =~ ^[0-9]{4}$ && "$END_YEAR" =~ ^[0-9]{4}$ ]] || [ "$START_YEAR" -gt "$END_YEAR" ]; then
  echo "usage: $0 -s <start> [-e <end>]  (4-digit years, start <= end; got '$START_YEAR'..'$END_YEAR')" >&2
  exit 2
fi

export NCAA_MFB_RAW_ROOT="${NCAA_MFB_RAW_ROOT:-https://raw.githubusercontent.com/sportsdataverse/ncaa-mfb-football-raw/main}"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

sdv_commit_push() {
  local msg="$1"; shift
  git add -- "$@" || { echo "::error ::git add failed for: $msg"; return 1; }
  if git diff --cached --quiet; then echo "nothing to commit for: $msg"; return 0; fi
  git commit -q -m "$msg" || { echo "::error ::commit failed: $msg"; return 1; }
  local attempt
  for attempt in 1 2 3; do
    if git push -q origin HEAD; then echo "pushed: $msg (attempt $attempt)"; return 0; fi
    echo "push rejected (attempt $attempt); syncing with origin"
    # Rebasing a feature branch (a hand dispatch from one) onto main can never
    # fast-forward; fail now instead of three times.
    [ "$(git branch --show-current)" = main ] || { echo "::error ::push rejected off main: $msg"; return 1; }
    git fetch --quiet origin main || true
    if ! git rebase --merge origin/main >/dev/null; then
      git rebase --abort >/dev/null 2>&1 || true
      echo "::error ::cannot rebase onto origin/main for: $msg"; return 1
    fi
  done
  echo "::error ::push still rejected after 3 attempts: $msg"; return 1
}

# shellcheck source=scripts/_venv.sh
. "$REPO/scripts/_venv.sh"
sdv_preflight ncaa_mfb_data_build polars sportsdataverse

mkdir -p logs
ANY_FAILED=0
for i in $(seq "$START_YEAR" "$END_YEAR"); do
  LOGFILE="logs/ncaa_mfb_data_${i}.log"
  {
    echo "=== season $i  $(date -u '+%F %T')Z  raw=$NCAA_MFB_RAW_ROOT ==="
    # shellcheck disable=SC2086
    PYTHONPATH=python "$SDV_PY" -m ncaa_mfb_data_build build --dataset all --season "$i" $PUBLISH
  } 2>&1 | tee -a "$LOGFILE"
  rc=${PIPESTATUS[0]}
  echo "season $i build EXIT=$rc" | tee -a "$LOGFILE"
  [ "$rc" = "0" ] || ANY_FAILED=1

  # Commit only a season that built AND published cleanly: a failed build uploads
  # nothing (cli builds every dataset before publishing any), and its partial
  # parquet must not reach the repo either. A failure during upload can leave the
  # release a step ahead of the repo until the next clean run re-commits.
  # Load-bearing subject -- downstream tooling parses the years out of it.
  if [ "$rc" = "0" ] && [ -n "$PUBLISH" ] && [ "$PUBLISH" != "--dry-run" ]; then
    sdv_commit_push "NCAA MFB Data Update (Start: $i End: $i)" mfb 2>&1 | tee -a "$LOGFILE"
    [ "${PIPESTATUS[0]}" = "0" ] || ANY_FAILED=1
  fi
done

echo "EXIT=$ANY_FAILED"
exit "$ANY_FAILED"
