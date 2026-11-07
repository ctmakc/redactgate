"""Unit tests for the offline desktop anonymizer (no network, no optional deps beyond the desktop set)."""

from __future__ import annotations

import socket

import pytest

from app.desktop import labels as L
from app.desktop import netguard
from app.desktop.engine import Anonymizer, Options, Replacement, normalize, parse_person
from app.desktop.formats import _apply_to_nodes, _csv_cells
from app.desktop.vaultdb import BadPassword, LabelVault, VaultLocked


@pytest.fixture()
def vault(tmp_path):
    v = LabelVault(tmp_path / "v.db")
    v.initialize("secret-1")
    yield v
    v.close()


def test_labels_render_and_inflected_backmapping():
    assert L.render("PERSON", 3, "uk") == "Фізособа-3"
    assert L.render("ORG", 1, "ru") == "Юрлицо-1"
    hits = L.find_labels("з Фізособою-1, счёт Юрлица-2, Person-3's account, РАХУНОК-4")
    assert [(h.category, h.number) for h in hits] == [("PERSON", 1), ("ORG", 2), ("PERSON", 3), ("ACCOUNT", 4)]
    assert L.find_labels("COVID-19 and Ukraine-2024") == []


def test_vault_encrypts_and_rejects_wrong_password(tmp_path, vault):
    pid = vault.create_project("Клієнт Петренко")
    anon = Anonymizer(vault)
    anon.anonymize_text(pid, "Петренко Іван Іванович, IBAN UA213223130000026007233566001", lang="uk", options=Options())
    raw = (tmp_path / "v.db").read_bytes()
    assert "Петренко".encode() not in raw and b"UA213223130000026007233566001" not in raw
    vault.lock()
    with pytest.raises(VaultLocked):
        vault.list_projects()
    with pytest.raises(BadPassword):
        vault.unlock("wrong-pass")
    vault.unlock("secret-1")
    assert vault.get_project(pid)["name"] == "Клієнт Петренко"


def test_same_value_same_label_and_alias_linking(vault):
    pid = vault.create_project("x")
    anon = Anonymizer(vault)
    out, _, _ = anon.anonymize_text(
        pid,
        "Директор Петренко Іван Іванович. Підпис: Петренко І.І. Платник ТОВ «Альфа», а також ТОВ \"АЛЬФА\".",
        lang="uk", options=Options())
    assert out.count("Фізособа-1") == 2 and "Фізособа-2" not in out
    assert out.count("Юрособа-1") == 2


def test_exact_roundtrip_and_canonical_for_ai_text(vault):
    pid = vault.create_project("x")
    anon = Anonymizer(vault)
    src = "в особі директора Петренка Івана Івановича; підпис Петренко І.І.; ЄДРПОУ 38123456"
    out, _, _ = anon.anonymize_text(pid, src, lang="uk", options=Options())
    back, _, _ = anon.restore_text(pid, out)
    assert back == src
    ai, _, _ = anon.restore_text(pid, "Фізособа-1 підписав. Невідома Фізособа-9.")
    assert ai.startswith("Петренко") and "Фізособа-9" in ai


def test_excluded_value_stays(vault):
    pid = vault.create_project("x")
    anon = Anonymizer(vault)
    anon.add_manual(pid, "ORG", "ПриватБанк", excluded=True)
    out, _, _ = anon.anonymize_text(pid, "Рахунок у ПриватБанк, ТОВ «Бета»", lang="uk", options=Options())
    assert "ПриватБанк" in out and "Бета" not in out


def test_amounts_only_when_enabled(vault):
    pid = vault.create_project("x")
    anon = Anonymizer(vault)
    out, _, _ = anon.anonymize_text(pid, "Сума 125 000,00 грн", lang="uk", options=Options())
    assert "125 000,00 грн" in out
    opts = Options(categories=set(L.ALL_CATEGORIES))
    out, _, _ = anon.anonymize_text(pid, "Сума 125 000,00 грн", lang="uk", options=opts)
    assert "Сума-1" in out


def test_apply_to_nodes_keeps_runs():
    texts = ["Контакт: ", "Петренко", " Іван Іванович", ", тел."]
    joined = "".join(texts)
    s = joined.index("Петренко")
    e = s + len("Петренко Іван Іванович")
    assert _apply_to_nodes(texts, [Replacement(s, e, "Фізособа-1")]) == ["Контакт: ", "Фізособа-1", "", ", тел."]


def test_csv_cells_quoted():
    t = 'a;"ТОВ ""Альфа"";x";c\r\n1;2;3\r\n'
    rows = _csv_cells(t, ";")
    assert [t[s:e] for s, e, _q in rows[0]] == ["a", 'ТОВ ""Альфа"";x', "c"]


def test_normalize():
    assert normalize("ACCOUNT", "UA21 3223 1300") == normalize("ACCOUNT", "UA2132231300")
    assert normalize("PHONE", "+38 (050) 123-45-67") == normalize("PHONE", "0501234567")
    assert parse_person("Петренко І.І.")["s"] == parse_person("Петренка Івана Івановича")["s"]


def test_netguard_blocks_outbound_allows_loopback():
    netguard.install()
    with pytest.raises(PermissionError):
        socket.create_connection(("example.com", 443), timeout=2)
    with pytest.raises(PermissionError):
        socket.socket().connect(("1.1.1.1", 53))
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    c = socket.create_connection(srv.getsockname(), timeout=2)
    c.close()
    srv.close()
    assert netguard.status()["blocked"] >= 2
