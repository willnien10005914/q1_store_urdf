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
    q1_preset = None
    for candidate in (
        Path.home() / ".config/hhtools/robots/q1_leg/robot.yaml",
        (hhtools / "configs/robots/q1_leg/robot.yaml") if hhtools else None,
        STORE_ROOT / "vendor/roboparty/q1_leg/robot.yaml",
    ):
        if candidate and Path(candidate).is_file():
            q1_preset = Path(candidate).resolve()
            break
    gvhmr = Path(os.environ["HHTOOLS_GVHMR_ROOT"]).expanduser() if os.environ.get("HHTOOLS_GVHMR_ROOT") else None
    if gvhmr is None:
        for candidate in (Path.home() / "projects/GVHMR", Path.home() / "GVHMR"):
            if candidate.is_dir():
                gvhmr = candidate
                break
    body_models = None
    for candidate in (
        Path(os.environ["HHTOOLS_BODY_MODELS"]).expanduser() if os.environ.get("HHTOOLS_BODY_MODELS") else None,
        (hhtools / "configs/body_models") if hhtools else None,
        Path.home() / ".cache/hhtools/body_models",
    ):
        if candidate and Path(candidate).is_dir():
            body_models = Path(candidate).resolve()
            break
    smplx = bool(body_models and any(body_models.glob("smplx/SMPL*")))
    return {
        "pipeline": "beyondmimic",
        "docs": {
            "rp1_training": "https://roboparty.com/documents/roboto-01?doc=training%2Findex&lang=en",
            "hhtools": "https://github.com/Roboparty/human-humanoid-tools",
            "roboparty_train": "https://github.com/Roboparty/roboparty_train",
            "rp1": "https://github.com/Roboparty/RP1",
        },
        "paths": {key: str(value) if value else None for key, value in paths.items()},
        "q1_preset": str(q1_preset) if q1_preset else None,
        "q1_robot_ready": bool(q1_preset),
        "gvhmr_root": str(gvhmr) if gvhmr else None,
        "body_models": str(body_models) if body_models else None,
        "smplx_weights": smplx,
        "hhtools_available": bool(hhtools and (hhtools / "hhtools").is_dir()),
        "beyondmimic_available": bool(beyond),
        "convert_motion": str(convert) if convert else None,
        "ready_for_import": True,  # CSV import is pure-Python and always available
        "ready_for_bm_npz": bool(convert),
        "ready_for_formal_video_retarget": bool(q1_preset and smplx and gvhmr),
        "note": (
            "Import HHTools CSV/PKL with root trajectory, then train Q1 Leg with "
            "pipeline=beyondmimic. Formal video→Q1 needs q1_leg preset (setup_hhtools_q1.sh), "
            "SMPL-X weights, and GVHMR. Full RP1.3-BeyondMimic Isaac task remains on "
            "roboparty_train; this store adapts the reference contract for Q1."
        ),
    }
