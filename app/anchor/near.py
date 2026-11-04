"""Minimal NEAR JSON-RPC client: view calls and signed function-call transactions.

Dependency-light on purpose (``httpx`` + ``PyNaCl``, both already in the RedactGate
stack): base58, the borsh layout of a NEAR ``Transaction`` and ed25519 signing are
implemented here in ~150 lines, so the gateway does not need near-cli, Node or a Rust
toolchain at runtime.

Only the actions RedactGate uses are encoded: CreateAccount, DeployContract,
FunctionCall, Transfer and AddKey. Layout follows nearcore ``core/primitives``
(``Transaction`` V0, ``Action`` enum indices 0..5).
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from dataclasses import dataclass, field
from typing import Any

import httpx

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

RPC_URLS = {
    "testnet": "https://rpc.testnet.near.org",
    "mainnet": "https://rpc.mainnet.near.org",
}
EXPLORER_TX = {
    "testnet": "https://testnet.nearblocks.io/txns/{}",
    "mainnet": "https://nearblocks.io/txns/{}",
}

TGAS = 10**12


# ── base58 ──────────────────────────────────────────────────────────────────────


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(data) - len(data.lstrip(b"\0"))
    return "1" * pad + out


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\0" * pad + body


# ── borsh primitives ────────────────────────────────────────────────────────────


def _u8(v: int) -> bytes:
    return struct.pack("<B", v)


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _u64(v: int) -> bytes:
    return struct.pack("<Q", v)


def _u128(v: int) -> bytes:
    return v.to_bytes(16, "little")


def _bytes(b: bytes) -> bytes:
    return _u32(len(b)) + b


def _str(s: str) -> bytes:
    return _bytes(s.encode())


def _pubkey(pk: bytes) -> bytes:
    return _u8(0) + pk  # KeyType::ED25519


# ── actions ─────────────────────────────────────────────────────────────────────


def create_account() -> bytes:
    return _u8(0)


def deploy_contract(code: bytes) -> bytes:
    return _u8(1) + _bytes(code)


def function_call(method: str, args: dict | bytes, *, gas: int = 30 * TGAS, deposit: int = 0) -> bytes:
    raw = args if isinstance(args, bytes) else json.dumps(args, separators=(",", ":")).encode()
    return _u8(2) + _str(method) + _bytes(raw) + _u64(gas) + _u128(deposit)


def transfer(deposit: int) -> bytes:
    return _u8(3) + _u128(deposit)


def add_full_access_key(pk: bytes) -> bytes:
    return _u8(5) + _pubkey(pk) + _u64(0) + _u8(1)


def add_function_call_key(
    pk: bytes, receiver_id: str, method_names: list[str], allowance: int | None = None
) -> bytes:
    perm = _u8(0)  # AccessKeyPermission::FunctionCall
    perm += (_u8(1) + _u128(allowance)) if allowance is not None else _u8(0)
    perm += _str(receiver_id) + _u32(len(method_names)) + b"".join(_str(m) for m in method_names)
    return _u8(5) + _pubkey(pk) + _u64(0) + perm


def serialize_transaction(
    *, signer_id: str, public_key: bytes, nonce: int, receiver_id: str, block_hash: bytes,
    actions: list[bytes],
) -> bytes:
    return (
        _str(signer_id)
        + _pubkey(public_key)
        + _u64(nonce)
        + _str(receiver_id)
        + block_hash
        + _u32(len(actions))
        + b"".join(actions)
    )


# ── keys ────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class KeyPair:
    """ed25519 key in NEAR's ``ed25519:<base58>`` text form (64-byte secret, 32-byte public)."""

    secret: bytes
    public: bytes

    @classmethod
    def from_string(cls, s: str) -> KeyPair:
        raw = b58decode(s.split(":", 1)[-1])
        if len(raw) == 64:
            return cls(secret=raw, public=raw[32:])
        if len(raw) == 32:  # bare seed
            from nacl.signing import SigningKey

            sk = SigningKey(raw)
            return cls(secret=raw + bytes(sk.verify_key), public=bytes(sk.verify_key))
        raise ValueError("unexpected NEAR private key length")

    @classmethod
    def generate(cls) -> KeyPair:
        from nacl.signing import SigningKey

        sk = SigningKey.generate()
        pk = bytes(sk.verify_key)
        return cls(secret=bytes(sk) + pk, public=pk)

    @property
    def public_key_str(self) -> str:
        return "ed25519:" + b58encode(self.public)

    @property
    def secret_key_str(self) -> str:
        return "ed25519:" + b58encode(self.secret)

    def sign(self, msg: bytes) -> bytes:
        from nacl.signing import SigningKey

        return SigningKey(self.secret[:32]).sign(msg).signature


def sign_transaction(tx: bytes, key: KeyPair) -> bytes:
    sig = key.sign(hashlib.sha256(tx).digest())
    return tx + _u8(0) + sig


def tx_hash(tx: bytes) -> str:
    return b58encode(hashlib.sha256(tx).digest())


# ── RPC ─────────────────────────────────────────────────────────────────────────


class NearRpcError(RuntimeError):
    pass


@dataclass
class NearClient:
    network: str = "testnet"
    rpc_url: str | None = None
    timeout: float = 30.0
    _http: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.rpc_url = self.rpc_url or RPC_URLS[self.network]

    async def _rpc(self, method: str, params: Any) -> Any:
        body = {"jsonrpc": "2.0", "id": "rg", "method": method, "params": params}
        if self._http is not None:
            resp = await self._http.post(self.rpc_url, json=body)
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.post(self.rpc_url, json=body)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise NearRpcError(json.dumps(data["error"])[:800])
        return data["result"]

    async def view(self, contract_id: str, method: str, args: dict | None = None) -> Any:
        res = await self._rpc(
            "query",
            {
                "request_type": "call_function",
                "finality": "final",
                "account_id": contract_id,
                "method_name": method,
                "args_base64": base64.b64encode(json.dumps(args or {}).encode()).decode(),
            },
        )
        if "error" in res:
            raise NearRpcError(res["error"])
        raw = bytes(res["result"])
        return json.loads(raw) if raw else None

    async def access_key_nonce(self, account_id: str, public_key: str) -> tuple[int, bytes]:
        res = await self._rpc(
            "query",
            {
                "request_type": "view_access_key",
                "finality": "final",
                "account_id": account_id,
                "public_key": public_key,
            },
        )
        if "error" in res:
            raise NearRpcError(res["error"])
        return int(res["nonce"]), b58decode(res["block_hash"])

    async def send(
        self, *, signer_id: str, key: KeyPair, receiver_id: str, actions: list[bytes],
        wait_until: str = "FINAL",
    ) -> dict:
        nonce, block_hash = await self.access_key_nonce(signer_id, key.public_key_str)
        tx = serialize_transaction(
            signer_id=signer_id, public_key=key.public, nonce=nonce + 1,
            receiver_id=receiver_id, block_hash=block_hash, actions=actions,
        )
        signed = sign_transaction(tx, key)
        res = await self._rpc(
            "send_tx",
            {"signed_tx_base64": base64.b64encode(signed).decode(), "wait_until": wait_until},
        )
        status = res.get("status", {})
        if isinstance(status, dict) and "Failure" in status:
            raise NearRpcError(json.dumps(status["Failure"])[:800])
        res["_tx_hash"] = tx_hash(tx)
        return res

    def explorer_tx(self, h: str) -> str:
        return EXPLORER_TX.get(self.network, EXPLORER_TX["testnet"]).format(h)


def success_json(result: dict) -> Any:
    """Decode the JSON return value of a function-call transaction outcome."""
    val = result.get("status", {}).get("SuccessValue")
    if not val:
        return None
    return json.loads(base64.b64decode(val))
