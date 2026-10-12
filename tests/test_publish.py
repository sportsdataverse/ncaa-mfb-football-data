"""Tests for publish.py -- per-file ``gh release upload --clobber``.

Hermetic: no real ``gh`` or R calls. Files are pre-staged; ``runner``/
``exists_check`` are fakes that just record/answer.
"""

from __future__ import annotations

from pathlib import Path

from ncaa_mfb_data_build.config import REGISTRY
from ncaa_mfb_data_build.io import CSV_SUFFIX
from ncaa_mfb_data_build.publish import DEFAULT_REPO, publish_dataset

_SPEC = REGISTRY["pbp"]


#: release metadata sidecars -- asserted separately, not a data asset
SIDECARS = ("timestamp.", "package_function.")


def _stage(tmp_path: Path) -> None:
    pq_dir = tmp_path / "mfb" / "pbp" / "parquet"
    pq_dir.mkdir(parents=True)
    (pq_dir / "ncaa_mfb_pbp_2026.parquet").write_bytes(b"parquet-bytes")

    rel_dir = tmp_path / "mfb" / "_release_build" / "pbp"
    rel_dir.mkdir(parents=True)
    (rel_dir / f"ncaa_mfb_pbp_2026{CSV_SUFFIX}").write_bytes(b"csv-bytes")
    (rel_dir / "ncaa_mfb_pbp_2026.rds").write_bytes(b"rds-bytes")


def test_publish_creates_release_when_absent(tmp_path: Path):
    _stage(tmp_path)
    calls: list[list[str]] = []

    result = publish_dataset(
        _SPEC,
        2026,
        base=tmp_path,
        runner=lambda args: calls.append(args),
        exists_check=lambda t, r: False,
        make_rds=False,
    )

    creates = [c for c in calls if c[:2] == ["release", "create"]]
    uploads = [
        c
        for c in calls
        if c[:2] == ["release", "upload"] and not Path(c[3]).name.startswith(SIDECARS)
    ]
    assert len(creates) == 1
    assert creates[0][2] == "ncaa_mfb_pbp"
    assert "--repo" in creates[0] and DEFAULT_REPO in creates[0]

    assert len(uploads) == 3
    for c in uploads:
        assert c[2] == "ncaa_mfb_pbp"
        assert c[4:6] == ["--repo", DEFAULT_REPO]
        assert c[-1] == "--clobber"

    assert result["uploaded"] == 3
    assert result["tag"] == "ncaa_mfb_pbp"


def test_publish_skips_create_when_release_present(tmp_path: Path):
    _stage(tmp_path)
    calls: list[list[str]] = []

    publish_dataset(
        _SPEC,
        2026,
        base=tmp_path,
        runner=lambda args: calls.append(args),
        exists_check=lambda t, r: True,
        make_rds=False,
    )

    assert not any(c[:2] == ["release", "create"] for c in calls)
    data_uploads = [
        c
        for c in calls
        if c[:2] == ["release", "upload"] and not Path(c[3]).name.startswith(SIDECARS)
    ]
    assert len(data_uploads) == 3


def test_publish_dry_run_makes_no_calls(tmp_path: Path):
    _stage(tmp_path)
    calls: list[list[str]] = []

    result = publish_dataset(
        _SPEC,
        2026,
        base=tmp_path,
        dry_run=True,
        runner=lambda args: calls.append(args),
        exists_check=lambda t, r: False,
        make_rds=False,
    )

    assert calls == []
    assert result["uploaded"] == 0


def test_publish_only_parquet_staged_uploads_one_file(tmp_path: Path):
    pq_dir = tmp_path / "mfb" / "pbp" / "parquet"
    pq_dir.mkdir(parents=True)
    (pq_dir / "ncaa_mfb_pbp_2026.parquet").write_bytes(b"parquet-bytes")
    calls: list[list[str]] = []

    result = publish_dataset(
        _SPEC,
        2026,
        base=tmp_path,
        runner=lambda args: calls.append(args),
        exists_check=lambda t, r: True,
        make_rds=False,
    )

    uploads = [
        c
        for c in calls
        if c[:2] == ["release", "upload"] and not Path(c[3]).name.startswith(SIDECARS)
    ]
    assert len(uploads) == 1
    assert result["uploaded"] == 1


def _stage_parquet_only(tmp_path: Path) -> None:
    pq_dir = tmp_path / "mfb" / "pbp" / "parquet"
    pq_dir.mkdir(parents=True)
    (pq_dir / "ncaa_mfb_pbp_2026.parquet").write_bytes(b"parquet-bytes")


def test_publish_make_rds_stages_and_uploads_rds(tmp_path: Path, monkeypatch):
    """Only a parquet is pre-staged; a stubbed rds.to_rds should get called and
    its output picked up as a second uploaded asset."""
    _stage_parquet_only(tmp_path)

    def _fake_to_rds(parquet_path, rds_path, **kwargs):
        rds_path = Path(rds_path)
        rds_path.parent.mkdir(parents=True, exist_ok=True)
        rds_path.write_bytes(b"fake-rds-bytes")
        return rds_path

    monkeypatch.setattr("ncaa_mfb_data_build.rds.to_rds", _fake_to_rds)
    calls: list[list[str]] = []

    result = publish_dataset(
        _SPEC,
        2026,
        base=tmp_path,
        runner=lambda args: calls.append(args),
        exists_check=lambda t, r: True,
        make_rds=True,
    )

    rds_path = tmp_path / "mfb" / "_release_build" / "pbp" / "ncaa_mfb_pbp_2026.rds"
    assert rds_path.exists()
    uploads = [
        c
        for c in calls
        if c[:2] == ["release", "upload"] and not Path(c[3]).name.startswith(SIDECARS)
    ]
    assert len(uploads) == 2
    assert result["uploaded"] == 2


def test_publish_make_rds_failure_still_uploads_parquet(tmp_path: Path, monkeypatch):
    """rds.to_rds raising must not block the parquet(+csv) upload -- swallowed, not fatal."""
    _stage_parquet_only(tmp_path)

    def _raising_to_rds(parquet_path, rds_path, **kwargs):
        raise RuntimeError("Rscript not found")

    monkeypatch.setattr("ncaa_mfb_data_build.rds.to_rds", _raising_to_rds)
    calls: list[list[str]] = []

    result = publish_dataset(
        _SPEC,
        2026,
        base=tmp_path,
        runner=lambda args: calls.append(args),
        exists_check=lambda t, r: True,
        make_rds=True,
    )

    uploads = [
        c
        for c in calls
        if c[:2] == ["release", "upload"] and not Path(c[3]).name.startswith(SIDECARS)
    ]
    assert len(uploads) == 1
    assert result["uploaded"] == 1


# --- release-state resolution ------------------------------------------------
#
# The distinction under test: "this tag has nothing" vs "I could not look".
# Collapsing them makes `check --porcelain` emit an empty resume index and exit
# 0, so run_historical_publish.sh re-uploads an entire history while reporting a
# clean run. Absence must be proven by gh's `HTTP 404`, never inferred
# from an exit code -- gh also exits non-zero for auth, rate-limit, and bad-repo
# errors, and on 2026-08-18 a Windows STATUS_DLL_INIT_FAILED (exit 3221225794)
# was read as "tag missing" and killed a live unit on a redundant create.


class _Proc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_published_assets_parses_names(monkeypatch):
    from ncaa_mfb_data_build import publish as P

    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: _Proc(0, "a.parquet\tsha256:1\na.csv.gz\tsha256:2\n"))
    assert P.published_assets("t") == {"a.parquet", "a.csv.gz"}


def test_published_assets_empty_only_for_confirmed_missing(monkeypatch):
    from ncaa_mfb_data_build import publish as P

    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: _Proc(1, "", "gh: Not Found (HTTP 404)"))
    assert P.published_assets("t") == set()


def test_published_assets_raises_on_ambiguous_exit(monkeypatch):
    """A DLL-init / auth / rate-limit failure must NOT look like an empty tag."""
    import pytest

    from ncaa_mfb_data_build import publish as P

    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: _Proc(3221225794))
    with pytest.raises(P.GhUnavailable):
        P.published_assets("t")


def test_published_assets_raises_on_launch_failure_and_timeout(monkeypatch):
    """These arrive as EXCEPTIONS, not exit codes -- an rc-only check misses them."""
    import pytest

    from ncaa_mfb_data_build import publish as P

    def _boom(*a, **k):
        raise OSError("gh not found")

    monkeypatch.setattr(P.subprocess, "run", _boom)
    with pytest.raises(P.GhUnavailable):
        P.published_assets("t")

    def _hang(*a, **k):
        raise P.subprocess.TimeoutExpired("gh", 1)

    monkeypatch.setattr(P.subprocess, "run", _hang)
    with pytest.raises(P.GhUnavailable):
        P.published_assets("t")


def test_release_exists_assumes_present_when_gh_cannot_answer(monkeypatch):
    """Safe default: a bogus `release create` over a live tag fails the unit."""
    from ncaa_mfb_data_build import publish as P

    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: _Proc(3221225794))
    assert P._gh_release_exists("t", DEFAULT_REPO) is True

    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: _Proc(1, "", "gh: Not Found (HTTP 404)"))
    assert P._gh_release_exists("t", DEFAULT_REPO) is False


# --- gh timeouts -------------------------------------------------------------
#
# Metadata calls and uploads are different workloads. A single pbp season ships
# a ~50MB parquet, a ~68MB csv.gz and a ~59MB rds; on 2026-08-18 the shared
# 180s bound aborted `pbp 2013` mid-upload and discarded 279s of build work
# because the transfer was slow, not stuck. Widening the shared bound instead
# would have made it useless as a hang detector for release view/create.


def test_metadata_and_upload_timeouts_are_separate():
    from ncaa_mfb_data_build import publish as P

    assert P._GH_TIMEOUT == 180
    assert P._GH_UPLOAD_TIMEOUT > P._GH_TIMEOUT
    # Big enough for a ~68MB asset on a slow link.
    assert P._GH_UPLOAD_TIMEOUT >= 900


def test_uploads_use_the_long_timeout(tmp_path: Path, monkeypatch):
    """The upload shell-out must not inherit the short metadata bound."""
    from ncaa_mfb_data_build import publish as P

    _stage(tmp_path)
    seen: "list[int]" = []

    def _fake_run(argv, **kw):
        # the long bound is about a 100MB+ data asset on a slow link; the
        # ~50-byte release sidecars run on the short metadata bound and would
        # otherwise read as a violation of it
        if "upload" in argv and not any(Path(a).name.startswith(SIDECARS) for a in argv):
            seen.append(kw.get("timeout"))

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(P.subprocess, "run", _fake_run)
    P.publish_dataset(_SPEC, 2026, base=tmp_path, exists_check=lambda *_: True, make_rds=False)
    assert seen, "no upload was attempted"
    assert all(t == P._GH_UPLOAD_TIMEOUT for t in seen), seen


# --- digest skip ---------------------------------------------------------------
#
# The droplet shares ONE 5,000/h API quota. A nightly that rebuilds an unchanged
# season must cost one release listing, not ~3 requests per asset plus 4 sidecars.


def _sha(p: Path) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def _data_uploads(calls):
    return [
        Path(c[3]).name
        for c in calls
        if c[:2] == ["release", "upload"] and not Path(c[3]).name.startswith(SIDECARS)
    ]


def _staged(tmp_path: Path) -> dict[str, Path]:
    _stage(tmp_path)
    return {
        "parquet": tmp_path / "mfb" / "pbp" / "parquet" / "ncaa_mfb_pbp_2026.parquet",
        "csv": tmp_path / "mfb" / "_release_build" / "pbp" / f"ncaa_mfb_pbp_2026{CSV_SUFFIX}",
        "rds": tmp_path / "mfb" / "_release_build" / "pbp" / "ncaa_mfb_pbp_2026.rds",
    }


def test_unchanged_parquet_skips_every_format_and_the_stamp(tmp_path: Path):
    files = _staged(tmp_path)
    remote = {f.name: _sha(f) for f in files.values()}
    # rds + csv.gz re-stamp every build (write time, gzip mtime)
    remote[files["rds"].name] = remote[files["csv"].name] = "sha256:stale"
    calls: list[list[str]] = []
    result = publish_dataset(
        _SPEC, 2026, base=tmp_path, runner=calls.append, digests=lambda t, r: remote,
        make_rds=False,
    )
    assert calls == []
    assert result["uploaded"] == 0


def test_changed_parquet_uploads_changed_files_parquet_last(tmp_path: Path):
    files = _staged(tmp_path)
    remote = {f.name: _sha(f) for f in files.values()}
    remote[files["parquet"].name] = remote[files["rds"].name] = "sha256:old"
    calls: list[list[str]] = []
    publish_dataset(
        _SPEC, 2026, base=tmp_path, runner=calls.append, digests=lambda t, r: remote,
        make_rds=False,
    )
    # the csv matched so it stays; the parquet goes LAST, then the sidecars
    assert _data_uploads(calls) == [files["rds"].name, files["parquet"].name]
    assert any(Path(c[3]).name == "timestamp.json" for c in calls)


def test_missing_sibling_forces_upload_even_when_parquet_matches(tmp_path: Path):
    files = _staged(tmp_path)
    remote = {files["parquet"].name: _sha(files["parquet"])}
    calls: list[list[str]] = []
    publish_dataset(
        _SPEC, 2026, base=tmp_path, runner=calls.append, digests=lambda t, r: remote,
        make_rds=False,
    )
    up = _data_uploads(calls)
    assert set(up) == {f.name for f in files.values()}
    assert up[-1] == files["parquet"].name


def test_failed_sibling_holds_the_parquet_back(tmp_path: Path):
    import subprocess

    import pytest

    files = _staged(tmp_path)
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(argv)
        if argv[3].endswith(".rds"):
            raise subprocess.CalledProcessError(1, argv)

    with pytest.raises(subprocess.CalledProcessError):
        publish_dataset(
            _SPEC, 2026, base=tmp_path, runner=runner, digests=lambda t, r: {},
            make_rds=False,
        )
    assert files["parquet"].name not in _data_uploads(calls)


def test_no_release_creates_it_then_uploads(tmp_path: Path):
    _staged(tmp_path)
    calls: list[list[str]] = []
    publish_dataset(
        _SPEC, 2026, base=tmp_path, runner=calls.append, digests=lambda t, r: None,
        make_rds=False,
    )
    assert calls[0][:2] == ["release", "create"]
    assert len(_data_uploads(calls)) == 3


def test_unanswerable_listing_uploads_everything_without_create(tmp_path: Path):
    from ncaa_mfb_data_build import publish as P

    _staged(tmp_path)
    calls: list[list[str]] = []

    def _boom(t, r):
        raise P.GhUnavailable("x")

    publish_dataset(
        _SPEC, 2026, base=tmp_path, runner=calls.append, digests=_boom, make_rds=False
    )
    assert not any(c[:2] == ["release", "create"] for c in calls)
    assert len(_data_uploads(calls)) == 3


def test_release_digests_uses_rest_not_graphql(monkeypatch):
    from ncaa_mfb_data_build import publish as P

    seen = []

    def _run(argv, **kw):
        seen.append(argv)
        return _Proc(0, "a.parquet\tsha256:ab\n")

    monkeypatch.setattr(P.subprocess, "run", _run)
    assert P._gh_release_digests("t", DEFAULT_REPO) == {"a.parquet": "sha256:ab"}
    assert seen[0][:3] == ["gh", "api", f"repos/{DEFAULT_REPO}/releases/tags/t"]
