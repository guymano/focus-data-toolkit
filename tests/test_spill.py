"""SpillableIndexPool / SpillableMap: dict semantics before and after the SQLite spill."""

from __future__ import annotations

import pytest

from focus_data_toolkit.storage.spill import SpillableIndexPool


@pytest.fixture
def pool(tmp_path):
    p = SpillableIndexPool(tmp_path / "spill.sqlite", threshold=3)
    yield p
    p.close()


def _exercise(m) -> None:
    for i in range(10):
        m[f"k{i}"] = f"v{i}"


def test_map_behaves_like_dict_before_spill(tmp_path):
    pool = SpillableIndexPool(tmp_path / "s.sqlite", threshold=100)
    m = pool.make_map()
    _exercise(m)
    assert not pool.spilled
    assert not (tmp_path / "s.sqlite").exists()  # lazy: no file until a spill
    assert len(m) == 10
    assert m["k3"] == "v3"
    assert "k9" in m and "missing" not in m
    assert m.get("missing") is None
    pool.close()


def test_map_behaves_like_dict_after_spill(pool, tmp_path):
    m = pool.make_map()
    _exercise(m)  # threshold=3 -> spilled long ago
    assert pool.spilled
    assert (tmp_path / "spill.sqlite").exists()
    assert len(m) == 10
    assert m["k3"] == "v3"
    assert "k9" in m and "missing" not in m
    assert m.get("missing") is None
    with pytest.raises(KeyError):
        m["missing"]
    # Overwrite still works post-spill.
    m["k3"] = "new"
    assert m["k3"] == "new"
    # Deterministic sorted iteration post-spill.
    assert list(m) == sorted(f"k{i}" for i in range(10))
    del m["k3"]
    assert "k3" not in m and len(m) == 9
    with pytest.raises(KeyError):
        del m["k3"]


def test_maps_are_independent(pool):
    a, b = pool.make_map(), pool.make_map()
    a["x"] = "1"
    _exercise(b)
    assert "k5" not in a
    assert b.get("x") is None
    assert len(a) == 1 and len(b) == 10


def test_non_string_membership_is_false_after_spill(pool):
    m = pool.make_map()
    _exercise(m)
    assert 3 not in m  # type: ignore[comparison-overlap]


def test_threshold_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        SpillableIndexPool(tmp_path / "s.sqlite", threshold=0)
