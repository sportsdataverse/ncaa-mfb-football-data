# ncaa-mfb-football-data

Python producer for the **NCAA football (MFB)** release datasets built from
`stats.ncaa.org`. Mirrors [`ncaa-mbb-hoops-data`](https://github.com/sportsdataverse/ncaa-mbb-hoops-data)
/ [`ncaa-wbb-hoops-data`](https://github.com/sportsdataverse/ncaa-wbb-hoops-data).

Pipeline: `stats.ncaa.org -> ncaa-mfb-football-raw -> ncaa-mfb-football-data [HERE] -> sportsdataverse-data`

**Status: live.** The build stage re-keys the raw repo's parquet onto the
release layout; publishing (parquet + csv.gz + rds per season, ported from
`ncaa-wbb-hoops-data`) uploads to `sportsdataverse/sportsdataverse-data`.

## Input contract

This repo consumes the committed output of
[`ncaa-mfb-football-raw`](https://github.com/sportsdataverse/ncaa-mfb-football-raw)
(its stage 05, `mfb_datasets.py` — fully offline, parsing via sdv-py
`cfb_ncaa_pbp` / `cfb_ncaa_box`), read from the sibling checkout
`../ncaa-mfb-football-raw` or `NCAA_MFB_RAW_ROOT`:

| raw path (`{ay}` = ENDING academic year) | release dataset |
| --- | --- |
| `mfb/teams/parquet/{ay}_div{11,12}.parquet` | `teams` (concat; `division` 11=FBS, 12=FCS) |
| `mfb/schedules/parquet/{ay}.parquet` | `schedule` (one row per team-game, `contest_id`) |
| `mfb/rosters/parquet/{ay}.parquet` | `rosters` (stats.ncaa.org `player_id`) |
| `mfb/datasets/{ay}/pbp.parquet` | `pbp` (structural NCAA pbp) |
| `mfb/datasets/{ay}/pbp_cfbfastr.parquet` | `pbp_cfbfastr` (cfbfastR-named play frame) |
| `mfb/datasets/{ay}/team_stats.parquet` | `team_stats` |
| `mfb/datasets/{ay}/player_stats_{cat}.parquet` | `player_stats` (diagonal concat, `category` from filename) |
| `mfb/datasets/{ay}/drives.parquet` | `drives` |
| `mfb/datasets/{ay}/officials.parquet` | `officials` |
| `mfb/datasets/{ay}/linescore.parquet` | `linescore` |

`contest_id` / `team_id` / `player_id` are NCAA **string** ids and stay `Utf8`.
`mfb/datasets/{ay}/qa_pbp_vs_linescore.parquet` is a raw-side QA artifact and
is not released.

**Season convention: ENDING academic year.** `season = 2026` is the fall-2025
season (ay 2026), matching the raw tree's `academic_year` key and the
`mfb/datasets/{ay}/` directory. Never re-key to the start year.

## Output contract

- **parquet**: committed in-repo under `mfb/{dataset}/parquet/` as
  `ncaa_mfb_{dataset}_{season}.parquet`, every frame stamped with `season`
  (Int64, ending year). The `ncaa_mfb_` prefix matches the release tag.
- **parquet + csv.gz + rds**: release assets on
  `sportsdataverse/sportsdataverse-data`, tagged `ncaa_mfb_{dataset}`, staged
  under the gitignored `mfb/_release_build/` (`io.py` / `rds.py` / `publish.py`,
  ported from `ncaa-wbb-hoops-data` — per-file `gh release upload --clobber`,
  create-if-missing, `GhUnavailable` resume semantics).

## Run order

```bash
uv sync --frozen
SEASON=2026 bash scripts/run_build.sh                  # all datasets
SEASON=2026 DATASET=pbp_cfbfastr bash scripts/run_build.sh
# or directly:
uv run python -m ncaa_mfb_data_build build --dataset all --season 2026

# publish one season (build + stage csv.gz/rds + gh upload):
SEASON=2026 bash scripts/run_publish.sh
# full-history sweep (resumable; DRY_RUN=1 to stage without uploading):
bash scripts/run_historical_publish.sh                 # 2026 down to 2014
# audit built seasons vs what each release actually holds:
uv run python -m ncaa_mfb_data_build check
```

Offline; `NCAA_MFB_RAW_ROOT` defaults to `../ncaa-mfb-football-raw`. Backfill
= the same command with another `--season` (the raw repo must hold that
`mfb/datasets/{ay}/` first — see its RUNBOOK).

## Tests

```bash
uv run pytest -q
uv run ruff check python tests
```

Hermetic: `tests/test_build.py` fabricates a raw tree in `tmp_path`.
