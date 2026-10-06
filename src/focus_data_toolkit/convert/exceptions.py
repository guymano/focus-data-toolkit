"""Conversion exceptions.

Defined in a leaf module so the per-dataset converters can raise them without importing the
package that imports them; :mod:`focus_data_toolkit.convert` re-exports both unchanged.
"""

from __future__ import annotations


class ConversionError(ValueError):
    """Raised when the source cannot be converted."""


class ConversionCancelled(ConversionError):
    """Raised cooperatively when a cancel predicate returns True mid-conversion.

    Subclasses :class:`ConversionError` so existing ``except ConversionError`` handlers
    still clean up (the atomic staging directory is removed on the way out, so nothing is
    published); the CLI catches it first to report a distinct cancelled exit code.
    """
