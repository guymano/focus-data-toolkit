"""Atomic output directory: results appear only once everything succeeded.

Nothing is written to the destination until all files are on disk (in a temporary directory
on the *same* filesystem), mandatory validations have passed, and checksums + manifest are
written last. Publication is a single directory rename; on any error the temporary directory
is removed and the destination is left untouched. This prevents partial files, a stale mix of
old and new results, or an inconsistent manifest after a crash.

The temporary directory name carries a run id, and an operational ``_run.json`` sidecar
carries the run id / timestamp / file checksums — kept out of the deterministic business
manifest so dataset bytes stay reproducible while operational metadata is still recorded.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from collections.abc import Callable, Iterable
from enum import StrEnum
from pathlib import Path


class OnExists(StrEnum):
    REFUSE = "refuse"      # fail fast if the destination already exists (default, safest)
    REPLACE = "replace"    # atomically swap the existing destination for the new one
    VERSION = "version"    # write into a new versioned subdirectory, never touching prior results


class AtomicWriteError(Exception):
    """Raised on an atomic-write failure (e.g. destination exists under REFUSE)."""


class DestinationExistsError(AtomicWriteError):
    """The destination already exists and the policy is REFUSE."""


def _fsync_file(path: Path) -> None:
    with open(path, "rb") as handle:
        os.fsync(handle.fileno())


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

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self._committed and not self.keep_temp:
            shutil.rmtree(self._tmp, ignore_errors=True)
        return False

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
        # target exists -> swap: move it aside, move the new one in, then delete the old.
        trash = self._parent / f".trash-{self.run_id}"
        os.replace(target, trash)
        try:
            os.replace(self._tmp, target)
        except OSError:  # pragma: no cover - restore on failure
            os.replace(trash, target)
            raise
        finally:
            shutil.rmtree(trash, ignore_errors=True)
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
    "sha256_file",
    "sha256sums_text",
    "write_files_atomically",
]
