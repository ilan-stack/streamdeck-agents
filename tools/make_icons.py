"""Render the default icon set into icons/: rounded gradient tiles with a white SF Symbol (needs bin/sfsymbol).
Icons are generated on your Mac rather than shipped, so no third-party logos or Apple artwork live in the repo.
Add your own PNGs to icons/ (or drop them on a key in the dashboard) for app or brand logos."""
import os
import subprocess
import sys
from PIL import Image, ImageDraw

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS, SF = os.path.join(HOME, "icons"), os.path.join(HOME, "bin", "sfsymbol")
SET = {   # name: (SF Symbol, top colour, bottom colour)
    "volume": ("speaker.wave.2.fill", "#5b5b66", "#3a3a42"), "muted": ("speaker.slash.fill", "#ff5a52", "#c0392b"),
    "layout": ("keyboard", "#ff7ab6", "#e0407f"), "screenshot": ("camera.viewfinder", "#5f6bff", "#3a3fd6"),
    "record": ("record.circle", "#ff5a52", "#d61f1f"), "calendar": ("calendar", "#ff5a52", "#e0281f"),
    "focus": ("timer", "#ffa43a", "#f27a0c"), "claude": ("sparkles", "#e07a52", "#c2562e"),
    "translate": ("translate", "#3d8bff", "#0a5cf0"), "improve": ("wand.and.stars", "#6e6bff", "#4a3fe0"),
    "mic": ("mic.fill", "#b76bff", "#8a3ff0"), "status": ("waveform.path.ecg", "#34d36b", "#1f9e4b"),
}


def tile(sym, top, bottom, out, S=512):
    tmp = out + ".sym.png"
    subprocess.run([SF, sym, tmp], check=True, capture_output=True)
    hexrgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
    t, b = hexrgb(top), hexrgb(bottom)
    grad = Image.new("RGBA", (S, S))
    d = ImageDraw.Draw(grad)
    for y in range(S):
        f = y / (S - 1)
        d.line([(0, y), (S, y)], fill=tuple(int(t[i] + (b[i] - t[i]) * f) for i in range(3)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * .225), fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    g = Image.open(tmp).convert("RGBA")
    g = g.crop(g.getbbox())
    g.thumbnail((int(S * .56),) * 2, Image.LANCZOS)
    img.paste(g, ((S - g.width) // 2, (S - g.height) // 2), g)
    P = int(S * 1.25)
    c = Image.new("RGBA", (P, P), (0, 0, 0, 0))
    c.paste(img, ((P - S) // 2,) * 2, img)
    c.save(out)
    os.remove(tmp)


if __name__ == "__main__":
    if not os.path.exists(SF):
        sys.exit("bin/sfsymbol missing - run tools/build_native.sh first")
    os.makedirs(ICONS, exist_ok=True)
    for name, (sym, top, bottom) in SET.items():
        tile(sym, top, bottom, os.path.join(ICONS, f"{name}.png"))
    print(f"rendered {len(SET)} icons into icons/")
