#!/usr/bin/env bash
# Build one or all NCAA MFB release datasets for a season, OFFLINE against the
# sibling ncaa-mfb-football-raw checkout (or NCAA_MFB_RAW_ROOT if set).
#
# Usage:
#   SEASON=2025 bash scripts/run_build.sh                 # all datasets (2025 = fall-2025, STARTING year)
#   SEASON=2025 DATASET=pbp_cfbfastr bash scripts/run_build.sh
#
# Watch live in another terminal:  tail -f logs/run_build_<timestamp>.log
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
export NCAA_MFB_RAW_ROOT="${NCAA_MFB_RAW_ROOT:-$REPO_ROOT/../ncaa-mfb-football-raw}"
export PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8

mkdir -p logs
LOG="logs/run_build_$(date +%Y%m%d_%H%M%S).log"
echo "log -> ${LOG}  (watch: tail -f ${LOG})"
uv run python -m ncaa_mfb_data_build build \
  --dataset "${DATASET:-all}" --season "${SEASON:?set SEASON}" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
echo "EXIT=${rc}" | tee -a "$LOG"
exit "$rc"
