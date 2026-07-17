"""Single source of truth for the package version.

Kept deliberately import-free so the build backend can read
``[tool.setuptools.dynamic] version = {attr = "focus_data_toolkit._version.__version__"}``
without importing the package (which would pull in optional/runtime modules at build time).
"""

__version__ = "0.3.0"
