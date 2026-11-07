"""Local, encrypted label vault — one SQLite file on the user's computer.

Built on RedactGate's :class:`app.redaction.vault.Vault` crypto (per-scope DEK via
HKDF-SHA256, AES-256-GCM for every real value, keyed HMAC-SHA256 fingerprints for
lookups) and the :class:`app.redaction.store.TokenStore` contract. What changes for the
desktop:

* the scope is a **project** (client / case) instead of a single API request, so the same
  value gets the same label across every document of the project;
* labels are sequential and human-readable (``PERSON-1`` → «Фізособа-1»);
* the master key never leaves memory: it is derived from the user's password with scrypt
  and the file stores only a salt and an AES-GCM verifier;
* project names, file names, real values and per-document restore maps are all encrypted.
  The only plaintext is structure: label numbers, categories, counts, timestamps.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.redaction.store import TokenRecord
from app.redaction.vault import Vault

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v BLOB);
CREATE TABLE IF NOT EXISTS project (
  id TEXT PRIMARY KEY, name_ct BLOB NOT NULL, label_lang TEXT NOT NULL DEFAULT 'uk',
  options TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS entity (
  project_id TEXT NOT NULL, category TEXT NOT NULL, n INTEGER NOT NULL,
  fingerprint TEXT NOT NULL, value_ct BLOB NOT NULL, meta_ct BLOB,
  occurrences INTEGER NOT NULL DEFAULT 0, excluded INTEGER NOT NULL DEFAULT 0,
  origin TEXT NOT NULL DEFAULT 'auto', created REAL NOT NULL,
  PRIMARY KEY (project_id, category, n));
CREATE UNIQUE INDEX IF NOT EXISTS entity_fp ON entity(project_id, fingerprint);
CREATE TABLE IF NOT EXISTS variant (
  project_id TEXT NOT NULL, category TEXT NOT NULL, n INTEGER NOT NULL,
  fingerprint TEXT NOT NULL, value_ct BLOB NOT NULL,
  PRIMARY KEY (project_id, fingerprint));
CREATE TABLE IF NOT EXISTS document (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name_ct BLOB NOT NULL, kind TEXT NOT NULL,
  direction TEXT NOT NULL, digest TEXT, restore_ct BLOB, stats TEXT NOT NULL DEFAULT '{}',
  created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS document_digest ON document(project_id, digest);
"""

_VERIFIER = b"redactgate-desktop-vault-v1"
_SCRYPT = {"n": 2**15, "r": 8, "p": 1}


class VaultLocked(RuntimeError):
    pass


class BadPassword(ValueError):
    pass


def _hkdf(key: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=SHA256(), length=32, salt=b"redactgate-desktop", info=info).derive(key)


@dataclass
class Entity:
    project_id: str
    category: str
    n: int
    fingerprint: str
    value: str
    meta: dict = field(default_factory=dict)
    occurrences: int = 0
    excluded: bool = False
    origin: str = "auto"
    variants: dict[str, str] = field(default_factory=dict)  # fingerprint -> surface

    @property
    def key(self) -> tuple[str, int]:
        return (self.category, self.n)


class LabelVault(Vault):
    """RedactGate ``Vault`` with a project scope, sequential labels and SQLite storage."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._lock = threading.RLock()
        self._unlocked = False
        self._cache: dict[str, dict[tuple[str, int], Entity]] = {}
        self._fp_index: dict[str, dict[str, Entity]] = {}
        super().__init__(store=None, master_key=b"\0" * 32, fingerprint_key=b"\0" * 32)  # type: ignore[arg-type]

    # ── password / keys ─────────────────────────────────────────────────────

    def _meta(self, k: str) -> bytes | None:
        row = self._db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, k: str, v: bytes) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)", (k, v))

    @property
    def initialized(self) -> bool:
        return self._meta("verifier") is not None

    @property
    def unlocked(self) -> bool:
        return self._unlocked

    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, maxmem=128 * 1024 * 1024, **_SCRYPT)

    def _install_keys(self, root: bytes) -> None:
        self._master_key = _hkdf(root, b"value-encryption")
        self._fingerprint_key = _hkdf(root, b"value-fingerprint")
        self._meta_key = _hkdf(root, b"metadata-encryption")
        self._unlocked = True

    def initialize(self, password: str) -> None:
        if self.initialized:
            raise RuntimeError("vault already initialized")
        if len(password) < 6:
            raise BadPassword("password too short")
        with self._lock:
            salt = os.urandom(16)
            root = self._derive(password, salt)
            # A random data key wrapped by the password key: changing the password
            # re-wraps it without re-encrypting every value.
            data_key = os.urandom(32)
            kek = _hkdf(root, b"key-wrap")
            nonce = os.urandom(12)
            self._set_meta("salt", salt)
            self._set_meta("wrapped", nonce + AESGCM(kek).encrypt(nonce, data_key, _VERIFIER))
            self._install_keys(data_key)
            self._set_meta("verifier", self._enc_meta(_VERIFIER.decode()))

    def unlock(self, password: str) -> None:
        with self._lock:
            salt = self._meta("salt")
            wrapped = self._meta("wrapped")
            if not salt or not wrapped:
                raise RuntimeError("vault not initialized")
            kek = _hkdf(self._derive(password, salt), b"key-wrap")
            try:
                data_key = AESGCM(kek).decrypt(wrapped[:12], wrapped[12:], _VERIFIER)
            except InvalidTag as exc:
                raise BadPassword("wrong password") from exc
            self._install_keys(data_key)
            if self._dec_meta(self._meta("verifier")) != _VERIFIER.decode():
                self.lock()
                raise BadPassword("vault verifier mismatch")

    def change_password(self, old: str, new: str) -> None:
        if len(new) < 6:
            raise BadPassword("password too short")
        with self._lock:
            salt = self._meta("salt")
            wrapped = self._meta("wrapped")
            kek = _hkdf(self._derive(old, salt), b"key-wrap")
            try:
                data_key = AESGCM(kek).decrypt(wrapped[:12], wrapped[12:], _VERIFIER)
            except InvalidTag as exc:
                raise BadPassword("wrong password") from exc
            salt2 = os.urandom(16)
            kek2 = _hkdf(self._derive(new, salt2), b"key-wrap")
            nonce = os.urandom(12)
            self._set_meta("salt", salt2)
            self._set_meta("wrapped", nonce + AESGCM(kek2).encrypt(nonce, data_key, _VERIFIER))

    def lock(self) -> None:
        with self._lock:
            self._master_key = b"\0" * 32
            self._fingerprint_key = b"\0" * 32
            self._meta_key = b"\0" * 32
            self._cache.clear()
            self._fp_index.clear()
            self._unlocked = False

    def _need(self) -> None:
        if not self._unlocked:
            raise VaultLocked("vault is locked")

    def _enc_meta(self, s: str) -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self._meta_key).encrypt(nonce, s.encode("utf-8"), None)

    def _dec_meta(self, blob: bytes | None) -> str:
        if not blob:
            return ""
        return AESGCM(self._meta_key).decrypt(blob[:12], blob[12:], None).decode("utf-8")

    # ── projects ────────────────────────────────────────────────────────────

    def create_project(self, name: str, label_lang: str = "uk", options: dict | None = None) -> str:
        self._need()
        pid = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO project(id, name_ct, label_lang, options, created, updated) VALUES (?,?,?,?,?,?)",
                (pid, self._enc_meta(name), label_lang, json.dumps(options or {}), now, now),
            )
        return pid

    def list_projects(self) -> list[dict]:
        self._need()
        rows = self._db.execute(
            "SELECT p.id, p.name_ct, p.label_lang, p.options, p.created, p.updated,"
            " (SELECT COUNT(*) FROM entity e WHERE e.project_id=p.id),"
            " (SELECT COUNT(*) FROM document d WHERE d.project_id=p.id)"
            " FROM project p ORDER BY p.updated DESC"
        ).fetchall()
        return [
            {
                "id": r[0], "name": self._dec_meta(r[1]), "label_lang": r[2],
                "options": json.loads(r[3] or "{}"), "created": r[4], "updated": r[5],
                "entities": r[6], "documents": r[7],
            }
            for r in rows
        ]

    def get_project(self, pid: str) -> dict:
        for p in self.list_projects():
            if p["id"] == pid:
                return p
        raise KeyError(pid)

    def update_project(self, pid: str, *, name: str | None = None, label_lang: str | None = None,
                       options: dict | None = None) -> None:
        self._need()
        with self._lock:
            if name is not None:
                self._db.execute("UPDATE project SET name_ct=? WHERE id=?", (self._enc_meta(name), pid))
            if label_lang is not None:
                self._db.execute("UPDATE project SET label_lang=? WHERE id=?", (label_lang, pid))
            if options is not None:
                self._db.execute("UPDATE project SET options=? WHERE id=?", (json.dumps(options), pid))
            self.touch(pid)

    def touch(self, pid: str) -> None:
        self._db.execute("UPDATE project SET updated=? WHERE id=?", (time.time(), pid))

    def delete_project(self, pid: str) -> None:
        self._need()
        with self._lock:
            for t in ("entity", "variant", "document"):
                self._db.execute(f"DELETE FROM {t} WHERE project_id=?", (pid,))
            self._db.execute("DELETE FROM project WHERE id=?", (pid,))
            self._cache.pop(pid, None)
            self._fp_index.pop(pid, None)
            self._db.execute("VACUUM")

    # ── entities ────────────────────────────────────────────────────────────

    def fingerprint(self, pid: str, category: str, norm_value: str) -> str:
        return self._fingerprint(pid, category, norm_value)

    def _load(self, pid: str) -> dict[tuple[str, int], Entity]:
        self._need()
        if pid in self._cache:
            return self._cache[pid]
        ents: dict[tuple[str, int], Entity] = {}
        for cat, n, fp, vct, mct, occ, exc, origin in self._db.execute(
            "SELECT category, n, fingerprint, value_ct, meta_ct, occurrences, excluded, origin"
            " FROM entity WHERE project_id=?",
            (pid,),
        ):
            ents[(cat, n)] = Entity(
                pid, cat, n, fp, self._decrypt(pid, vct),
                json.loads(self._dec_meta(mct) or "{}"), occ, bool(exc), origin,
            )
        for cat, n, fp, vct in self._db.execute(
            "SELECT category, n, fingerprint, value_ct FROM variant WHERE project_id=?", (pid,)
        ):
            e = ents.get((cat, n))
            if e is not None:
                e.variants[fp] = self._decrypt(pid, vct)
        self._cache[pid] = ents
        idx: dict[str, Entity] = {}
        for e in ents.values():
            idx[e.fingerprint] = e
            for vfp in e.variants:
                idx[vfp] = e
        self._fp_index[pid] = idx
        return ents

    def entities(self, pid: str) -> list[Entity]:
        with self._lock:
            return sorted(self._load(pid).values(), key=lambda e: (e.category, e.n))

    def entity(self, pid: str, category: str, n: int) -> Entity | None:
        with self._lock:
            return self._load(pid).get((category, n))

    def by_fingerprint(self, pid: str, fp: str) -> Entity | None:
        with self._lock:
            self._load(pid)
            return self._fp_index[pid].get(fp)

    def add_entity(self, pid: str, category: str, canonical: str, fp: str, *, meta: dict | None = None,
                   origin: str = "auto", excluded: bool = False) -> Entity:
        with self._lock:
            ents = self._load(pid)
            row = self._db.execute(
                "SELECT COALESCE(MAX(n), 0) FROM entity WHERE project_id=? AND category=?", (pid, category)
            ).fetchone()
            n = int(row[0]) + 1
            e = Entity(pid, category, n, fp, canonical, meta or {}, 0, excluded, origin)
            self._db.execute(
                "INSERT INTO entity(project_id, category, n, fingerprint, value_ct, meta_ct, occurrences,"
                " excluded, origin, created) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pid, category, n, fp, self._encrypt(pid, canonical), self._enc_meta(json.dumps(meta or {})),
                 0, int(excluded), origin, time.time()),
            )
            ents[e.key] = e
            self._fp_index[pid][fp] = e
            self.touch(pid)
            return e

    def add_variant(self, e: Entity, fp: str, surface: str) -> None:
        with self._lock:
            if fp == e.fingerprint or fp in e.variants:
                return
            self._db.execute(
                "INSERT OR IGNORE INTO variant(project_id, category, n, fingerprint, value_ct) VALUES (?,?,?,?,?)",
                (e.project_id, e.category, e.n, fp, self._encrypt(e.project_id, surface)),
            )
            e.variants[fp] = surface
            self._fp_index[e.project_id][fp] = e

    def set_canonical(self, e: Entity, value: str, meta: dict | None = None) -> None:
        with self._lock:
            e.value = value
            if meta is not None:
                e.meta = meta
            self._db.execute(
                "UPDATE entity SET value_ct=?, meta_ct=? WHERE project_id=? AND category=? AND n=?",
                (self._encrypt(e.project_id, value), self._enc_meta(json.dumps(e.meta)), e.project_id, e.category, e.n),
            )

    def set_excluded(self, e: Entity, excluded: bool) -> None:
        with self._lock:
            e.excluded = excluded
            self._db.execute(
                "UPDATE entity SET excluded=? WHERE project_id=? AND category=? AND n=?",
                (int(excluded), e.project_id, e.category, e.n),
            )

    def bump(self, counts: dict[tuple[str, str, int], int]) -> None:
        with self._lock:
            for (pid, cat, n), c in counts.items():
                self._db.execute(
                    "UPDATE entity SET occurrences=occurrences+? WHERE project_id=? AND category=? AND n=?",
                    (c, pid, cat, n),
                )
                e = self._cache.get(pid, {}).get((cat, n))
                if e:
                    e.occurrences += c

    def delete_entity(self, e: Entity) -> None:
        with self._lock:
            self._db.execute("DELETE FROM entity WHERE project_id=? AND category=? AND n=?", (e.project_id, e.category, e.n))
            self._db.execute("DELETE FROM variant WHERE project_id=? AND category=? AND n=?", (e.project_id, e.category, e.n))
            self._cache.pop(e.project_id, None)
            self._fp_index.pop(e.project_id, None)

    # ── documents ───────────────────────────────────────────────────────────

    def record_document(self, pid: str, name: str, kind: str, direction: str, digest: str | None,
                        restore: list | None, stats: dict) -> str:
        self._need()
        did = uuid.uuid4().hex[:12]
        with self._lock:
            self._db.execute(
                "INSERT INTO document(id, project_id, name_ct, kind, direction, digest, restore_ct, stats, created)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (did, pid, self._enc_meta(name), kind, direction, digest,
                 self._encrypt(pid, json.dumps(restore, ensure_ascii=False)) if restore is not None else None,
                 json.dumps(stats), time.time()),
            )
            self.touch(pid)
        return did

    def restore_map(self, pid: str, digest: str) -> list | None:
        self._need()
        row = self._db.execute(
            "SELECT restore_ct FROM document WHERE project_id=? AND digest=? AND direction='anon'"
            " ORDER BY created DESC LIMIT 1",
            (pid, digest),
        ).fetchone()
        if not row or not row[0]:
            return None
        return json.loads(self._decrypt(pid, row[0]))

    def documents(self, pid: str) -> list[dict]:
        self._need()
        return [
            {"id": r[0], "name": self._dec_meta(r[1]), "kind": r[2], "direction": r[3],
             "stats": json.loads(r[4] or "{}"), "created": r[5]}
            for r in self._db.execute(
                "SELECT id, name_ct, kind, direction, stats, created FROM document WHERE project_id=?"
                " ORDER BY created DESC",
                (pid,),
            )
        ]

    # ── RedactGate TokenStore view (protocol compatibility) ──────────────────

    def as_token_records(self, pid: str) -> list[TokenRecord]:
        return [
            TokenRecord(session_id=pid, placeholder=f"{e.category}-{e.n}", entity_type=e.category,
                        value_ciphertext=b"", value_fingerprint=e.fingerprint, occurrences=e.occurrences)
            for e in self.entities(pid)
        ]

    def close(self) -> None:
        self._db.close()
