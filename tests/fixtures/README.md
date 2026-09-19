# tests/fixtures

`pbp_cfbfastr_3games_2025.parquet` — three real games (6386278, 6386297, 6386298)
cut verbatim from the published `ncaa_mfb_pbp_cfbfastr_2025` asset, 526 rows ×
105 columns. Used by `test_qa.py` so the validation gate and the drift gate run
against the real mapper output rather than a synthetic frame.

Refresh:

    uv run python -c "import polars as pl; \
      df = pl.read_parquet('mfb/pbp_cfbfastr/parquet/ncaa_mfb_pbp_cfbfastr_2025.parquet'); \
      ids = df['game_id'].unique(maintain_order=True).to_list()[:3]; \
      df.filter(pl.col('game_id').is_in(ids)).write_parquet('tests/fixtures/pbp_cfbfastr_3games_2025.parquet', compression='zstd')"
