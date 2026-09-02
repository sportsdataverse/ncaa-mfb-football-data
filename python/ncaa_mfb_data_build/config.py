"""Dataset registry -- one row per released NCAA MFB dataset.

Every dataset here is a *re-key* of parquet the sibling ``ncaa-mfb-football-raw``
already builds (its stage 05, ``mfb_datasets.py``): parsing lives there (via
sdv-py ``cfb_ncaa_pbp`` / ``cfb_ncaa_box``), this repo only maps the raw tree's
per-season files onto the release layout ``mfb/{dataset}/parquet/
ncaa_mfb_{dataset}_{season}.parquet`` and stamps ``season``.

``season`` is the **STARTING** year throughout -- the football convention
(cfbfastR / cfb / nfl): ``season = 2025`` is the fall-2025 season. The raw tree
is keyed by stats.ncaa.org's ENDING academic year (``mfb/datasets/{ay}/``), so
the build reads ``ay = season + 1`` and re-keys at this boundary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Sibling ncaa-mfb-football-raw checkout root (the ONLY input of this repo).
RAW_ROOT_ENV = "NCAA_MFB_RAW_ROOT"
DEFAULT_RAW_ROOT = Path(__file__).resolve().parents[3] / "ncaa-mfb-football-raw"

#: Release-tag prefix; also the parquet filename prefix so a downloaded asset
#: keeps its provenance instead of colliding with another league's pbp_2026.
TAG_PREFIX = "ncaa_mfb_"


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    #: glob under ``{raw_root}/mfb/`` for one season; ``{season}`` is substituted.
    #: Several matches (teams per division) are concatenated.
    raw_glob: str
    description: str

    @property
    def tag(self) -> str:
        return TAG_PREFIX + self.name


# Insertion order is the build order for ``--dataset all``: reference frames
# first, then per-game extracts. No dataset reads another dataset's output.
REGISTRY: dict[str, DatasetSpec] = {
    "teams": DatasetSpec(
        "teams",
        "teams/parquet/{season}_div*.parquet",
        "team ids/names per division (11=FBS, 12=FCS)",
    ),
    "schedule": DatasetSpec(
        "schedule", "schedules/parquet/{season}.parquet", "schedule master: one row per team-game"
    ),
    "rosters": DatasetSpec(
        "rosters",
        "rosters/parquet/{season}.parquet",
        "per-team season rosters with stats.ncaa.org player ids",
    ),
    "pbp": DatasetSpec("pbp", "datasets/{season}/pbp.parquet", "structural NCAA play-by-play"),
    "pbp_cfbfastr": DatasetSpec(
        "pbp_cfbfastr", "datasets/{season}/pbp_cfbfastr.parquet", "cfbfastR-named play frame"
    ),
    "team_stats": DatasetSpec(
        "team_stats", "datasets/{season}/team_stats.parquet", "per-period team box"
    ),
    "player_stats": DatasetSpec(
        "player_stats",
        "datasets/{season}/player_stats_*.parquet",
        "individual box, all categories (diagonal concat; `category` from filename)",
    ),
    "drives": DatasetSpec("drives", "datasets/{season}/drives.parquet", "drive chart"),
    "officials": DatasetSpec(
        "officials", "datasets/{season}/officials.parquet", "officiating crews"
    ),
    "linescore": DatasetSpec(
        "linescore", "datasets/{season}/linescore.parquet", "linescore + game info"
    ),
}


def raw_root() -> Path:
    return Path(os.environ.get(RAW_ROOT_ENV) or DEFAULT_RAW_ROOT)


# --- release sidecar metadata -------------------------------------------------
# Every published tag carries package_function.txt/.json -- the half of R's
# sportsdataverse_save() the Python publisher used to drop. These tags have no
# reader in any package yet (no hoopR/cfbfastR loader, and nothing in sdv-py's
# releases.yaml), so rather than invent a loader name that would 404 for a
# consumer, each names the producer stage that writes it -- the same convention
# the ncaa_*_rapm tags already carry on their published sidecars.
#
# When sdv-py grows load_ncaa_mfb_* loaders, swap these for the loader names.
#
# Keyed by tag. The publish tests assert every REGISTRY tag has an entry, so a
# new dataset cannot ship an unnamed tag.
PKG_FUNCTION: dict[str, str] = {
    TAG_PREFIX + "teams": "python/ncaa_mfb_01_teams_creation.py",
    TAG_PREFIX + "schedule": "python/ncaa_mfb_02_schedule_creation.py",
    TAG_PREFIX + "rosters": "python/ncaa_mfb_03_rosters_creation.py",
    TAG_PREFIX + "pbp": "python/ncaa_mfb_04_pbp_creation.py",
    TAG_PREFIX + "pbp_cfbfastr": "python/ncaa_mfb_05_pbp_cfbfastr_creation.py",
    TAG_PREFIX + "team_stats": "python/ncaa_mfb_06_team_stats_creation.py",
    TAG_PREFIX + "player_stats": "python/ncaa_mfb_07_player_stats_creation.py",
    TAG_PREFIX + "drives": "python/ncaa_mfb_08_drives_creation.py",
    TAG_PREFIX + "officials": "python/ncaa_mfb_09_officials_creation.py",
    TAG_PREFIX + "linescore": "python/ncaa_mfb_10_linescore_creation.py",
}
