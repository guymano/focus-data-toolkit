"""Atomic output directory: results appear only once everything succeeded.

Nothing is written to the destination until all files are on disk (in a temporary directory
on the *same* filesystem), mandatory validations have passed, and checksums + manifest are
written last. Publication is a single directory rename; on any error the temporary directory
is removed and the destination is left untouched. This prevents partial files, a stale mix of
old and new results, or an inconsistent manifest after a crash.

A **replace** (``on_exists=replace``) needs two renames (old aside, new in), so that swap is
**journaled**: a durable ``.replace-journal-<run_id>.json`` in the parent directory records
the (target, tmp, trash) names before the first rename and is removed after the swap
concludes. If the process dies inside the window, the next :class:`AtomicOutputDir` for the
same destination — or an explicit ``fdt clean`` — reads the journal and finishes the job:
roll the fully-staged new result forward, or roll the old result back, never leaving the
destination missing. Recovery actions are surfaced as :class:`RuntimeWarning`s.

The temporary directory name carries a run id, and an operational ``_run.json`` sidecar
carries the run id / timestamp / file checksums — kept out of the deterministic business
manifest so dataset bytes stay reproducible while operational metadata is still recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
import warnings
from collections.abc import Callable, Iterable
from enum import StrEnum
from pathlib import Path

_JOURNAL_PREFIX = ".replace-journal-"
_TMP_PREFIX = ".output.tmp-"
_TRASH_PREFIX = ".trash-"


class OnExists(StrEnum):
    REFUSE = "refuse"      # fail fast if the destination already exists (default, safest)
    REPLACE = "replace"    # atomically swap the existing destination for the new one
    VERSION = "version"    # write into a new versioned subdirectory, never touching prior results


class AtomicWriteError(Exception):
    """Raised on an atomic-write failure (e.g. destination exists under REFUSE)."""


class DestinationExistsError(AtomicWriteError):
    """The destination already exists and the policy is REFUSE."""


def _fsync_file(path: Path) -> None:
    # Durability hardening, best-effort. On POSIX, fsync of a read-only fd works and is
    # honoured. On Windows, os.fsync requires a writable descriptor — a read-only handle
    # raises EBADF — and there is no portable read-only file-flush, so a refused fsync must
    # not fail an otherwise-complete publish (the atomic directory rename remains the
    # correctness guarantee). This mirrors the best-effort handling in ``_fsync_dir``.
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    # POSIX only; Windows has no directory fsync and forbids opening a dir fd.
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _is_plain_name(name: object) -> bool:
    """A single path component: non-empty, no separators/drive, no traversal, not absolute."""
    if not isinstance(name, str) or not name or name in (".", ".."):
        return False
    if any(sep in name for sep in ("/", "\\", ":", "\x00")):
        return False
    return Path(name).name == name and not Path(name).is_absolute()


def _read_journal(journal: Path) -> dict | None:
    """Load a replace journal; None when unreadable/invalid (left for ``fdt clean``).

    Every recorded name must be a plain sibling basename with the writer's own prefixes —
    a crafted or corrupted journal (absolute paths, ``..``, foreign names) must never steer
    recovery renames/removals outside the output parent, so it is rejected here before any
    filesystem operation and later removed as stale by :func:`clean_leftovers`.
    """
    try:
        info = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (isinstance(info, dict) and {"target", "tmp", "trash"} <= set(info)):
        return None
    target, tmp, trash = info["target"], info["tmp"], info["trash"]
    if not all(_is_plain_name(name) for name in (target, tmp, trash)):
        return None
    if not (tmp.startswith(_TMP_PREFIX) and trash.startswith(_TRASH_PREFIX)):
        return None
    if target.startswith((_TMP_PREFIX, _TRASH_PREFIX, _JOURNAL_PREFIX)):
        return None  # a destination is never one of the writer's own scratch names
    return info


def recover_interrupted_replaces(
    parent: str | os.PathLike[str], *, dest_name: str | None = None
) -> list[str]:
    """Finish (or safely undo) journaled replace swaps that a crash left half-done.

    Scans ``parent`` for ``.replace-journal-*`` files — one exists only inside a replace
    swap — and resolves each unambiguous state:

    * destination missing, staged tmp present → **roll forward** (the tmp was fully written,
      validated and fsync'd before the journal was created), then drop the old ``.trash-*``;
    * destination missing, only ``.trash-*`` present → **roll back** the old result;
    * destination present → the swap concluded (or never started): drop the leftover
      ``.trash-*`` and, if the run died before its first rename, the never-published tmp.

    ``dest_name`` restricts recovery to journals targeting that destination (what
    :class:`AtomicOutputDir` uses on entry). Returns a description of every action taken.
    """
    parent = Path(parent)
    actions: list[str] = []
    for journal in sorted(parent.glob(f"{_JOURNAL_PREFIX}*.json")):
        info = _read_journal(journal)
        if info is None or (dest_name is not None and info["target"] != dest_name):
            continue
        target = parent / info["target"]
        tmp = parent / info["tmp"]
        trash = parent / info["trash"]
        if not target.exists() and tmp.exists():
            os.replace(tmp, target)
            actions.append(
                f"rolled forward interrupted replace of {target}: published the fully "
                f"staged result from {tmp.name}"
            )
            if trash.exists():
                shutil.rmtree(trash, ignore_errors=True)
                actions.append(f"removed superseded previous result {trash.name}")
        elif not target.exists() and trash.exists():
            os.replace(trash, target)
            actions.append(
                f"rolled back interrupted replace of {target}: restored the previous "
                f"result from {trash.name}"
            )
        else:
            if trash.exists():
                shutil.rmtree(trash, ignore_errors=True)
                actions.append(f"removed leftover previous result {trash.name}")
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
                actions.append(
                    f"removed staged result {tmp.name} of a replace that never started "
                    f"(destination {target.name} is intact); re-run the conversion"
                )
        journal.unlink(missing_ok=True)
        _fsync_dir(parent)
    return actions


def _remove_orphans(directory: Path) -> list[str]:
    """Remove leftover staging/trash directories and stale journals inside ``directory``.

    Runs after :func:`recover_interrupted_replaces`, so any journal still present is
    unreadable or invalid — removed as stale, never acted upon.
    """
    actions: list[str] = []
    for leftover in sorted(directory.iterdir()):
        if leftover.name.startswith((_TMP_PREFIX, _TRASH_PREFIX)) and leftover.is_dir():
            shutil.rmtree(leftover, ignore_errors=True)
            kind = "unpublished staging" if leftover.name.startswith(_TMP_PREFIX) else "trash"
            actions.append(f"removed orphan {kind} directory {leftover.name}")
        elif leftover.name.startswith(_JOURNAL_PREFIX) and leftover.is_file():
            leftover.unlink(missing_ok=True)
            actions.append(f"removed stale replace journal {leftover.name}")
    if actions:
        _fsync_dir(directory)
    return actions


def clean_leftovers(directory: str | os.PathLike[str]) -> list[str]:
    """Recover journaled replaces, then remove orphan staging/trash leftovers (``fdt clean``).

    ``AtomicOutputDir`` stages ``.output.tmp-*`` / ``.trash-*`` / journals in the
    **destination's parent**, so when ``directory`` is an output directory its leftovers are
    siblings: both ``directory`` itself (as a container of outputs) and its parent are
    swept. Journaled replaces are recovered first (any destination — this is explicit
    maintenance), then orphans (staging from runs that died before publishing, trash from
    concluded swaps, unreadable journals) are removed. Only call this when no conversion is
    currently publishing here — a live run's staging directory is indistinguishable from a
    dead one's.
    """
    directory = Path(directory)
    actions: list[str] = []
    parent = directory.parent
    if parent != directory and parent.is_dir():
        actions.extend(recover_interrupted_replaces(parent))
        actions.extend(_remove_orphans(parent))
    if directory.is_dir():
        actions.extend(recover_interrupted_replaces(directory))
        actions.extend(_remove_orphans(directory))
    return actions


def sha256_file(path: Path) -> str:
    """Stream ``path`` through SHA-256 (bounded memory) and return the hex digest."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AtomicOutputDir:
    """A staging directory that publishes to ``dest`` atomically on :meth:`commit`.

    Usage::

        with AtomicOutputDir(dest, on_exists=OnExists.REFUSE) as out:
            out.write_bytes("a.csv", data)
            # ... run mandatory validations; raise to abort and clean up ...
            out.commit(final_files={"manifest.json": manifest_bytes})
    """

    def __init__(
        self,
        dest: str | os.PathLike[str],
        *,
        on_exists: OnExists | str = OnExists.REFUSE,
        keep_temp: bool = False,
        run_id: str | None = None,
    ) -> None:
        self.dest = Path(dest)
        self.on_exists = OnExists(on_exists)
        self.keep_temp = keep_temp
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._parent = self.dest.parent
        self._tmp = self._parent / f".output.tmp-{self.run_id}"
        self._data_files: list[Path] = []
        self._committed = False
        self._published_path: Path | None = None

    # -- context management ----------------------------------------------------
    def __enter__(self) -> AtomicOutputDir:
        # A previous run may have died mid-swap: finish its journaled replace first, so the
        # destination is in a consistent state before this run's policy is applied.
        if self._parent.is_dir():
            for action in recover_interrupted_replaces(self._parent, dest_name=self.dest.name):
                warnings.warn(f"recovered interrupted publish: {action}", RuntimeWarning,
                              stacklevel=2)
        if self.on_exists is OnExists.REFUSE and self.dest.exists():
            raise DestinationExistsError(
                f"destination {self.dest} already exists (on_exists=refuse)"
            )
        self._parent.mkdir(parents=True, exist_ok=True)
        if self._tmp.exists():  # pragma: no cover - astronomically unlikely id clash
            shutil.rmtree(self._tmp)
        self._tmp.mkdir()
        if os.stat(self._tmp).st_dev != os.stat(self._parent).st_dev:  # pragma: no cover
            shutil.rmtree(self._tmp, ignore_errors=True)
            raise AtomicWriteError("temporary directory is not on the destination filesystem")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Return None (falsy) — never suppress the exception.
        if not self._committed and not self.keep_temp:
            shutil.rmtree(self._tmp, ignore_errors=True)

    # -- writing ---------------------------------------------------------------
    def path_for(self, name: str) -> Path:
        # Reject absolute paths and '..' so a caller-supplied name cannot escape the staging
        # directory (which would write outside the atomic flow and the checksum set). Relative
        # subdirectories are allowed, e.g. for Parquet partitioning.
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise AtomicWriteError(
                f"unsafe output name {name!r}: must be a relative path without '..'"
            )
        return self._tmp / candidate

    def write_bytes(self, name: str, data: bytes, *, is_data: bool = True) -> Path:
        """Write ``data`` into the staging dir, flush and fsync it."""
        path = self.path_for(name)
        with open(path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if is_data:
            self._data_files.append(path)
        return path

    def write_text(self, name: str, text: str, *, is_data: bool = True) -> Path:
        return self.write_bytes(name, text.encode("utf-8"), is_data=is_data)

    def add_data_file(self, name: str) -> Path:
        """Register a staging file written directly (not via :meth:`write_bytes`).

        The streaming path writes large datasets through incremental file handles to keep memory
        bounded; this fsyncs the finished file and enrolls it in the checksum/size set so it is
        durably persisted and covered by ``SHA256SUMS`` like any other data file.
        """
        path = self.path_for(name)
        _fsync_file(path)
        self._data_files.append(path)
        return path

    def add_data_tree(self, name: str) -> list[Path]:
        """Register every file under a staged directory (e.g. a partitioned Parquet dataset).

        Each file is fsync'd and enrolled for checksums under its path relative to the staging
        directory, so a partition tree is covered by ``SHA256SUMS`` file-by-file. Every directory
        in the tree (root and each partition level) is fsync'd too, so the nested directory
        entries are durable before publish — otherwise a crash could lose part files that the
        manifest and ``SHA256SUMS`` already reference.
        """
        root = self.path_for(name)
        added = sorted(p for p in root.rglob("*") if p.is_file())
        dirs: set[Path] = {root}
        for path in added:
            _fsync_file(path)
            self._data_files.append(path)
            dirs.update(path.parents)  # every partition-level dir up the tree
        # fsync deepest-first so a parent's entry for a child dir is persisted after the child.
        for directory in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
            if root in (directory, *directory.parents):  # stay within the staged tree
                _fsync_dir(directory)
        return added

    def discard(self, name: str) -> None:
        """Delete a staging file (e.g. scratch state) so it is never published."""
        self.path_for(name).unlink(missing_ok=True)

    def _rel(self, path: Path) -> str:
        """Path relative to the staging dir — the key under which a file is published/checksummed.

        Using the relative path (not just the basename) keeps partition part files distinct
        (many partitions each have a ``part-0.parquet``); for flat files it equals the basename,
        so single-file output is unaffected.
        """
        return path.relative_to(self._tmp).as_posix()

    def checksums(self) -> dict[str, str]:
        """SHA-256 of every data file written so far, keyed by relative path (sorted)."""
        return {self._rel(p): sha256_file(p) for p in sorted(self._data_files, key=self._rel)}

    def sizes(self) -> dict[str, int]:
        return {self._rel(p): p.stat().st_size for p in sorted(self._data_files, key=self._rel)}

    # -- publishing ------------------------------------------------------------
    def commit(self, *, final_files: dict[str, bytes] | None = None) -> Path:
        """Write ``final_files`` (manifest, checksums) last, then rename atomically."""
        for name, data in (final_files or {}).items():
            self.write_bytes(name, data, is_data=False)
        _fsync_dir(self._tmp)
        target = self._resolve_target()
        self._atomic_publish(target)
        self._committed = True
        self._published_path = target
        return target

    def _resolve_target(self) -> Path:
        if self.on_exists is OnExists.VERSION:
            self.dest.mkdir(parents=True, exist_ok=True)
            return self.dest / f"run-{self.run_id}"
        return self.dest

    def _atomic_publish(self, target: Path) -> None:
        # Close is implicit (files already closed). Windows forbids renaming over open handles.
        if not target.exists():
            os.replace(self._tmp, target)
            _fsync_dir(target.parent)
            return
        # The destination was checked at __enter__, but another run may have created it while
        # this one was staging. Re-honour the refuse policy at publish time rather than
        # clobbering a concurrently-published result.
        if self.on_exists is OnExists.REFUSE:
            raise DestinationExistsError(
                f"destination {target} appeared during staging (on_exists=refuse)"
            )
        if self.on_exists is OnExists.VERSION:  # pragma: no cover - run-id collision
            raise AtomicWriteError(
                f"versioned destination {target} already exists (run-id collision); retry"
            )
        # target exists -> swap: move it aside, move the new one in, then delete the old.
        # The two renames are journaled so a crash inside the window is recoverable (the
        # journal is written and fsync'd durably *before* the destination is touched).
        trash = self._parent / f"{_TRASH_PREFIX}{self.run_id}"
        journal = self._parent / f"{_JOURNAL_PREFIX}{self.run_id}.json"
        record = {
            "run_id": self.run_id,
            "target": target.name,
            "tmp": self._tmp.name,
            "trash": trash.name,
        }
        with open(journal, "wb") as handle:
            handle.write(json.dumps(record, sort_keys=True).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_dir(self._parent)
        os.replace(target, trash)
        try:
            os.replace(self._tmp, target)
        except OSError:  # pragma: no cover - restore on failure
            os.replace(trash, target)
            raise
        finally:
            shutil.rmtree(trash, ignore_errors=True)
            journal.unlink(missing_ok=True)
        _fsync_dir(target.parent)


def sha256sums_text(checksums: dict[str, str]) -> str:
    """Render a ``SHA256SUMS`` file body (``<hex>  <name>`` per line, sorted)."""
    return "".join(f"{checksums[name]}  {name}\n" for name in sorted(checksums))


def write_files_atomically(
    dest: str | os.PathLike[str],
    data_files: Iterable[tuple[str, bytes]],
    *,
    final_files: dict[str, bytes] | None = None,
    on_exists: OnExists | str = OnExists.REFUSE,
    keep_temp: bool = False,
    validate: Callable[[], None] | None = None,
) -> Path:
    """Write ``data_files`` then ``final_files`` to ``dest`` atomically.

    ``validate`` (if given) runs after the data files are staged and before anything is
    published; raising from it aborts the write and removes the staging directory.
    """
    with AtomicOutputDir(dest, on_exists=on_exists, keep_temp=keep_temp) as out:
        for name, data in data_files:
            out.write_bytes(name, data)
        if validate is not None:
            validate()
        return out.commit(final_files=final_files)


__all__ = [
    "AtomicOutputDir",
    "AtomicWriteError",
    "DestinationExistsError",
    "OnExists",
    "clean_leftovers",
    "recover_interrupted_replaces",
    "sha256_file",
    "sha256sums_text",
    "write_files_atomically",
]
