"""Provider- and version-agnostic generation engine.

The six ``generate_<provider>_focus_<version>`` modules are thin shims that bind a
:class:`~focus_data_toolkit.generators.providers.profile.ProviderProfile` to a
:class:`~focus_data_toolkit.generators.versions.adapter.VersionAdapter` and expose the
public ``generate_csv_bytes`` / ``generate_rows`` / ``main`` API. All shared logic —
determinism helpers, the FOCUS JSON builders, the row scenarios, the scenario ladder and
CSV serialization — lives here, defined exactly once.

Determinism contract: output is a pure function of the ordered sequence of RNG method
calls. The engine preserves the historical call order per scenario, and each provider
callable owns its own draw count, so a given ``(provider, focus_version, rows, seed)``
reproduces the same bytes as before the refactor.
"""
