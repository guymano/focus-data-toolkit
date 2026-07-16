"""Normative FOCUS column sets per (dataset, version).

Everything here is *computed* from the committed FOCUS 1.4 model — each column records
the ``version`` it was introduced in — plus a small hand-authored table of columns that
existed in an earlier version but were **removed** by 1.4 (and so are absent from the 1.4
model). This keeps the registry in lock-step with the model artifact while still being
able to describe 1.2 and 1.3 headers faithfully.

Source of truth for the removed-column table: the FOCUS changelog and this repository's own
1.2/1.3 generators. ``ProviderName`` / ``PublisherName`` were superseded by
``ServiceProviderName`` / ``HostProviderName`` in 1.3 and removed in 1.4.

Sanity check (see ``version_columns``): Cost and Usage yields 57 columns at 1.2, 65 at 1.3
and 65 at 1.4 — matching the generators (57/65) and the 1.4 model (65).
"""

from __future__ import annotations

from functools import cache

from focus_data_toolkit.model import FOCUS_1_4_DATASETS, load_model, resolve_dataset

# FOCUS versions this toolkit reasons about for detection/conversion.
SUPPORTED_VERSIONS: tuple[str, ...] = ("1.2", "1.3", "1.4")

# dataset -> {column: (introduced_in, removed_in, mandatory_before)} for columns removed by
# 1.4. ``mandatory_before`` is the version at which the column stopped being required because
# a replacement arrived; a 1.2 Cost and Usage source needs ProviderName / PublisherName to
# derive the 1.4-Mandatory ServiceProviderName / HostProviderName (which do not exist until
# 1.3), so they are mandatory for versions < 1.3.
REMOVED_COLUMNS: dict[str, dict[str, tuple[str, str, str]]] = {
    "Cost and Usage": {
        "ProviderName": ("0.5", "1.4", "1.3"),
        "PublisherName": ("0.5", "1.4", "1.3"),
    },
}


def version_tuple(version: str) -> tuple[int, int]:
    """Parse a ``"major.minor"`` (or longer) FOCUS version to a comparable tuple."""
    parts = version.strip().split(".")
    try:
        return (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"unparseable FOCUS version {version!r}") from exc


def normalize_version(version: str) -> str:
    """Normalise a version string to ``"major.minor"`` (e.g. ``"1.3.0"`` -> ``"1.3"``)."""
    major, minor = version_tuple(version)
    return f"{major}.{minor}"


@cache
def version_columns(dataset: str, version: str) -> frozenset[str]:
    """FOCUS columns of ``dataset`` present at ``version``.

    A model column is present when it was introduced at or before ``version``; a removed
    column is present when it was introduced at or before ``version`` and removed strictly
    after it.
    """
    cols = load_model()["datasets"][dataset]["columns"]
    tv = version_tuple(version)
    present = {c for c, spec in cols.items() if version_tuple(spec["version"]) <= tv}
    for col, (intro, removed, _mandatory_before) in REMOVED_COLUMNS.get(dataset, {}).items():
        if version_tuple(intro) <= tv < version_tuple(removed):
            present.add(col)
    return frozenset(present)


@cache
def mandatory_columns(dataset: str, version: str) -> frozenset[str]:
    """Mandatory FOCUS columns of ``dataset`` present at ``version``.

    Feature level is taken from the 1.4 model; removed columns that were required at
    ``version`` (before a replacement arrived) are added so detection does not accept a source
    that cannot fill the 1.4-Mandatory columns it derives.
    """
    cols = load_model()["datasets"][dataset]["columns"]
    tv = version_tuple(version)
    mandatory = {
        c
        for c, spec in cols.items()
        if version_tuple(spec["version"]) <= tv and spec.get("feature_level") == "Mandatory"
    }
    for col, (intro, _removed, mandatory_before) in REMOVED_COLUMNS.get(dataset, {}).items():
        if version_tuple(intro) <= tv < version_tuple(mandatory_before):
            mandatory.add(col)
    return frozenset(mandatory)


@cache
def dataset_exists_at(dataset: str, version: str) -> bool:
    """Whether ``dataset`` is defined at all at ``version`` (has any column)."""
    return bool(version_columns(dataset, version))


@cache
def all_dataset_columns(dataset: str) -> frozenset[str]:
    """Every FOCUS column of ``dataset`` across all versions, including removed ones."""
    cols = set(load_model()["datasets"][dataset]["columns"])
    cols |= set(REMOVED_COLUMNS.get(dataset, {}))
    return frozenset(cols)


@cache
def all_focus_columns() -> frozenset[str]:
    """Every FOCUS column name across every dataset and version."""
    out: set[str] = set()
    for dataset in FOCUS_1_4_DATASETS:
        out |= all_dataset_columns(dataset)
    return frozenset(out)


def candidate_schemas() -> list[tuple[str, str]]:
    """All ``(dataset, version)`` pairs that actually exist, in canonical order."""
    return [
        (dataset, version)
        for dataset in FOCUS_1_4_DATASETS
        for version in SUPPORTED_VERSIONS
        if dataset_exists_at(dataset, version)
    ]


def resolve_dataset_name(name: str) -> str:
    """Resolve a dataset alias (``"cau"``, ``"cost-and-usage"``, ...) to its canonical name."""
    return resolve_dataset(name.replace("-", " "))
