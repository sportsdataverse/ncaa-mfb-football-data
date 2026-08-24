"""Gate the numbered stage-shim set AND its ordering against the registry.

Mirrors the WBB/MBB twins' convention: ``python/ncaa_mfb_NN_{dataset}_creation.py``,
one per released dataset, numbered in ``config.REGISTRY`` insertion order (the
order ``--dataset all`` builds in). Renumbering one without the other fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

from ncaa_mfb_data_build.config import REGISTRY

PY_DIR = Path(__file__).resolve().parents[1] / "python"
_SHIM_RE = re.compile(r"^ncaa_mfb_(\d{2})_(.+)_creation\.py$")


def _shims() -> "list[tuple[int, str]]":
    out = []
    for p in sorted(PY_DIR.glob("ncaa_mfb_*_creation.py")):
        m = _SHIM_RE.match(p.name)
        assert m, f"stage shim {p.name} does not match NN_<dataset>_creation.py"
        out.append((int(m.group(1)), m.group(2)))
    return out


def test_one_shim_per_registry_dataset() -> None:
    assert [name for _, name in _shims()] == list(REGISTRY)


def test_numbers_ascend_with_registry_order() -> None:
    nums = [n for n, _ in _shims()]
    assert nums == list(range(1, len(REGISTRY) + 1))


def test_shims_invoke_their_own_dataset() -> None:
    for n, name in _shims():
        text = (PY_DIR / f"ncaa_mfb_{n:02d}_{name}_creation.py").read_text(encoding="utf-8")
        assert f'"--dataset", "{name}"' in text
