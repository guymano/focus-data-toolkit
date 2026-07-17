"""Journaled replace + crash recovery (PR-13): every mid-swap state is recoverable."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from focus_data_toolkit.cli import main
from focus_data_toolkit.io.atomic_writer import (
    OnExists,
    clean_leftovers,
    recover_interrupted_replaces,
    write_files_atomically,
)


def publish(dest: Path, content: bytes, on_exists=OnExists.REFUSE) -> Path:
    return write_files_atomically(dest, [("data.csv", content)], on_exists=on_exists)


def _journal(parent: Path, run_id: str, dest: Path) -> Path:
    journal = parent / f".replace-journal-{run_id}.json"
    journal.write_text(json.dumps({
        "run_id": run_id,
        "target": dest.name,
        "tmp": f".output.tmp-{run_id}",
        "trash": f".trash-{run_id}",
    }), encoding="utf-8")
    return journal


def _staged(parent: Path, run_id: str, content: bytes) -> Path:
    tmp = parent / f".output.tmp-{run_id}"
    tmp.mkdir()
    (tmp / "data.csv").write_bytes(content)
    return tmp


def _trashed(parent: Path, run_id: str, content: bytes) -> Path:
    trash = parent / f".trash-{run_id}"
    trash.mkdir()
    (trash / "data.csv").write_bytes(content)
    return trash


def test_replace_leaves_no_journal_or_trash_on_success(tmp_path):
    dest = tmp_path / "out"
    publish(dest, b"old")
    publish(dest, b"new", on_exists=OnExists.REPLACE)
    assert (dest / "data.csv").read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["out"]


def test_crash_between_renames_rolls_forward(tmp_path):
    # State: journal + old result in .trash-* + fully staged new result; destination missing.
    dest = tmp_path / "out"
    _journal(tmp_path, "dead01", dest)
    _trashed(tmp_path, "dead01", b"old")
    _staged(tmp_path, "dead01", b"new")
    actions = recover_interrupted_replaces(tmp_path, dest_name="out")
    assert any("rolled forward" in a for a in actions)
    assert (dest / "data.csv").read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["out"]


def test_crash_after_swap_before_trash_cleanup_drops_trash(tmp_path):
    # State: journal + .trash-* leftover; destination already holds the new result.
    dest = tmp_path / "out"
    publish(dest, b"new")
    _journal(tmp_path, "dead02", dest)
    _trashed(tmp_path, "dead02", b"old")
    actions = recover_interrupted_replaces(tmp_path, dest_name="out")
    assert any("removed leftover previous result" in a for a in actions)
    assert (dest / "data.csv").read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["out"]


def test_crash_before_first_rename_keeps_destination_and_cleans_staging(tmp_path):
    # State: journal + staged tmp; destination untouched (crash before any rename).
    dest = tmp_path / "out"
    publish(dest, b"old")
    _journal(tmp_path, "dead03", dest)
    _staged(tmp_path, "dead03", b"new")
    actions = recover_interrupted_replaces(tmp_path, dest_name="out")
    assert any("never started" in a for a in actions)
    assert (dest / "data.csv").read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["out"]


def test_crash_with_only_trash_rolls_back(tmp_path):
    # State: journal + .trash-*; destination and tmp both missing -> restore the old result.
    dest = tmp_path / "out"
    _journal(tmp_path, "dead04", dest)
    _trashed(tmp_path, "dead04", b"old")
    actions = recover_interrupted_replaces(tmp_path, dest_name="out")
    assert any("rolled back" in a for a in actions)
    assert (dest / "data.csv").read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["out"]


def test_next_publish_recovers_automatically_with_warning(tmp_path):
    dest = tmp_path / "out"
    _journal(tmp_path, "dead05", dest)
    _trashed(tmp_path, "dead05", b"old")
    _staged(tmp_path, "dead05", b"new")
    with pytest.warns(RuntimeWarning, match="recovered interrupted publish"):
        publish(dest, b"newer", on_exists=OnExists.REPLACE)
    assert (dest / "data.csv").read_bytes() == b"newer"
    assert [p.name for p in tmp_path.iterdir()] == ["out"]


def test_recovery_ignores_other_destinations(tmp_path):
    other = tmp_path / "other"
    _journal(tmp_path, "dead06", other)
    _trashed(tmp_path, "dead06", b"old")
    assert recover_interrupted_replaces(tmp_path, dest_name="out") == []
    assert (tmp_path / ".replace-journal-dead06.json").exists()
    # Unfiltered recovery (fdt clean) handles it.
    actions = recover_interrupted_replaces(tmp_path)
    assert any("rolled back" in a for a in actions)
    assert (other / "data.csv").read_bytes() == b"old"


def test_live_rename_failure_still_restores_old_result(tmp_path, monkeypatch):
    dest = tmp_path / "out"
    publish(dest, b"old")
    real_replace = os.replace
    calls = {"n": 0}

    def failing_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:  # the tmp -> target rename inside the swap
            raise OSError("injected failure between the two renames")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="injected"):
        publish(dest, b"new", on_exists=OnExists.REPLACE)
    monkeypatch.undo()
    assert (dest / "data.csv").read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["out"]


def test_clean_leftovers_removes_orphans_and_recovers(tmp_path):
    dest = tmp_path / "out"
    publish(dest, b"kept")
    _staged(tmp_path, "orphan1", b"junk")  # died during staging: no journal
    _trashed(tmp_path, "orphan2", b"junk")
    (tmp_path / ".replace-journal-bad.json").write_text("not json", encoding="utf-8")
    actions = clean_leftovers(tmp_path)
    assert len(actions) == 3
    assert (dest / "data.csv").read_bytes() == b"kept"
    assert [p.name for p in tmp_path.iterdir()] == ["out"]


def test_clean_cli_on_missing_destination_restores_it(tmp_path):
    # fdt clean --out DEST where DEST vanished mid-swap: recovery restores it.
    dest = tmp_path / "out"
    _journal(tmp_path, "dead07", dest)
    _trashed(tmp_path, "dead07", b"old")
    rc = main(["clean", "--out", str(dest)])
    assert rc == 0
    assert (dest / "data.csv").read_bytes() == b"old"


def test_clean_cli_reports_nothing_to_clean(tmp_path, capsys):
    rc = main(["clean", "--out", str(tmp_path)])
    assert rc == 0
    assert "nothing to clean" in capsys.readouterr().out


def test_crafted_journal_paths_are_never_followed(tmp_path):
    # A journal whose tmp/trash escape the parent must be rejected before any filesystem
    # operation — recovery takes no action, and clean removes it as stale.
    parent = tmp_path / "work"
    parent.mkdir()
    dest = parent / "out"
    publish(dest, b"kept")
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "precious.txt").write_text("do not delete", encoding="utf-8")
    (parent / ".replace-journal-evil.json").write_text(json.dumps({
        "run_id": "evil",
        "target": "out",
        "tmp": ".output.tmp-evil",
        "trash": "../victim",
    }), encoding="utf-8")
    assert recover_interrupted_replaces(parent, dest_name="out") == []
    assert (victim / "precious.txt").exists()
    actions = clean_leftovers(dest)
    assert any("stale replace journal" in a for a in actions)
    assert (victim / "precious.txt").exists()
    assert (dest / "data.csv").read_bytes() == b"kept"


def test_crafted_journal_absolute_paths_are_rejected(tmp_path):
    parent = tmp_path / "work"
    parent.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (parent / ".replace-journal-abs.json").write_text(json.dumps({
        "run_id": "abs",
        "target": "out",
        "tmp": ".output.tmp-abs",
        "trash": str(victim),
    }), encoding="utf-8")
    assert recover_interrupted_replaces(parent) == []
    assert victim.exists()


def test_clean_on_output_directory_sweeps_sibling_leftovers(tmp_path):
    # Leftovers live NEXT to the destination (AtomicOutputDir stages in the parent):
    # `fdt clean --out DEST` must sweep them without the user guessing the parent.
    dest = tmp_path / "out"
    publish(dest, b"kept")
    _staged(tmp_path, "sibling1", b"junk")  # died during staging, no journal
    _trashed(tmp_path, "sibling2", b"junk")
    rc = main(["clean", "--out", str(dest)])
    assert rc == 0
    assert (dest / "data.csv").read_bytes() == b"kept"
    assert [p.name for p in tmp_path.iterdir()] == ["out"]
