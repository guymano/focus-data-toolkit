"""Small value objects threaded through the engine row builders.

``ResourceRef`` / ``RowContext`` live in :mod:`focus_data_toolkit.generators.providers.profile`
next to the :class:`ServiceSpec` they reference, so the import graph stays a one-way street
(engine -> providers) with no cycle. Re-exported here for the engine-side importers.
"""

from __future__ import annotations

from focus_data_toolkit.generators.providers.profile import ResourceRef, RowContext

__all__ = ["ResourceRef", "RowContext"]
