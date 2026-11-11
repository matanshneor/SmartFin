"""Compare two screenshot runs pixel by pixel.

    python tests/visual/diff.py before after

Exits 1 when any shot differs, and writes side-by-side images of the changed
pairs to tests/visual/out/diff-<before>-<after>/.
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops

OUT = Path(__file__).resolve().parent / "out"


def main(a, b):
    da, db = OUT / a, OUT / b
    dest = OUT / f"diff-{a}-{b}"
    changed, missing = [], []
    for fa in sorted(da.glob("*.png")):
        fb = db / fa.name
        if not fb.exists():
            missing.append(fa.name)
            continue
        ia, ib = Image.open(fa).convert("RGB"), Image.open(fb).convert("RGB")
        if ia.size != ib.size or ImageChops.difference(ia, ib).getbbox():
            changed.append(fa.name)
            dest.mkdir(parents=True, exist_ok=True)
            w = ia.width + ib.width + 10
            side = Image.new("RGB", (w, max(ia.height, ib.height)), (128, 128, 128))
            side.paste(ia, (0, 0))
            side.paste(ib, (ia.width + 10, 0))
            side.save(dest / fa.name)
    for name in changed:
        print("changed:", name)
    for name in missing:
        print("missing in", b + ":", name)
    print(f"{len(changed)} changed, {len(missing)} missing")
    return 1 if changed or missing else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
