# -*- mode: python ; coding: utf-8 -*-
# Build:  pyinstaller packaging/desktop/redactgate-desktop.spec --noconfirm   (from the repo root)
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]
ICON_DIR = Path(SPECPATH) / "icons"
is_win = sys.platform.startswith("win")
is_mac = sys.platform == "darwin"

datas = [
    (str(ROOT / "app/desktop/ui"), "app/desktop/ui"),
    (str(ROOT / "app/desktop/assets"), "app/desktop/assets"),
    (str(ROOT / "app/desktop/demo"), "app/desktop/demo"),
    (str(ROOT / "app/packs"), "app/packs"),
    (str(ROOT / "LICENSE"), "."),
]
hidden = [
    "app.redaction.regex_packs", "app.redaction.validators", "app.redaction.merge",
    "app.redaction.vault", "app.redaction.store", "app.redaction.placeholders",
    "app.schemas.entities", "app.desktop.server", "app.desktop.netguard",
    "pymupdf", "lxml.etree", "psutil",
]
excludes = ["tkinter", "fastapi", "uvicorn", "sqlalchemy", "asyncpg", "httpx", "presidio_analyzer", "spacy",
            "numpy", "pandas", "matplotlib", "IPython", "pytest", "PIL", "docx", "openpyxl", "pymupdf4llm",
            "pymupdf_layout", "onnxruntime"]

a = Analysis(
    [str(Path(SPECPATH) / "launch.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hidden,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="RedactGate Desktop" if not sys.platform.startswith("linux") else "redactgate-desktop",
    console=False,
    icon=str(ICON_DIR / ("icon.ico" if is_win else "icon-512.png")) if not is_mac else None,
    version=str(Path(SPECPATH) / "version_info.txt") if is_win else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="redactgate-desktop")
if is_mac:
    app = BUNDLE(
        coll,
        name="RedactGate Desktop.app",
        icon=str(ICON_DIR / "icon.icns") if (ICON_DIR / "icon.icns").exists() else None,
        bundle_identifier="biz.mmix.redactgate.desktop",
        info_plist={"CFBundleShortVersionString": "0.1.0", "NSHighResolutionCapable": True,
                    "LSApplicationCategoryType": "public.app-category.productivity"},
    )
