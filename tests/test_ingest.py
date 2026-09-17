"""Offline tests for the HTTPS input path (``ingest.mirror_season``).

A fake downloader serves a synthetic raw tree by URL, so the build runs exactly
as CI runs it -- ``--raw-root https://...`` -- with no network and no checkout.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import polars as pl
import pytest
from test_build import AY, SEASON, _raw_tree

from ncaa_mfb_data_build import ingest
from ncaa_mfb_data_build.builders import season_contest_ids
from ncaa_mfb_data_build.cli import main

BASE = "https://raw.example.test/ncaa-mfb-football-raw/main"


def _served(raw: Path, calls: "list[str] | None" = None, fail: "tuple[str, ...]" = ()):
    def get(url: str) -> "bytes | None":
        if calls is not None:
            calls.append(url)
        if url.endswith(fail):
            raise ingest.FetchError(f"{url}: HTTP 503")
        f = raw / url.removeprefix(BASE + "/")
        return f.read_bytes() if f.is_file() else None

    return get


def _https_raw(tmp_path: Path) -> Path:
    raw = _raw_tree(tmp_path / "raw")
    # a real schedule master: several rows per contest, unplayed games null
    pl.DataFrame({"contest_id": ["1", "1", "2", "3", None]}).write_parquet(
        raw / f"mfb/schedules/parquet/{AY}.parquet"
    )
    return raw


def test_build_over_https_matches_the_checkout_build(tmp_path, monkeypatch) -> None:
    raw = _https_raw(tmp_path)
    monkeypatch.setattr(ingest, "_default_downloader", _served(raw))
    monkeypatch.setenv("NCAA_MFB_CACHE", str(tmp_path / "cache"))

    for name in ["teams", "schedule", "rosters", "pbp", "player_stats"]:
        rc = main(
            ["build", "--dataset", name, "--season", str(SEASON),
             "--base", str(tmp_path / "data"), "--raw-root", BASE]
        )  # fmt: skip
        assert rc == 0, name

    pbp = pl.read_parquet(tmp_path / f"data/mfb/pbp/parquet/ncaa_mfb_pbp_{SEASON}.parquet")
    # contest 3 is scheduled but has no parsed payload: skipped, not fatal
    assert set(pbp.get_column("espn_game_id").to_list()) == {"4011", "4012"}
    teams = pl.read_parquet(tmp_path / f"data/mfb/teams/parquet/ncaa_mfb_teams_{SEASON}.parquet")
    assert sorted(teams.get_column("division").to_list()) == [11, 12]


def test_mirror_never_lists_directories_and_skips_bundles(tmp_path) -> None:
    raw = _https_raw(tmp_path)
    calls: "list[str]" = []
    root = ingest.mirror_season(BASE, SEASON, tmp_path / "cache", downloader=_served(raw, calls))

    assert all(not u.endswith("/") and "/mfb/raw/" not in u for u in calls)
    assert season_contest_ids(root, SEASON) == ["1", "2", "3"]
    assert not (root / "mfb/json/3.json.gz").exists()


def test_corrupt_cache_entry_is_refetched(tmp_path) -> None:
    raw = _https_raw(tmp_path)
    cache = tmp_path / "cache"
    (cache / "mfb/json").mkdir(parents=True)
    (cache / "mfb/json/1.json.gz").write_bytes(gzip.compress(b'{"truncat'))

    ingest.mirror_season(BASE, SEASON, cache, downloader=_served(raw))

    assert (cache / "mfb/json/1.json.gz").read_bytes() == (raw / "mfb/json/1.json.gz").read_bytes()


def test_stale_reference_file_does_not_survive_a_miss(tmp_path) -> None:
    raw = _https_raw(tmp_path)
    cache = tmp_path / "cache"
    ingest.mirror_season(BASE, SEASON, cache, downloader=_served(raw))
    assert (cache / f"mfb/rosters/parquet/{AY}.parquet").is_file()

    (raw / f"mfb/rosters/parquet/{AY}.parquet").unlink()
    ingest.mirror_season(BASE, SEASON, cache, downloader=_served(raw))

    assert not (cache / f"mfb/rosters/parquet/{AY}.parquet").exists()


def test_failed_payload_fetch_fails_the_season_not_just_the_game(tmp_path, monkeypatch) -> None:
    raw = _https_raw(tmp_path)
    monkeypatch.setattr(ingest, "_default_downloader", _served(raw, fail=("json/2.json.gz",)))
    monkeypatch.setenv("NCAA_MFB_CACHE", str(tmp_path / "cache"))

    with pytest.raises(ingest.FetchError, match="1 payload fetch"):
        main(["build", "--dataset", "pbp", "--season", str(SEASON),
              "--base", str(tmp_path / "data"), "--raw-root", BASE])  # fmt: skip
    assert not (tmp_path / "data/mfb/pbp").exists()  # nothing built, nothing to publish


def test_failed_reference_fetch_is_fatal(tmp_path) -> None:
    raw = _https_raw(tmp_path)
    with pytest.raises(ingest.FetchError):
        ingest.mirror_season(
            BASE, SEASON, tmp_path / "cache", downloader=_served(raw, fail=(f"{AY}_div11.parquet",))
        )


def test_default_downloader_separates_404_from_failure(monkeypatch) -> None:
    import types

    from sportsdataverse import dl_utils
    from sportsdataverse.errors import NoDataError

    def fake(outcome):
        def download(**_):
            if isinstance(outcome, Exception):
                raise outcome
            return types.SimpleNamespace(status_code=outcome, content=b"x")

        return download

    monkeypatch.setattr(dl_utils, "download", fake(NoDataError("404")))
    assert ingest._default_downloader(BASE + "/mfb/json/1.json.gz") is None
    for outcome in (ConnectionError("reset"), 503):
        monkeypatch.setattr(dl_utils, "download", fake(outcome))
        with pytest.raises(ingest.FetchError):
            ingest._default_downloader(BASE + "/mfb/json/1.json.gz")


def test_plaintext_raw_root_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError, match="https"):
        ingest.mirror_season("http://raw.example.test/x/main", SEASON, tmp_path, downloader=None)


def test_a_failed_build_publishes_nothing(tmp_path, monkeypatch) -> None:
    from ncaa_mfb_data_build import publish

    raw = _https_raw(tmp_path)
    (raw / f"mfb/rosters/parquet/{AY}.parquet").unlink()  # 3rd dataset in build order fails
    uploads: "list[str]" = []
    monkeypatch.setattr(publish, "publish_dataset", lambda spec, *a, **k: uploads.append(spec.name))

    with pytest.raises(FileNotFoundError):
        main(["build", "--dataset", "all", "--season", str(SEASON), "--publish",
              "--base", str(tmp_path / "data"), "--raw-root", str(raw)])  # fmt: skip
    assert uploads == []  # teams + schedule built fine, yet nothing went up
