"""Host-agnostic path discovery for the Isaac Lab training worker.

The web service may run on Spark, an x86 Intel host with an NVIDIA GPU, or a
remote machine that only serves the UI. Training itself needs a local
``q1_leg_urdf`` checkout with ``scripts/isaac_env.sh``; that checkout is often at
``~/projects/q1_leg_urdf`` on workstation hosts and ``~/will/q1_leg_urdf`` on Spark.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


def leg_root_candidates(store_root: Path | None = None, explicit: str | Path | None = None):
    """Ordered search paths for the biped Isaac Lab repository."""
    seen: set[Path] = set()
    ordered: list[Path] = []

    def add(path):
        if not path:
            return
        resolved = Path(path).expanduser()
        key = resolved.resolve() if resolved.exists() else resolved
        if key in seen:
            return
        seen.add(key)
        ordered.append(resolved)

    add(explicit)
    add(os.environ.get("Q1_LEG_ROOT"))
    home = Path.home()
    add(home / "projects/q1_leg_urdf")
    add(home / "will/q1_leg_urdf")
    add(home / "q1_leg_urdf")
    if store_root is not None:
        root = Path(store_root).resolve()
        add(root.parent / "q1_leg_urdf")
        add(root / "vendor/q1_leg_urdf")
    return ordered


def resolve_leg_root(store_root: Path | None = None, explicit: str | Path | None = None, required: bool = False):
    """Return the first leg checkout that has ``scripts/isaac_env.sh``.

    An explicit path (constructor ``--leg-root`` / ``leg_root=``) wins even when the
    tree is incomplete — tests and operators use that override. Environment and
    well-known locations are searched only when no explicit path is given.

    When nothing matches, returns the preferred default path (env or
    ``~/projects/q1_leg_urdf`` when that parent exists, else ``~/will/q1_leg_urdf``)
    without requiring the tree to exist unless ``required`` is true.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    for candidate in leg_root_candidates(store_root, None):
        if (candidate / "scripts/isaac_env.sh").is_file():
            return candidate.resolve()
    preferred = Path(os.environ["Q1_LEG_ROOT"]).expanduser() if os.environ.get("Q1_LEG_ROOT") else None
    if preferred is None:
        home = Path.home()
        preferred = home / "projects/q1_leg_urdf" if (home / "projects").is_dir() else home / "will/q1_leg_urdf"
    preferred = preferred.resolve()
    if required and not (preferred / "scripts/isaac_env.sh").is_file():
        raise FileNotFoundError(
            "Q1_LEG_ROOT not found. Set Q1_LEG_ROOT to a q1_leg_urdf checkout with scripts/isaac_env.sh "
            f"(searched: {', '.join(str(p) for p in leg_root_candidates(store_root, None))})"
        )
    return preferred


def resolve_gpu_lock(leg_root: Path, data_dir: Path | None = None):
    """File lock shared with other local Isaac training processes."""
    if os.environ.get("Q1_GPU_LOCK"):
        return Path(os.environ["Q1_GPU_LOCK"]).expanduser().resolve()
    # Legacy Spark path keeps mutual exclusion with older training scripts.
    legacy = Path(leg_root).resolve().parent / ".spark-training-gpu.lock"
    if data_dir is not None:
        modern = Path(data_dir).resolve() / "worker/gpu.lock"
        if legacy.exists() and not modern.exists():
            return legacy
        return modern
    return legacy


def nvidia_gpu_name():
    """Best-effort first NVIDIA GPU product name, or None."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            stderr=subprocess.DEVNULL, text=True, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.strip().splitlines()
    return line[0].strip() if line else None


def host_platform_info():
    """Small dict describing the machine that can run (or is running) the worker."""
    info = {
        "hostname": platform.node() or "unknown",
        "machine": platform.machine() or "unknown",
        "system": platform.system() or "unknown",
        "gpu": nvidia_gpu_name(),
    }
    return info
