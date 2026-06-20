"""Reversible token vault — the core IP of RedactGate.

The vault turns detected sensitive entities into stable, reversible placeholders
(``[[TYPE_hex]]``) before any text leaves the perimeter, and swaps them back on the way
in. It is built so that:

* **Referential consistency** — the same value within one redaction session always maps
  to the same placeholder, so an LLM can still reason about "the same person" across the
  prompt without ever seeing the real value.
* **Reversible, at-rest-encrypted** — each real value is AES-256-GCM encrypted under a
  per-session data-encryption key (DEK) derived deterministically from the master key.
  The ciphertext alone (without the master key) never contains the plaintext.
* **Stream-safe** — :class:`StreamDetokenizer` re-inflates an SSE token stream without
  ever emitting a placeholder that is split across chunks half-swapped.

Crypto:
  * ``dek = HKDF-SHA256(master_key, salt=session_id, info="redactgate-dek-v1")`` (32B).
  * ``fingerprint = HMAC-SHA256(fingerprint_key, "session:type:value")`` (hex).
  * The placeholder token suffix is the fingerprint prefix; on a (rare) collision with a
    different value in the same session it is extended 2 hex chars at a time.

SECURITY: this module never logs, prints, or persists a raw entity value. Persisted state
is ciphertext + a keyed fingerprint (irreversible without ``fingerprint_key``).
"""

from __future__ import annotations

import hmac
import os
from hashlib import sha256

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.redaction.placeholders import (
    MAX_PLACEHOLDER_LEN,
    PLACEHOLDER_RE,
    make_placeholder,
    trailing_partial_len,
)
from app.redaction.store import TokenRecord, TokenStore
from app.schemas.entities import EntitySpan

# AES-GCM standard nonce length (96 bits). Stored as ``nonce || ciphertext``.
_NONCE_LEN = 12
_HKDF_INFO = b"redactgate-dek-v1"
_DEK_LEN = 32


def _derive_dek(master_key: bytes, session_id: str) -> bytes:
    """Deterministically derive the 32-byte per-session DEK from the master key."""
    return HKDF(
        algorithm=SHA256(),
        length=_DEK_LEN,
        salt=session_id.encode(),
        info=_HKDF_INFO,
    ).derive(master_key)


class Vault:
    """Reversible, referential, encrypted tokenizer scoped by ``session_id``."""

    def __init__(
        self, store: TokenStore, *, master_key: bytes, fingerprint_key: bytes
    ) -> None:
        self._store = store
        self._master_key = master_key
        self._fingerprint_key = fingerprint_key

    # ── internals ──────────────────────────────────────────────────────────

    def _fingerprint(self, session_id: str, entity_type: str, value: str) -> str:
        msg = f"{session_id}:{entity_type}:{value}".encode()
        return hmac.new(self._fingerprint_key, msg, sha256).hexdigest()

    async def _allocate_placeholder(
        self, session_id: str, entity_type: str, fingerprint: str
    ) -> str:
        """Pick a placeholder for a NEW fingerprint, extending the hex on collision.

        Starts from the 6-char fingerprint prefix; if that placeholder is already taken in
        this session by a *different* fingerprint, lengthen the hex suffix by 2 chars and
        retry. Bounded by the available fingerprint hex (64 chars from SHA-256).
        """
        length = 6
        while length <= len(fingerprint):
            token_hex = fingerprint[:length]
            placeholder = make_placeholder(entity_type, token_hex)
            existing = await self._store.get_by_placeholder(session_id, placeholder)
            if existing is None or existing.value_fingerprint == fingerprint:
                return placeholder
            length += 2
        # Extremely unlikely: full fingerprint collided. Fall back to the longest form.
        return make_placeholder(entity_type, fingerprint)

    def _encrypt(self, session_id: str, value: str) -> bytes:
        dek = _derive_dek(self._master_key, session_id)
        nonce = os.urandom(_NONCE_LEN)
        ct = AESGCM(dek).encrypt(nonce, value.encode(), None)
        return nonce + ct

    def _decrypt(self, session_id: str, blob: bytes) -> str:
        dek = _derive_dek(self._master_key, session_id)
        nonce, ct = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
        return AESGCM(dek).decrypt(nonce, ct, None).decode()

    # ── public API ─────────────────────────────────────────────────────────

    async def tokenize(
        self, text: str, spans: list[EntitySpan], *, session_id: str
    ) -> str:
        """Replace each ``EntitySpan`` in ``text`` with a reversible placeholder.

        Spans are applied RIGHT-TO-LEFT (descending start) so earlier offsets stay valid
        as the string is rewritten. The same value in the same session always yields the
        same placeholder (referential consistency).
        """
        if not spans:
            return text
        ordered = sorted(spans, key=lambda s: s.start, reverse=True)
        out = text
        for span in ordered:
            value = text[span.start : span.end]
            fp = self._fingerprint(session_id, span.entity_type, value)
            existing = await self._store.get_by_fingerprint(session_id, fp)
            if existing is not None:
                placeholder = existing.placeholder
                await self._store.bump_occurrence(session_id, fp)
            else:
                placeholder = await self._allocate_placeholder(
                    session_id, span.entity_type, fp
                )
                ciphertext = self._encrypt(session_id, value)
                await self._store.put(
                    TokenRecord(
                        session_id=session_id,
                        placeholder=placeholder,
                        entity_type=span.entity_type,
                        value_ciphertext=ciphertext,
                        value_fingerprint=fp,
                    )
                )
            out = out[: span.start] + placeholder + out[span.end :]
        return out

    async def detokenize(self, text: str, *, session_id: str) -> str:
        """Swap every known ``[[TYPE_hex]]`` placeholder back to its real value.

        Unknown placeholders (not in this session's store) are left untouched.
        """
        if "[[" not in text:
            return text

        out: list[str] = []
        last = 0
        for m in PLACEHOLDER_RE.finditer(text):
            placeholder = m.group(0)
            record = await self._store.get_by_placeholder(session_id, placeholder)
            if record is None:
                continue  # leave unknown placeholder as-is (handled by slice below)
            out.append(text[last : m.start()])
            out.append(self._decrypt(session_id, record.value_ciphertext))
            last = m.end()
        out.append(text[last:])
        return "".join(out)

    def stream_detokenizer(self, session_id: str) -> StreamDetokenizer:
        """Create a stateful streaming de-tokenizer bound to this session."""
        return StreamDetokenizer(self, session_id)


class StreamDetokenizer:
    """Stream-safe re-inflation of an SSE token stream.

    Each ``push(chunk)`` appends to an internal buffer, de-tokenizes the part that cannot
    contain a placeholder split across the chunk boundary, and emits only fully-resolved
    text. A trailing partial that *might* still grow into a placeholder is held back (up to
    ``MAX_PLACEHOLDER_LEN``). ``flush()`` resolves and emits the remainder.

    Invariant: ``"".join(all push() returns) + flush() == vault.detokenize(full_text)``.
    """

    def __init__(self, vault: Vault, session_id: str) -> None:
        self._vault = vault
        self._session_id = session_id
        self._buffer = ""

    def _hold_len(self) -> int:
        """How many trailing chars of the buffer to withhold this round.

        Holds back any trailing partial placeholder (``trailing_partial_len``) but never
        more than ``MAX_PLACEHOLDER_LEN``, so a complete placeholder is always emitted.
        """
        n = trailing_partial_len(self._buffer)
        return min(n, MAX_PLACEHOLDER_LEN)

    async def push(self, chunk: str) -> str:
        self._buffer += chunk
        hold = self._hold_len()
        if hold >= len(self._buffer):
            # The whole buffer might still be the start of a placeholder — emit nothing.
            return ""
        emit_src = self._buffer[: len(self._buffer) - hold] if hold else self._buffer
        self._buffer = self._buffer[len(self._buffer) - hold :] if hold else ""
        return await self._vault.detokenize(emit_src, session_id=self._session_id)

    async def flush(self) -> str:
        if not self._buffer:
            return ""
        out = await self._vault.detokenize(self._buffer, session_id=self._session_id)
        self._buffer = ""
        return out
