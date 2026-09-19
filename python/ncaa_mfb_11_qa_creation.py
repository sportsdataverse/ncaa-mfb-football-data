"""Numbered stage shim: build the ``qa`` dataset for one season.

Report-only data-integrity gate -- one row per game from
``sportsdataverse.validation.validate_game`` over the season's built
``pbp_cfbfastr``, plus a ``ncaa_mfb_qa_{season}_summary.json`` sidecar with the
season aggregate and the pre-publish drift findings. LAST in the registry
order: it reads the parquet stage 05 wrote. Nothing here fails a build
(``ncaa_mfb_data_build.qa.BLOCKING`` is ``False``).

    uv run python python/ncaa_mfb_11_qa_creation.py --season 2025
"""

import sys

from ncaa_mfb_data_build.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["build", "--dataset", "qa", *sys.argv[1:]]))
