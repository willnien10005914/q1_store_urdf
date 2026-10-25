#!/usr/bin/env python3
"""Build a root-hosted browser demo from public/ + assets/; never copy runtime data."""
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist"


def main():
    for path in (ROOT / "public/index.html", ROOT / "public/app.js",
                 ROOT / "assets/robots/manifest.json", ROOT / "assets/examples/catalog.json"):
        if not path.is_file():
            raise SystemExit(f"Required source is missing: {path.relative_to(ROOT)}")
    if OUTPUT.is_symlink():
        raise SystemExit("Refusing to replace a symlink at dist/")
    with tempfile.TemporaryDirectory(prefix="q1-static-") as temporary:
        stage = Path(temporary) / "dist"
        shutil.copytree(ROOT / "public", stage)
        shutil.copytree(ROOT / "assets", stage / "assets")
        if OUTPUT.exists():
            shutil.rmtree(OUTPUT)
        shutil.move(str(stage), str(OUTPUT))
    files = [path for path in OUTPUT.rglob("*") if path.is_file()]
    size = sum(path.stat().st_size for path in files)
    print(f"Built dist/: {len(files)} files, {size / 1_000_000:.1f} MB")
    print("Preview: python3 -m http.server 8877 --bind 127.0.0.1 --directory dist")
    print("Static mode uses browser-local demo records; YouTube download requires server.py.")


if __name__ == "__main__":
    main()
