"""Numbered stage shim: build the ``teams`` dataset for one season.

Thin wrapper over ``ncaa_mfb_data_build.cli`` (the number is dataset
identity + intended build order, mirroring the WBB/MBB -data convention;
no dataset reads another dataset's output).

    uv run python python/ncaa_mfb_01_teams_creation.py --season 2025
"""

import sys

from ncaa_mfb_data_build.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["build", "--dataset", "teams", *sys.argv[1:]]))
