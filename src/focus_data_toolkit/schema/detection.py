"""Identify the FOCUS dataset and version of an arbitrary header row.

The previous approach tested a header against four marker columns and returned only
``"1.2"``/``"1.3"``. That mis-detects a 1.3 export missing an optional 1.3 column as 1.2,
never recognises 1.4, and cannot tell the four datasets apart.

:func:`detect_focus_schema` instead scores the header against every ``(dataset, version)``
schema in the registry (present columns, absent-but-expected columns, and FOCUS columns
that belong to the dataset but a *different* version — the hybrid signal), and reports a
confidence plus the exact discrepancies. ``x_``-prefixed extension columns never count
against a match; unknown non-``x_`` columns are surfaced separately.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from focus_data_toolkit.schema import registry

CONF_HIGH = "HIGH"
CONF_MEDIUM = "MEDIUM"
CONF_LOW = "LOW"

# A runner-up candidate whose Jaccard similarity is within this of the best is "ambiguous".
_AMBIGUITY_DELTA = 0.04
# Below this best-similarity floor the header is treated as "not FOCUS" (no dataset).
_DATASET_FLOOR = 0.20


@dataclass(frozen=True)
class SchemaDetectionResult:
    """Outcome of detecting the FOCUS dataset/version of a header row."""

    dataset: str | None
    detected_version: str | None
    confidence: str
    exact_match: bool
    score: float
    missing_columns: tuple[str, ...]
    additional_focus_columns: tuple[str, ...]
    extension_columns: tuple[str, ...]
    unknown_columns: tuple[str, ...]
    ambiguous_candidates: tuple[tuple[str, str], ...]
    forced: bool = False
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """A confident, unambiguous identification of a supported schema."""
        return (
            self.dataset is not None
            and self.detected_version is not None
            and self.confidence == CONF_HIGH
        )

    def as_dict(self) -> dict:
        """JSON-serialisable view for the manifest."""
        return {
            "dataset": self.dataset,
            "detected_version": self.detected_version,
            "confidence": self.confidence,
            "exact_match": self.exact_match,
            "score": round(self.score, 4),
            "forced": self.forced,
            "missing_columns": list(self.missing_columns),
            "additional_focus_columns": list(self.additional_focus_columns),
            "extension_columns": list(self.extension_columns),
            "unknown_columns": list(self.unknown_columns),
            "ambiguous_candidates": [list(c) for c in self.ambiguous_candidates],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class _Candidate:
    dataset: str
    version: str
    jaccard: float
    mandatory_coverage: float
    missing: tuple[str, ...]
    additional_focus: tuple[str, ...]


def _score(dataset: str, version: str, headers: frozenset[str]) -> _Candidate:
    expected = registry.version_columns(dataset, version)
    dataset_focus = registry.all_dataset_columns(dataset)
    present = expected & headers
    missing = expected - headers
    additional_focus = (headers & dataset_focus) - expected
    union = expected | (headers & dataset_focus)
    jaccard = len(present) / len(union) if union else 0.0
    mandatory = registry.mandatory_columns(dataset, version)
    mandatory_coverage = len(mandatory & headers) / len(mandatory) if mandatory else 1.0
    return _Candidate(
        dataset=dataset,
        version=version,
        jaccard=jaccard,
        mandatory_coverage=mandatory_coverage,
        missing=tuple(sorted(missing)),
        additional_focus=tuple(sorted(additional_focus)),
    )


def detect_focus_schema(
    headers: Iterable[str],
    *,
    dataset: str | None = None,
    version: str | None = None,
) -> SchemaDetectionResult:
    """Detect the FOCUS dataset and version of ``headers``.

    ``dataset`` and/or ``version`` force the corresponding dimension (they still get scored,
    so a bad forced choice yields a low confidence the caller can reject). Unknown forced
    values raise ``ValueError``.
    """
    header_list = [h for h in headers]
    header_set = frozenset(header_list)
    extension = tuple(sorted(h for h in header_set if h.startswith("x_")))
    focus_all = registry.all_focus_columns()
    unknown = tuple(sorted(h for h in header_set if not h.startswith("x_") and h not in focus_all))

    forced_dataset = registry.resolve_dataset_name(dataset) if dataset is not None else None
    forced_version = registry.normalize_version(version) if version is not None else None
    forced = forced_dataset is not None or forced_version is not None

    candidates = registry.candidate_schemas()
    if forced_dataset is not None:
        candidates = [c for c in candidates if c[0] == forced_dataset]
    if forced_version is not None:
        candidates = [c for c in candidates if c[1] == forced_version]

    if not candidates:
        note = "forced (dataset, version) does not exist in FOCUS"
        return SchemaDetectionResult(
            dataset=forced_dataset,
            detected_version=forced_version,
            confidence=CONF_LOW,
            exact_match=False,
            score=0.0,
            missing_columns=(),
            additional_focus_columns=(),
            extension_columns=extension,
            unknown_columns=unknown,
            ambiguous_candidates=(),
            forced=forced,
            notes=(note,),
        )

    scored = sorted(
        (_score(d, v, header_set) for d, v in candidates),
        key=lambda c: (c.jaccard, c.mandatory_coverage),
        reverse=True,
    )
    best = scored[0]

    # Ambiguity: any other candidate whose similarity is within delta of the best.
    ambiguous = tuple(
        (c.dataset, c.version)
        for c in scored[1:]
        if c.jaccard > 0 and best.jaccard - c.jaccard < _AMBIGUITY_DELTA
    )

    notes: list[str] = []
    dataset_name: str | None = best.dataset
    detected_version: str | None = best.version

    if not forced and best.jaccard < _DATASET_FLOOR:
        # Header does not resemble any FOCUS schema.
        return SchemaDetectionResult(
            dataset=None,
            detected_version=None,
            confidence=CONF_LOW,
            exact_match=False,
            score=round(best.jaccard, 4),
            missing_columns=(),
            additional_focus_columns=(),
            extension_columns=extension,
            unknown_columns=unknown,
            ambiguous_candidates=(),
            forced=forced,
            notes=("header does not match any known FOCUS dataset/version",),
        )

    exact_match = not best.missing and not best.additional_focus and not unknown

    # Confidence.
    if forced_version is not None:
        # The version is locked by the user. Only columns that belong to *another* version of
        # this dataset (``additional_focus``) make the forced version genuinely impossible;
        # merely missing columns are a completeness issue the lint reports, not a reason to
        # override an explicit choice.
        if best.additional_focus:
            confidence = CONF_LOW
            notes.append(
                f"header is incompatible with forced version {forced_version} "
                f"(columns from another version: {', '.join(best.additional_focus)})"
            )
        elif best.missing or best.mandatory_coverage < 0.999:
            confidence = CONF_MEDIUM  # compatible but incomplete (lint will flag specifics)
        else:
            confidence = CONF_HIGH
    elif best.jaccard >= 0.9 and best.mandatory_coverage >= 0.999 and not ambiguous and not unknown:
        confidence = CONF_HIGH
    elif best.jaccard >= 0.6 and best.mandatory_coverage >= 0.8:
        confidence = CONF_MEDIUM
    else:
        confidence = CONF_LOW

    if ambiguous and confidence == CONF_HIGH:
        confidence = CONF_MEDIUM
    if unknown and confidence == CONF_HIGH:
        confidence = CONF_MEDIUM
        notes.append(f"{len(unknown)} unknown non-x_ column(s) present")
    elif unknown:
        notes.append(f"{len(unknown)} unknown non-x_ column(s) present")
    if best.additional_focus:
        notes.append(
            "columns from another FOCUS version present: " + ", ".join(best.additional_focus)
        )
    if ambiguous:
        notes.append(
            "close alternative schema(s): " + ", ".join(f"{d} {v}" for d, v in ambiguous)
        )

    return SchemaDetectionResult(
        dataset=dataset_name,
        detected_version=detected_version,
        confidence=confidence,
        exact_match=exact_match,
        score=round(best.jaccard, 4),
        missing_columns=best.missing,
        additional_focus_columns=best.additional_focus,
        extension_columns=extension,
        unknown_columns=unknown,
        ambiguous_candidates=ambiguous,
        forced=forced,
        notes=tuple(notes),
    )
