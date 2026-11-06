"""Render the app icon (SVG) to PNG / ICO / ICNS-source PNGs with PyMuPDF + Pillow."""
import io
import sys
from pathlib import Path

import pymupdf
from PIL import Image

root = Path(__file__).resolve().parents[2]
svg = root / "app/desktop/ui/icon.svg"
out = Path(sys.argv[1] if len(sys.argv) > 1 else root / "packaging/desktop/icons")
out.mkdir(parents=True, exist_ok=True)
doc = pymupdf.open(str(svg))
page = doc[0]
big = page.get_pixmap(matrix=pymupdf.Matrix(1024 / page.rect.width, 1024 / page.rect.height), alpha=True)
img = Image.open(io.BytesIO(big.tobytes("png"))).convert("RGBA")
img.save(out / "icon-1024.png")
for s in (16, 32, 48, 64, 128, 256, 512):
    img.resize((s, s), Image.LANCZOS).save(out / f"icon-{s}.png")
img.save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icons ->", out)
