"""Mirror one season of the raw repo over HTTPS -- the CI input path.

A build on CI never clones ``ncaa-mfb-football-raw`` (2.4 GB and growing). Instead
:func:`mirror_season` fetches exactly the files one season's build reads into a
local tree with the raw repo's own layout, and the builders run against that tree
unchanged. Shape copied from ``cfb_data_ingest/fetch.py::fetch_final`` /
nfl-ngs-data ``ingest.py``:

- URLs are BUILT from the raw repo's schedule parquet (``contest_id``) -- a remote
  directory is never listed.
- Reference parquet (schedule / rosters / teams) is fetched fresh every run: it
  moves daily in season. Parsed game payloads go through a read-through cache
  with a corrupt-entry guard (gunzip + ``json.loads`` before trusting it, refetch
  once), so a half-written file from an interrupted run cannot poison a rebuild.
- Fail-soft per game with counters; ``downloader=`` is injectable for offline tests.
"""

from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path
from typing import Callable, Optional

import polars as pl

from ncaa_mfb_data_build._logging import get_logger
from ncaa_mfb_data_build.config import CACHE_ENV, DEFAULT_CACHE

log = get_logger()

Downloader = Callable[[str], Optional[bytes]]

#: Divisions the raw repo captures (11 = FBS, 12 = FCS). Spelled out because the
#: local build globs ``{ay}_div*.parquet``, which HTTPS cannot.
DIVISIONS = (11, 12)


def is_url(root: "str | Path") -> bool:
    return isinstance(root, str) and root.startswith(("http://", "https://"))


def _default_downloader(url: str) -> "bytes | None":
    """GET -> bytes; ``None`` on 404 or any failure. Retries bounded for CI."""
    from sportsdataverse.dl_utils import download

    try:
        resp = download(
            url=url,
            timeout=int(os.environ.get("SDV_PY_HTTP_TIMEOUT", "30")),
            num_retries=int(os.environ.get("SDV_PY_HTTP_RETRIES", "3")),
        )
    except Exception:  # noqa: BLE001 -- one bad file cannot abort the season
        return None
    if getattr(resp, "status_code", None) != 200:
        return None
    return getattr(resp, "content", None) or None


def _write_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(body)
    os.replace(tmp, path)


def _valid_payload(body: bytes) -> bool:
    try:
        json.loads(gzip.decompress(body))
        return True
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, EOFError):
        return False


def _fetch_payload(url: str, dest: Path, get: Downloader) -> str:
    """One parsed payload into ``dest``: 'cached' | 'fetched' | 'missing'."""
    if dest.is_file() and _valid_payload(dest.read_bytes()):
        return "cached"
    body = get(url)
    if body is None or not _valid_payload(body):
        dest.unlink(missing_ok=True)  # never leave a corrupt entry behind
        return "missing"
    _write_atomic(dest, body)
    return "fetched"


def mirror_season(
    base_url: str,
    season: int,
    cache: "Path | None" = None,
    *,
    downloader: "Downloader | None" = None,
) -> Path:
    """Fetch season ``season``'s build inputs under ``cache``; return it as a raw root.

    ``season`` is the STARTING year; the raw tree is keyed ``ay = season + 1``.
    A missing reference file is simply absent from the mirror, so the build fails
    loudly for that dataset exactly as it would against a checkout.
    """
    get = downloader or _default_downloader
    root = Path(cache or os.environ.get(CACHE_ENV) or DEFAULT_CACHE)
    base_url = base_url.rstrip("/")
    ay = season + 1

    refs = [
        f"schedules/parquet/{ay}.parquet",
        f"rosters/parquet/{ay}.parquet",
        *(f"teams/parquet/{ay}_div{d}.parquet" for d in DIVISIONS),
    ]
    for rel in refs:
        dest = root / "mfb" / rel
        body = get(f"{base_url}/mfb/{rel}")
        if body is None:
            dest.unlink(missing_ok=True)  # a stale copy must not stand in for today's
            log.warning("mirror %s: %s not on the raw repo", season, rel)
            continue
        _write_atomic(dest, body)

    schedule = root / "mfb" / "schedules" / "parquet" / f"{ay}.parquet"
    if not schedule.is_file():
        log.error("mirror %s: no schedule parquet -- nothing to enumerate games from", season)
        return root
    ids = (
        pl.read_parquet(io.BytesIO(schedule.read_bytes()), columns=["contest_id"])
        .get_column("contest_id")
        .drop_nulls()
        .unique()
        .sort()
        .to_list()
    )
    counts = {"cached": 0, "fetched": 0, "missing": 0}
    for cid in ids:
        rel = f"json/{cid}.json.gz"
        counts[_fetch_payload(f"{base_url}/mfb/{rel}", root / "mfb" / rel, get)] += 1
    log.info("mirror %s (ay %s): %d contests %s -> %s", season, ay, len(ids), counts, root)
    return root
