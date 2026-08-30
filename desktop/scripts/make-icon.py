"""Génère build/icon.ico depuis le logo de l'app (barres d'onde sur carré sombre).

Reproduit le logo-mark de static/index.html : fond #191713 arrondi,
barres #F5F3EF + une barre verte #0FF302. Multi-tailles 16→256.
Usage : .venv/Scripts/python scripts/make-icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

NIGHT = (0x19, 0x17, 0x13, 255)
CREAM = (0xF5, 0xF3, 0xEF, 255)
GREEN = (0x0F, 0xF3, 0x02, 255)

# Barres du SVG (viewBox 20) : x, y, largeur, hauteur, couleur
BARS = [
    (3.0, 7.0, 2.2, 6.0, CREAM),
    (7.0, 4.0, 2.2, 12.0, CREAM),
    (11.0, 6.5, 2.2, 7.0, GREEN),
    (15.0, 8.5, 2.2, 3.0, CREAM),
]


def render(size: int) -> Image.Image:
    scale = 8  # supersampling pour l'anticrénelage
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    radius = int(s * 8 / 28)  # 8px de rayon pour 28px dans l'UI
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=NIGHT)

    # SVG 14px centré dans 28px → zone utile 50 %, viewBox 20 → unité
    unit = (s * 0.5) / 20.0
    off = s * 0.25
    for x, y, w, h, color in BARS:
        x0, y0 = off + x * unit, off + y * unit
        x1, y1 = x0 + w * unit, y0 + h * unit
        d.rounded_rectangle([x0, y0, x1, y1], radius=(w * unit) / 2, fill=color)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    desktop = Path(__file__).resolve().parent.parent
    out_dir = desktop / "build"
    out_dir.mkdir(exist_ok=True)

    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [render(sz) for sz in sizes]
    ico = out_dir / "icon.ico"
    images[-1].save(ico, format="ICO", sizes=[(sz, sz) for sz in sizes],
                    append_images=images[:-1])

    # PNG 512 pour usage éventuel (Linux/macOS, docs)
    render(512).save(out_dir / "icon.png")
    print(f"OK: {ico}")


if __name__ == "__main__":
    main()
