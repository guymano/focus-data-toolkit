"""Progress reporting and cooperative cancellation for long-running conversions.

These are optional, dependency-free hooks. The streaming engine
(:func:`focus_data_toolkit.convert.convert_files`) emits throttled
:class:`ProgressEvent`\\ s and checks a :data:`CancelPredicate` between rows and
validation passes, so a caller — the CLI (``--progress`` + SIGINT/SIGTERM) or the
Studio backend (a cancel button) — can show progress and cancel cleanly without the
engine importing any UI concern. All hooks are opt-in: a conversion with neither a
callback nor a predicate behaves exactly as before.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

# The ordered phases a streaming conversion moves through. Progress is reported per phase
# rather than as a single 0..100% bar, because the passes measure different things (bytes
# read, rows aggregated, rows validated) and a single global percentage would mislead.
Phase = Literal[
    "READING",       # cheap key-collection pre-pass over the source (supplements only)
    "TRANSFORMING",  # the main Cost and Usage read + convert + write loop
    "AGGREGATING",   # Invoice Detail / Billing Period finalisation from the on-disk index
    "WRITING",       # writing the derived datasets
    "VALIDATING",    # per-dataset lint + cross-dataset bundle gate
    "PUBLISHING",    # checksums + manifest + the single atomic rename
]

PHASES: tuple[Phase, ...] = (
    "READING",
    "TRANSFORMING",
    "AGGREGATING",
    "WRITING",
    "VALIDATING",
    "PUBLISHING",
)


@dataclass(frozen=True)
class ProgressEvent:
    """A single progress observation emitted from a conversion phase.

    ``total`` is ``None`` when it cannot be known without extra work (e.g. rows aggregated
    on disk, whose count is not known until the pass completes). ``unit`` is ``"rows"`` or
    ``"bytes"`` — the ``TRANSFORMING`` phase reports bytes for a CSV source (byte cursor /
    file size) and rows for a Parquet source (footer row count). :attr:`fraction` is a
    convenience 0..1 derived from ``completed``/``total`` (``None`` when indeterminate).
    """

    phase: Phase
    completed: int
    total: int | None = None
    unit: str = "rows"
    message: str | None = None

    @property
    def fraction(self) -> float | None:
        """Completion in ``0..1`` when a total is known, else ``None`` (indeterminate)."""
        if self.total is None or self.total <= 0:
            return None
        return min(1.0, self.completed / self.total)

    def as_dict(self) -> dict:
        """JSON-friendly view (e.g. for a Studio SSE stream)."""
        return {
            "phase": self.phase,
            "completed": self.completed,
            "total": self.total,
            "unit": self.unit,
            "fraction": self.fraction,
            "message": self.message,
        }


# A caller-supplied sink for progress events. It must be cheap and must not raise.
ProgressCallback = Callable[[ProgressEvent], None]
# Returns True when cancellation has been requested. Checked cooperatively between rows /
# validation passes; the engine raises ConversionCancelled and publishes nothing.
CancelPredicate = Callable[[], bool]


__all__ = [
    "PHASES",
    "CancelPredicate",
    "Phase",
    "ProgressCallback",
    "ProgressEvent",
]
