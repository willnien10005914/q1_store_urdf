#!/usr/bin/env python3
"""Tutorial entrypoint: patch kitless PhysX URDF importer, then run example.py.

Usage (as launched by Q1 Store)::

    python lab3_runner.py example.py --job-dir ... --params ...
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise SystemExit("lab3_runner.py: missing script path (expected example.py …)")
    try:
        import lab3_compat

        lab3_compat.install_import_hook()
    except Exception as exc:  # noqa: BLE001
        print(f"[q1] lab3_compat hook skipped: {exc}", flush=True)

    script = Path(argv[0])
    sys.argv = argv
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
