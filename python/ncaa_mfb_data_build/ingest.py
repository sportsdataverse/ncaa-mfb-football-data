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
- A 404 and a FAILED fetch are different things. A 404 is "not on the raw repo
  (yet)" -- routine in season -- and is counted and skipped. Anything else
  (retries spent on 429/5xx, timeout, reset, a corrupt body) raises
  :class:`FetchError` and fails the season BEFORE anything is published: a count
  threshold cannot tell the two apart, and a short season published green is
  never repaired by a later push.
- ``downloader=`` is injectable for offline tests: return bytes, ``None`` for
  absent, or raise for a failure.

The payload cache is trusted once valid. CI starts empty every run; a persistent
local cache must be cleared after a raw ``03 --force`` re-parse.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import zlib
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


class FetchError(RuntimeError):
    """A raw file could not be fetched for a reason other than 404."""


def is_url(root: "str | Path") -> bool:
    return isinstance(root, str) and root.startswith(("http://", "https://"))


def _default_downloader(url: str) -> "bytes | None":
    """GET -> bytes; ``None`` on 404; :class:`FetchError` on any other failure."""
    from sportsdataverse.dl_utils import download
    from sportsdataverse.errors import NoDataError

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    try:
        resp = download(
            url=url,
            timeout=int(os.environ.get("SDV_PY_HTTP_TIMEOUT", "30")),
            num_retries=int(os.environ.get("SDV_PY_HTTP_RETRIES", "3")),
            # authenticated requests get a far higher raw.githubusercontent budget
            headers={"Authorization": f"token {token}"} if token else None,
        )
    except NoDataError:
        return None
    except Exception as exc:  # noqa: BLE001 -- re-raised as the one failure type
        raise FetchError(f"{url}: {exc}") from exc
    if getattr(resp, "status_code", None) != 200:
        raise FetchError(f"{url}: HTTP {getattr(resp, 'status_code', '?')}")
    return resp.content


def _write_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(body)
    os.replace(tmp, path)


def _valid_payload(body: bytes) -> bool:
    try:
        json.loads(gzip.decompress(body))
        return True
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, EOFError, zlib.error):
        return False


def _fetch_payload(url: str, dest: Path, get: Downloader) -> str:
    """One parsed payload into ``dest``: 'cached' | 'fetched' | 'missing' | 'failed'."""
    if dest.is_file() and _valid_payload(dest.read_bytes()):
        return "cached"
    dest.unlink(missing_ok=True)  # never leave a corrupt entry behind
    try:
        body = get(url)
    except FetchError as exc:
        log.error("fetch failed: %s", exc)
        return "failed"
    if body is None:
        return "missing"
    if not _valid_payload(body):
        log.error("corrupt payload served: %s", url)
        return "failed"
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
    A 404 reference file is simply absent from the mirror, so the build fails
    loudly for that dataset exactly as it would against a checkout. A FAILED fetch
    of any file raises :class:`FetchError` (payloads after every game is tried).
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
    counts = {"cached": 0, "fetched": 0, "missing": 0, "failed": 0}
    for cid in ids:
        rel = f"json/{cid}.json.gz"
        counts[_fetch_payload(f"{base_url}/mfb/{rel}", root / "mfb" / rel, get)] += 1
    log.info("mirror %s (ay %s): %d contests %s -> %s", season, ay, len(ids), counts, root)
    if counts["failed"]:
        raise FetchError(
            f"season {season}: {counts['failed']} payload fetch(es) failed -- refusing to "
            "build a short season"
        )
    return root
