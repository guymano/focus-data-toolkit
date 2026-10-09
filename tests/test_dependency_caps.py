"""PyArrow caps: pyarrow 26 needs NumPy 2 at import without declaring it, while focus-validator
(2.1 to 2.2.1) pins ``numpy<2``, so an uncapped install fails with an ImportError.

Every install that can put the two together carries the same cap: the ``parquet`` and
``validator`` extras, and CI's ``official-validation`` job. Lift it in all three places together,
once focus-validator accepts NumPy 2.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_EXTRAS = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "optional-dependencies"
]


def _pyarrow(requirements: list[str]) -> tuple[str, str]:
    """The single pyarrow requirement of an extra, as (upper bound, environment marker)."""
    found = [r for r in requirements if re.match(r"pyarrow\b", r)]
    assert len(found) == 1, requirements
    spec, _, marker = found[0].partition(";")
    bound = re.findall(r"<\s*(\d[\d.]*)", spec)
    assert len(bound) == 1, found[0]
    return bound[0], marker.strip()


def test_validator_extra_caps_pyarrow_like_the_parquet_extra() -> None:
    cap, _ = _pyarrow(_EXTRAS["parquet"])
    validator_cap, marker = _pyarrow(_EXTRAS["validator"])
    assert validator_cap == cap
    # The cap applies wherever focus-validator itself is installed.
    assert marker == "python_version >= '3.12'"


def test_official_validation_job_installs_the_same_cap() -> None:
    cap, _ = _pyarrow(_EXTRAS["parquet"])
    ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    installs = [line for line in ci.splitlines()
                if "uv pip install" in line and "focus-validator==" in line]
    assert len(installs) == 1, installs
    assert f'"pyarrow<{cap}"' in installs[0], installs[0]
