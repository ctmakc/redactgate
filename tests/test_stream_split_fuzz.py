"""Streaming re-inflation under adversarial chunking.

The upstream LLM decides where SSE chunk boundaries fall, so a placeholder can be cut at
ANY character. These tests fuzz chunk boundaries (seeded, deterministic) and assert the
stream invariant ``"".join(push…) + flush() == detokenize(full)``, plus that no fragment of
a placeholder ever reaches the client and that bracket-heavy plain text is not swallowed.
"""

from __future__ import annotations

import json
import random

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.auth import AuthContext
from app.config import settings as cfg
from app.gateway.base import Provider, get_provider, register_provider, reset_provider_cache
from app.gateway.openai import build_chunk, new_id
from app.redaction.placeholders import PLACEHOLDER_RE
from app.redaction.store import InMemoryTokenStore
from app.redaction.vault import Vault
from app.schemas.entities import EntitySpan, PolicyDecision, PolicyMode

VALUES = [
    ("SIN", "193 456 787"),
    ("EMAIL", "jane.doe@example.org"),
    ("IP_ADDRESS_V6", "2001:0db8:85a3:0000:0000:8a2e:0370:7334"),
    ("CREDIT_CARD", "4111 1111 1111 1111"),
]


async def _seed(vault: Vault, session_id: str) -> tuple[str, str]:
    """Tokenize a text containing every value; return (sanitized, original)."""
    parts, spans, pos = [], [], 0
    for etype, value in VALUES:
        prefix = f"{etype.lower()} is "
        parts.append(prefix)
        pos += len(prefix)
        spans.append(EntitySpan(pos, pos + len(value), etype, value))
        parts.append(value)
        pos += len(value)
        parts.append("; ")
        pos += 2
    original = "".join(parts)
    sanitized = await vault.tokenize(original, spans, session_id=session_id)
    return sanitized, original


def _random_chunks(text: str, rng: random.Random, max_len: int) -> list[str]:
    out, i = [], 0
    while i < len(text):
        n = rng.randint(1, max_len)
        out.append(text[i : i + n])
        i += n
    return out


async def _run(det, chunks: list[str]) -> tuple[str, list[str]]:
    emitted = [await det.push(c) for c in chunks]
    emitted.append(await det.flush())
    return "".join(emitted), emitted


@pytest.mark.parametrize("seed", range(40))
async def test_fuzzed_chunk_boundaries_roundtrip(vault, seed):
    sanitized, original = await _seed(vault, "fz")
    rng = random.Random(seed)
    chunks = _random_chunks(sanitized, rng, max_len=rng.choice([1, 2, 3, 5, 8, 13]))
    out, emitted = await _run(vault.stream_detokenizer("fz"), chunks)
    assert out == original
    for piece in emitted:
        assert "[[" not in piece, f"placeholder fragment leaked to client: {piece!r}"


@pytest.mark.parametrize("seed", range(10))
async def test_prepared_detokenizer_fuzz_matches_live(vault, seed):
    sanitized, original = await _seed(vault, "fzp")
    chunks = _random_chunks(sanitized, random.Random(1000 + seed), max_len=4)
    out, _ = await _run(await vault.stream_detokenizer_prepared("fzp"), chunks)
    assert out == original


async def test_adjacent_placeholders_split_between_them(vault):
    a = "193 456 787"
    b = "jane@example.org"
    text = a + b
    spans = [EntitySpan(0, len(a), "SIN", a), EntitySpan(len(a), len(text), "EMAIL", b)]
    sanitized = await vault.tokenize(text, spans, session_id="adj")
    assert "]][[" in sanitized
    cut = sanitized.index("]][[") + 1  # between the two closing brackets
    out, _ = await _run(vault.stream_detokenizer("adj"), [sanitized[:cut], sanitized[cut:]])
    assert out == text


@pytest.mark.parametrize(
    "plain",
    [
        "array[[1, 2], [3]] and dict['k']",
        "markdown [[wiki link]] stays",
        "[[NOT_A_TOKEN]] has no hex",
        "[[SIN_zzzz]] bad hex",
        "trailing open [[",
        "trailing [",
        "[[SIN_abcdef]] unknown to this session",
    ],
)
async def test_bracket_heavy_plain_text_is_not_swallowed(vault, plain):
    for size in (1, 2, 7):
        chunks = [plain[i : i + size] for i in range(0, len(plain), size)]
        out, _ = await _run(vault.stream_detokenizer("plain"), chunks)
        assert out == plain


async def test_placeholder_split_only_at_flush(vault):
    sanitized, original = await _seed(vault, "fl")
    sanitized, original = sanitized.removesuffix("; "), original.removesuffix("; ")
    assert sanitized.endswith("]]")
    # Stream ends one char short of the final placeholder: it must be HELD (not emitted
    # half-swapped), then the last "]" arrives and the remainder resolves on flush().
    det = vault.stream_detokenizer("fl")
    head = await det.push(sanitized[:-1])
    assert not head.endswith(sanitized[-12:-1])
    assert "[[" not in head
    rest = await det.push(sanitized[-1:])
    tail = await det.flush()
    assert head + rest + tail == original


# ── Through the real SSE route ───────────────────────────────────────────────────


class _CharStreamProvider(Provider):
    """Streams the sanitized user message back ONE CHARACTER per SSE chunk."""

    name = "charstream"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self.seen: list[dict] = []

    async def complete(self, payload):  # pragma: no cover - not used
        raise NotImplementedError

    async def stream(self, payload):
        self.seen.append(payload)
        text = payload["messages"][-1]["content"]
        cid = new_id()
        yield build_chunk(model="m", role="assistant", content="", completion_id=cid)
        for ch in text:
            yield build_chunk(model="m", content=ch, completion_id=cid)
        yield build_chunk(model="m", finish_reason="stop", completion_id=cid)


@pytest.fixture
def char_client():
    import app.gateway.base as gw_base
    from app.main import create_app

    register_provider("charstream", lambda s: _CharStreamProvider(s))
    reset_provider_cache()
    provider = get_provider("charstream", cfg)
    app = create_app()
    store = InMemoryTokenStore()

    class _Sink:
        async def record(self, **_kw):
            return None

    app.dependency_overrides[deps.get_token_store] = lambda: store
    app.dependency_overrides[deps.get_audit_sink] = lambda: _Sink()
    app.dependency_overrides[deps.get_policy_decision] = lambda: PolicyDecision(
        mode=PolicyMode.TOKENIZE, blocked=False
    )
    app.dependency_overrides[deps.get_active_provider] = lambda: provider
    app.dependency_overrides[deps.get_auth] = lambda: AuthContext("t", "p", "k")
    try:
        yield TestClient(app), provider
    finally:
        gw_base._REGISTRY.pop("charstream", None)
        reset_provider_cache()


def test_sse_route_reinflates_one_char_chunks(char_client, monkeypatch):
    import app.routes.proxy as proxy_mod

    async def _no_audit(**_kw):
        return None

    monkeypatch.setattr(proxy_mod, "_safe_audit_fresh", _no_audit)
    client, provider = char_client
    msg = "Card 4111 1111 1111 1111, SIN 193 456 787, mail jane.doe@example.org"
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "m", "stream": True, "messages": [{"role": "user", "content": msg}]},
    )
    assert resp.status_code == 200
    sent = provider.seen[-1]["messages"][-1]["content"]
    for raw in ("4111 1111 1111 1111", "193 456 787", "jane.doe@example.org"):
        assert raw not in sent
    assert len(PLACEHOLDER_RE.findall(sent)) == 3

    pieces: list[str] = []
    for line in resp.text.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        chunk = json.loads(line[6:])
        content = (chunk.get("choices") or [{}])[0].get("delta", {}).get("content")
        if content:
            assert "[[" not in content
            pieces.append(content)
    assert "".join(pieces) == msg
    assert resp.text.rstrip().endswith("data: [DONE]")
