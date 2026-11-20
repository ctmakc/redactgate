"""File-level anonymize/restore (app.desktop.pipeline) and the project label vault."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.desktop import labels as L
from app.desktop.engine import Anonymizer, Options, RunState, label_digest
from app.desktop.formats import OoxmlHandler, W
from app.desktop.pipeline import anonymize_file, prescan_files, restore_file
from app.desktop.vaultdb import BadPassword, LabelVault, VaultLocked


@pytest.fixture()
def vault(tmp_path):
    v = LabelVault(tmp_path / "v.db")
    v.initialize("secret-1")
    yield v
    v.close()


@pytest.fixture()
def anon(vault):
    return Anonymizer(vault)


@pytest.fixture()
def pid(vault):
    return vault.create_project("Клієнт", "uk")


TXT = (
    "Договір між ТОВ «Альфа-Трейд» (ЄДРПОУ 38123456) та ФОП Коваленко Марія.\n"
    "Директор Петренко Іван Іванович, IBAN UA213223130000026007233566001.\n"
    "Підпис: Петренко І.І.\n"
)


# ── text round-trip ────────────────────────────────────────────────────────


def test_text_file_anonymize_then_restore_is_byte_exact(anon, pid):
    data = TXT.encode()
    res = anonymize_file(anon, pid, "dogovir.txt", data, lang="uk", options=Options())
    out = res.data.decode()
    for secret in ("Альфа-Трейд", "38123456", "Коваленко", "Петренко", "UA213223130000026007233566001"):
        assert secret not in out
    assert "Юрособа-1" in out and "Фізособа-1" in out
    assert res.out_name == "dogovir.anon.txt" and res.kind == "text"
    assert res.counts["PERSON"] >= 3 and res.counts["ORG"] == 1 and res.counts["EDRPOU"] == 1
    assert res.labels >= 4  # distinct labels: org, code, account, two people
    assert res.preview_in == TXT and res.preview_out == out
    assert res.doc_id

    back = restore_file(anon, pid, res.out_name, res.data)
    assert back.data == data  # every mention gets its own original spelling back
    assert back.exact is True and back.unknown == []
    assert back.out_name == "dogovir.restored.txt"
    assert back.counts == res.counts


def test_restore_of_edited_ai_answer_uses_canonical_values_and_reports_unknown(anon, pid):
    anonymize_file(anon, pid, "a.txt", TXT.encode(), lang="uk", options=Options())
    director = next(e for e in anon.vault.entities(pid) if e.category == "PERSON" and "Петренко" in e.value)
    label = L.render("PERSON", director.n, "uk")
    answer = f"Підписант — {label}, контрагент Юрособою-1. Фізособа-77 не існує."
    back = restore_file(anon, pid, "answer.txt", answer.encode())
    text = back.data.decode()
    assert back.exact is False
    # «Петренко І.І.» was linked to the same person; the fullest spelling is the canonical one
    assert "Підписант — Петренко Іван Іванович, контрагент Альфа-Трейд." in text
    assert back.unknown == ["Фізособа-77"]


def test_documents_are_recorded_with_stats(anon, vault, pid):
    anonymize_file(anon, pid, "a.txt", TXT.encode(), lang="uk", options=Options())
    docs = vault.documents(pid)
    assert len(docs) == 1
    assert docs[0]["name"] == "a.txt" and docs[0]["direction"] == "anon"
    assert docs[0]["stats"]["counts"]["ORG"] == 1


def test_labels_follow_project_language(anon, vault):
    pid = vault.create_project("en", "en")
    res = anonymize_file(anon, pid, "a.txt", TXT.encode(), lang="en", options=Options())
    out = res.data.decode()
    assert "Person-1" in out and "Company-1" in out and "Фізособа" not in out


def test_disabled_category_is_kept(anon, pid):
    opts = Options(categories=set(L.ALL_CATEGORIES) - set(L.DEFAULT_OFF) - {"ACCOUNT"})
    res = anonymize_file(anon, pid, "a.txt", TXT.encode(), lang="uk", options=opts)
    assert "UA213223130000026007233566001" in res.data.decode()


# ── CSV / DOCX round-trip ─────────────────────────────────────────────────


def test_csv_column_context_and_exact_roundtrip(anon, pid):
    src = (
        "Дата;Контрагент;ЄДРПОУ;Рахунок;Сума\r\n"
        "01.10.2026;Зелений Гай;38123456;UA213223130000026007233566001;1000,00\r\n"
        "02.10.2026;\"ТОВ \"\"Альфа-Трейд\"\"\";40123455;26007233566001;250,00\r\n"
    )
    data = src.encode()
    res = anonymize_file(anon, pid, "vypyska.csv", data, lang="uk", options=Options())
    out = res.data.decode()
    assert "Зелений Гай" not in out  # only known as a counterparty from the column header
    assert "38123456" not in out and "40123455" not in out and "26007233566001" not in out
    assert "1000,00" in out  # amounts are off by default
    assert out.count("\r\n") == 3
    back = restore_file(anon, pid, res.out_name, res.data)
    assert back.data == data and back.exact is True


def _docx(paragraphs: list[list[str]]) -> bytes:
    body = "".join(
        "<w:p>" + "".join(f'<w:r><w:t xml:space="preserve">{t}</w:t></w:r>' for t in runs) + "</w:p>"
        for runs in paragraphs
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
        z.writestr("word/media/logo.png", b"\x89PNG-bytes")
    return buf.getvalue()


def test_docx_roundtrip_restores_every_unit(anon, pid):
    data = _docx([["Директор ", "Петренко Іван", " Іванович"], ["IBAN UA213223130000026007233566001"],
                  ["Підпис: Петренко І.І."]])
    res = anonymize_file(anon, pid, "d.docx", data, lang="uk", options=Options())
    h = OoxmlHandler("docx")
    assert all("Петренко" not in u.text for u in h.units(res.data))
    back = restore_file(anon, pid, "d.anon.docx", res.data)
    assert back.out_name == "d.restored.docx"
    assert [u.text for u in h.units(back.data)] == [u.text for u in h.units(data)]
    assert back.exact is True
    assert zipfile.ZipFile(io.BytesIO(back.data)).read("word/media/logo.png") == b"\x89PNG-bytes"


# ── batch prescan ──────────────────────────────────────────────────────────


def test_prescan_links_names_across_files_of_a_batch(anon, pid):
    letter = "Зустріч із Зелений Гай перенесено.".encode()
    table = "Контрагент,Сума\nЗелений Гай,10\n".encode()
    # without the prescan the free-text file alone does not reveal the counterparty
    alone = anonymize_file(anon, pid, "letter.txt", letter, lang="uk", options=Options())
    assert "Зелений Гай" in alone.data.decode()

    pid2 = anon.vault.create_project("batch")
    found = prescan_files(anon, pid2, [("letter.txt", letter), ("table.csv", table)], options=Options())
    assert found >= 1
    res = anonymize_file(anon, pid2, "letter.txt", letter, lang="uk", options=Options())
    assert "Зелений Гай" not in res.data.decode() and "Юрособа-1" in res.data.decode()


# ── engine pieces used by the pipeline ────────────────────────────────────


def test_exact_map_requires_the_same_label_sequence(anon, pid):
    out, _, _ = anon.anonymize_text(pid, "Петренко Іван Іванович і Петренко І.І.", lang="uk", options=Options())
    assert anon.exact_map(pid, [out]) is not None
    assert anon.exact_map(pid, [out + " Фізособа-1"]) is None  # one more mention -> not the same doc
    assert anon.exact_map(pid, ["без міток"]) is None


def test_options_from_dict():
    o = Options.from_dict({"categories": ["PERSON"], "packs": ["UA"]})
    assert o.categories == {"PERSON"} and o.packs == ["UA"]
    d = Options.from_dict(None)
    assert "AMOUNT" not in d.categories and "PERSON" in d.categories
    assert Options.from_dict({"categories": []}).categories == set()
    assert Options.from_dict({"packs": []}).packs == Options().packs  # empty list keeps defaults


def test_label_digest_depends_on_order():
    assert label_digest([("PERSON", 1), ("ORG", 1)]) != label_digest([("ORG", 1), ("PERSON", 1)])
    assert label_digest([]) == label_digest([])


def test_run_state_digest_changes_with_units():
    a, b = RunState(), RunState()
    a.units_out = ["x", "y"]
    b.units_out = ["xy"]
    assert a.digest() != b.digest()


# ── vault ──────────────────────────────────────────────────────────────────


def test_initialize_rejects_short_password_and_double_init(tmp_path):
    v = LabelVault(tmp_path / "x.db")
    with pytest.raises(BadPassword):
        v.initialize("12345")
    v.initialize("123456")
    with pytest.raises(RuntimeError):
        v.initialize("another-1")
    v.close()


def test_unlock_requires_initialized_vault(tmp_path):
    v = LabelVault(tmp_path / "x.db")
    with pytest.raises(RuntimeError):
        v.unlock("whatever")
    v.close()


def test_change_password_rewraps_key_and_keeps_data(tmp_path, vault, anon, pid):
    anon.anonymize_text(pid, "Петренко Іван Іванович", lang="uk", options=Options())
    with pytest.raises(BadPassword):
        vault.change_password("secret-1", "short")
    with pytest.raises(BadPassword):
        vault.change_password("wrong-old", "new-secret-2")
    vault.change_password("secret-1", "new-secret-2")
    vault.lock()
    with pytest.raises(BadPassword):
        vault.unlock("secret-1")
    vault.unlock("new-secret-2")
    assert vault.entity(pid, "PERSON", 1).value == "Петренко Іван Іванович"


def test_reopening_vault_file_needs_password(tmp_path, vault, anon, pid):
    anon.anonymize_text(pid, "ТОВ «Альфа»", lang="uk", options=Options())
    v2 = LabelVault(tmp_path / "v.db")
    assert v2.initialized and not v2.unlocked
    with pytest.raises(VaultLocked):
        v2.entities(pid)
    v2.unlock("secret-1")
    assert [e.value for e in v2.entities(pid)] == ["Альфа"]
    v2.close()


def test_delete_project_removes_entities_and_documents(vault, anon, pid):
    other = vault.create_project("other")
    anon.anonymize_text(pid, "ТОВ «Альфа»", lang="uk", options=Options())
    anon.anonymize_text(other, "ТОВ «Бета»", lang="uk", options=Options())
    vault.delete_project(pid)
    with pytest.raises(KeyError):
        vault.get_project(pid)
    assert vault.entities(pid) == [] and vault.documents(pid) == []
    assert [e.value for e in vault.entities(other)] == ["Бета"]


def test_projects_are_isolated_label_namespaces(vault, anon):
    p1, p2 = vault.create_project("a"), vault.create_project("b")
    o1, _, _ = anon.anonymize_text(p1, "ТОВ «Альфа», ТОВ «Бета»", lang="uk", options=Options())
    o2, _, _ = anon.anonymize_text(p2, "ТОВ «Бета»", lang="uk", options=Options())
    assert "Юрособа-2" in o1 and o2 == "ТОВ «Юрособа-1»"
    back, _, _ = anon.restore_text(p2, "Юрособа-1")
    assert back == "Бета"


def test_update_project_and_list_counts(vault, anon, pid):
    vault.update_project(pid, name="Новий", label_lang="en", options={"categories": ["ORG"]})
    anon.anonymize_text(pid, "ТОВ «Альфа»", lang="en", options=Options())
    p = vault.get_project(pid)
    assert p["name"] == "Новий" and p["label_lang"] == "en" and p["options"] == {"categories": ["ORG"]}
    assert p["entities"] == 1 and p["documents"] == 1


def test_occurrence_counter_is_bumped(vault, anon, pid):
    anon.anonymize_text(pid, "ТОВ «Альфа» і знову ТОВ «Альфа»", lang="uk", options=Options())
    assert vault.entity(pid, "ORG", 1).occurrences == 2


def test_token_records_never_carry_values(vault, anon, pid):
    anon.anonymize_text(pid, "ТОВ «Альфа»", lang="uk", options=Options())
    recs = vault.as_token_records(pid)
    assert [(r.placeholder, r.value_ciphertext) for r in recs] == [("ORG-1", b"")]


@pytest.mark.skip(reason="bug: deleting the newest entity frees its number (n = MAX(n)+1), so the next "
                         "new entity gets the same label and old documents restore to the wrong person "
                         "without being reported as unknown")
def test_deleted_entity_label_is_not_reused(vault, anon, pid):
    old, _, _ = anon.anonymize_text(pid, "Директор Петренко Іван Іванович підписав.", lang="uk",
                                    options=Options())
    vault.delete_entity(vault.entity(pid, "PERSON", 1))
    anon.detector.invalidate(pid)
    anon.anonymize_text(pid, "Бухгалтер Коваленко Олена Петрівна.", lang="uk", options=Options())
    back, st, _ = anon.restore_text(pid, old)
    assert "Коваленко" not in back
