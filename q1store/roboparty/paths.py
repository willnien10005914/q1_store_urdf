"""Discover local RoboParty / HHTools checkouts."""
from __future__ import annotations

import os
from pathlib import Path

STORE_ROOT = Path(__file__).resolve().parents[2]
SOURCES_FILE = STORE_ROOT / "vendor/roboparty/SOURCES.txt"


def _read_sources_file():
    values = {}
    if not SOURCES_FILE.is_file():
        return values
    for line in SOURCES_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = Path(value.strip()).expanduser()
    return values


def discover_roboparty():
    """Return absolute paths for HHTools, roboparty_train, and RP1 when present."""
    file_paths = _read_sources_file()
    home = Path.home()
    projects = home / "projects"

    def pick(env_key, file_key, *candidates):
        raw = os.environ.get(env_key)
        ordered = []
        if raw:
            ordered.append(Path(raw).expanduser())
        if file_key in file_paths:
            ordered.append(file_paths[file_key])
        ordered.extend(candidates)
        for path in ordered:
            if path and Path(path).exists():
                return Path(path).resolve()
        return None

    hhtools = pick(
        "Q1_HHTOOLS_ROOT", "HHTOOLS",
        projects / "human-humanoid-tools",
        home / "human-humanoid-tools",
        STORE_ROOT / "vendor/human-humanoid-tools",
    )
    train = pick(
        "Q1_ROBOPARTY_TRAIN_ROOT", "ROBOPARTY_TRAIN",
        projects / "roboparty_train",
        home / "roboparty_train",
        STORE_ROOT / "vendor/roboparty_train",
    )
    rp1 = pick(
        "Q1_RP1_ROOT", "RP1",
        projects / "RP1",
        home / "RP1",
        STORE_ROOT / "vendor/RP1",
    )
    return {"hhtools": hhtools, "roboparty_train": train, "rp1": rp1}


def status():
    paths = discover_roboparty()
    hhtools = paths["hhtools"]
    train = paths["roboparty_train"]
    beyond = None
    convert = None
    if train:
        beyond = train / "robolab/robolab/tasks/manager_based/beyondmimic"
        convert = train / "robolab/scripts/tools/hhtools/convert_motion.py"
        if not beyond.is_dir():
            beyond = None
        if not convert.is_file():
            convert = STORE_ROOT / "vendor/roboparty/scripts/convert_motion.py"
            if not convert.is_file():
                convert = None
    return {
        "pipeline": "beyondmimic",
        "docs": {
            "rp1_training": "https://roboparty.com/documents/roboto-01?doc=training%2Findex&lang=en",
            "hhtools": "https://github.com/Roboparty/human-humanoid-tools",
            "roboparty_train": "https://github.com/Roboparty/roboparty_train",
            "rp1": "https://github.com/Roboparty/RP1",
        },
        "paths": {key: str(value) if value else None for key, value in paths.items()},
        "hhtools_available": bool(hhtools and (hhtools / "hhtools").is_dir()),
        "beyondmimic_available": bool(beyond),
        "convert_motion": str(convert) if convert else None,
        "ready_for_import": True,  # CSV import is pure-Python and always available
        "ready_for_bm_npz": bool(convert),
        "note": (
            "Import HHTools CSV/PKL with root trajectory, then train Q1 Leg with "
            "pipeline=beyondmimic. Full RP1.3-BeyondMimic Isaac task remains on "
            "roboparty_train; this store adapts the reference contract for Q1."
        ),
    }
