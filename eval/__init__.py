"""RedactGate fidelity / detection evaluation harness.

The ``eval`` package is intentionally importable with **zero optional dependencies**:
it exercises the regex-only detection path against bundled, hand-labeled golden sets and
(optionally, when a provider is configured and reachable) measures answer fidelity of the
redact -> complete -> reinflate round-trip via an LLM judge.

Nothing in this package logs, prints or persists a raw entity value — only entity *type*
counts and aggregate scores cross any boundary.
"""

from __future__ import annotations

__all__ = ["harness"]
