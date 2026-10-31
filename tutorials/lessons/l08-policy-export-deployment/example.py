#!/usr/bin/env python3
"""Lesson 08 — export an RSL-RL policy to ONNX / TorchScript and write the Q1 policy bundle.

    python3 example.py --job-dir <dir> --params <dir>/params.json

Only torch is required. If no lesson-06 checkpoint exists a randomly initialised actor with the same
architecture is used and result.json is marked demo=true.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--job-dir", type=Path, required=True)
parser.add_argument("--params", type=Path, required=True)
args = parser.parse_args()
P = json.loads(args.params.read_text())
JOB = args.job_dir
JOB.mkdir(parents=True, exist_ok=True)
T0 = time.monotonic()


def progress(phase, iteration, total, **extra):
    elapsed = time.monotonic() - T0
    (JOB / "progress.json").write_text(json.dumps({"phase": phase, "iteration": iteration, "max_iterations": total,
                                                    "elapsed_s": round(elapsed, 1), "eta_s": None, "updated_at": time.time(), **extra}))


progress("starting", 0, 4, message="importing torch")
try:
    import torch
    import torch.nn as nn
except ImportError as exc:  # the store's sandbox python may lack torch; say so instead of faking an export
    (JOB / "result.json").write_text(json.dumps({"error": f"torch not available: {exc}", "passed": False}))
    progress("failed", 0, 4, message="torch missing")
    raise SystemExit(1)

JOINTS = list(P.get("control_joints", []))
IS_WHEEL = P.get("robot") == "wheel"
ACT_DIM = len(JOINTS)
OBS_LAYOUT = [{"name": "base_lin_vel", "dim": 3, "scale": 1.0}, {"name": "base_ang_vel", "dim": 3, "scale": 0.25},
              {"name": "projected_gravity", "dim": 3, "scale": 1.0}, {"name": "velocity_commands", "dim": 3, "scale": 1.0},
              {"name": "joint_pos_rel", "dim": ACT_DIM, "scale": 1.0}, {"name": "joint_vel", "dim": ACT_DIM, "scale": 0.05},
              {"name": "last_action", "dim": ACT_DIM, "scale": 1.0}]
OBS_DIM = sum(o["dim"] for o in OBS_LAYOUT)


def find_checkpoint(spec: str) -> Path | None:
    if spec and spec not in ("latest", "best") and Path(spec).exists():
        return Path(spec)
    root = Path(P.get("store_root", ".")) / ".data" / "tutorial" / "runs"
    candidates = sorted(root.glob("*/latest.pt"), key=lambda p: p.stat().st_mtime, reverse=True) if root.exists() else []
    for c in candidates:
        params = c.parent / "params.json"
        try:
            if json.loads(params.read_text()).get("robot") == P.get("robot"):
                return c
        except Exception:
            continue
    return candidates[0] if candidates else None


class EmpiricalNormalization(nn.Module):
    """Minimal stand-in for rsl_rl.modules.EmpiricalNormalization (inference only)."""

    def __init__(self, dim):
        super().__init__()
        self.register_buffer("mean", torch.zeros(dim))
        self.register_buffer("std", torch.ones(dim))

    def forward(self, x):
        return (x - self.mean) / (self.std + 1e-2)


def make_actor(hidden=(256, 128, 64)) -> nn.Sequential:
    layers, last = [], OBS_DIM
    for h in hidden:
        layers += [nn.Linear(last, h), nn.ELU()]
        last = h
    layers.append(nn.Linear(last, ACT_DIM))
    return nn.Sequential(*layers)


class Exportable(nn.Module):
    def __init__(self, normalizer, actor):
        super().__init__()
        self.normalizer, self.actor = normalizer, actor

    def forward(self, obs):
        return self.actor(self.normalizer(obs))


ckpt = find_checkpoint(str(P.get("checkpoint", "latest")))
normalizer, actor, demo, loaded_from = EmpiricalNormalization(OBS_DIM), make_actor(), True, None
if ckpt is not None:
    try:
        state = torch.load(ckpt, map_location="cpu", weights_only=False)
        model_sd = state.get("model_state_dict", state)
        actor_sd = {k.replace("actor.", "", 1): v for k, v in model_sd.items() if k.startswith("actor.")}
        if actor_sd:
            # Rebuild hidden sizes from the checkpoint so an edited lesson-06 network still loads.
            weights = [v for k, v in sorted(actor_sd.items()) if k.endswith("weight")]
            hidden = tuple(w.shape[0] for w in weights[:-1])
            actor = make_actor(hidden)
            actor.load_state_dict(actor_sd)
            norm_sd = state.get("obs_norm_state_dict")
            if norm_sd and norm_sd.get("mean") is not None and norm_sd["mean"].numel() == OBS_DIM:
                normalizer.mean.copy_(norm_sd["mean"].flatten().float())
                normalizer.std.copy_(norm_sd["std"].flatten().float())
            demo, loaded_from = False, str(ckpt)
    except Exception as exc:
        print(f"[lesson08] could not load {ckpt}: {exc}; using demo actor")

model = Exportable(normalizer, actor).eval()
progress("exporting", 1, 4, message=f"checkpoint={'demo' if demo else loaded_from}")

fmt = P.get("export_format", "onnx")
dummy = torch.zeros(1, OBS_DIM)
outputs = {}
export_errors = {}
if fmt in ("onnx", "both"):
    kwargs = dict(input_names=["obs"], output_names=["actions"], dynamic_axes={"obs": {0: "batch"}, "actions": {0: "batch"}},
                  opset_version=int(P.get("opset", 17)))
    try:
        try:
            # torch >= 2.5 defaults to the dynamo exporter (needs onnxscript); the TorchScript exporter is enough for an MLP.
            torch.onnx.export(model, dummy, str(JOB / "policy.onnx"), dynamo=False, **kwargs)
        except TypeError:
            torch.onnx.export(model, dummy, str(JOB / "policy.onnx"), **kwargs)
        outputs["onnx"] = "policy.onnx"
    except Exception as exc:  # report the real reason (e.g. missing onnx/onnxscript) rather than faking a bundle
        export_errors["onnx"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        if fmt == "onnx":
            fmt = "torchscript"
if fmt in ("torchscript", "both"):
    torch.jit.trace(model, dummy).save(str(JOB / "policy.pt"))
    outputs["torchscript"] = "policy.pt"

progress("verifying", 2, 4, message="numerical comparison")
steps = int(P.get("verify_steps", 200))
tol = float(P.get("tolerance", 1e-4))
obs = torch.randn(steps, OBS_DIM)
with torch.no_grad():
    ref = model(obs)
errors = {}
if "onnx" in outputs:
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(JOB / "policy.onnx"), providers=["CPUExecutionProvider"])
        out = torch.from_numpy(sess.run(None, {"obs": obs.numpy()})[0])
        errors["onnxruntime_max_abs_err"] = float((out - ref).abs().max())
    except ImportError:
        errors["onnxruntime_max_abs_err"] = None
if "torchscript" in outputs:
    with torch.no_grad():
        out = torch.jit.load(str(JOB / "policy.pt"))(obs)
    errors["torchscript_max_abs_err"] = float((out - ref).abs().max())
measured = [v for v in errors.values() if v is not None]
passed = all(v <= tol for v in measured) if measured else None

meta = {"robot": P.get("robot"), "control_hz": int(P.get("control_hz", 50)), "obs_dim": OBS_DIM, "act_dim": ACT_DIM,
        "obs_layout": OBS_LAYOUT, "joint_names": JOINTS,
        "action_kinds": ["velocity" if (IS_WHEEL and "wheel" in j.lower()) else "position" for j in JOINTS],
        "action_scale": 0.25, "default_joint_positions": P.get("default_joint_positions", {}), "quaternion_order": "xyzw",
        "trained_with": {"checkpoint": loaded_from, "demo": demo, "isaaclab": "3.0", "physics": P.get("physics", "newton_mjwarp")},
        "files": outputs}
(JOB / "policy_meta.json").write_text(json.dumps(meta, indent=2))

progress("bundling", 3, 4, message="hashing bundle")
digest = hashlib.sha256()
for name in sorted(list(outputs.values()) + ["policy_meta.json"]):
    digest.update((JOB / name).read_bytes())
cross = None
if P.get("cross_backend_check"):
    try:
        from isaaclab.app import launch_simulation  # noqa: F401
        cross = {"status": "not_run", "reason": "cross-backend rollout requires the lesson-06 env; run it from lesson 06 with physics switched"}
    except Exception:
        cross = {"status": "skipped", "reason": "isaaclab 3 not importable"}

result = {"demo": demo, "checkpoint": loaded_from, "export_format": fmt, "files": outputs, "obs_dim": OBS_DIM, "act_dim": ACT_DIM,
          "tolerance": tol, **errors, "passed": passed, "bundle_sha256": digest.hexdigest(), "cross_backend": cross,
          "export_errors": export_errors or None}
if not outputs:
    result["error"] = "no export succeeded: " + json.dumps(export_errors)
(JOB / "result.json").write_text(json.dumps(result, indent=2))
progress("complete", 4, 4, **{k: v for k, v in result.items() if k != "cross_backend"})
print("[lesson08] " + json.dumps(result))
