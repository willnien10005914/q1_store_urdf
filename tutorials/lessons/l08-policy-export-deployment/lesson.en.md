# Policy export and deployment

Training is only half the job. Q1 controllers (and this store’s deployments flow) need an **inference artifact that does not import Isaac Lab**, plus **policy_meta.json** documenting I/O semantics.

## 1. From latest.pt to an inference policy

RSL-RL checkpoints store full `OnPolicyRunner` state: actor, critic, optimizer, and (if enabled) `EmpiricalNormalization` statistics.

```python
from rsl_rl.runners import OnPolicyRunner
runner = OnPolicyRunner(env, agent_cfg.to_dict(), log_dir=None, device="cuda:0")
runner.load("latest.pt")
policy = runner.get_inference_policy(device="cuda:0")   # normalizer + actor, eval mode
```

3.0 CLI uses `--checkpoint latest|best|<path>`; this store’s `checkpoint=latest` picks the newest `latest.pt` from your lesson 6 run.

## 2. Export ONNX / TorchScript

```python
class Exportable(torch.nn.Module):
    def __init__(self, normalizer, actor):
        super().__init__(); self.normalizer, self.actor = normalizer, actor
    def forward(self, obs):
        return self.actor(self.normalizer(obs))

m = Exportable(runner.obs_normalizer, runner.alg.policy.actor).cpu().eval()
torch.onnx.export(m, torch.zeros(1, obs_dim), "policy.onnx", input_names=["obs"], output_names=["actions"],
                  dynamic_axes={"obs": {0: "batch"}, "actions": {0: "batch"}}, opset_version=17)
torch.jit.trace(m, torch.zeros(1, obs_dim)).save("policy.pt")
```

**The usual deployment failure** is not export errors—it is:

1. Forgetting to wrap the normalizer.
2. Observation order differs from training `ObservationGroupCfg` (`concatenate_terms=True` order = declaration order).
3. Actions missing `scale` and `default_joint_pos` remapping.

## 3. policy_meta.json contract

Bundle read by this store’s deployments:

```json
{
  "robot": "leg",
  "control_hz": 50,
  "obs_dim": 45, "act_dim": 12,
  "obs_layout": [{"name": "base_lin_vel", "dim": 3, "scale": 1.0}, {"name": "base_ang_vel", "dim": 3}, {"name": "projected_gravity", "dim": 3},
                 {"name": "velocity_commands", "dim": 3}, {"name": "joint_pos_rel", "dim": 12}, {"name": "joint_vel", "dim": 12, "scale": 0.05},
                 {"name": "last_action", "dim": 12}],
  "joint_names": ["..."],
  "action_kinds": ["position", "position", "...", "velocity"],
  "action_scale": 0.25,
  "default_joint_positions": {"...": 0.0},
  "quaternion_order": "xyzw",
  "trained_with": {"physics": "newton_mjwarp", "isaaclab": "3.0.0", "iterations": 300}
}
```

`quaternion_order` is required: 3.0 uses XYZW internally; many real IMU drivers emit WXYZ.

## 4. Numeric verify and cross-backend check

`example.py`:

1. Loads a checkpoint (or builds a random same-shape actor when missing, `demo=true`).
2. Runs `verify_steps` random observations through PyTorch vs ONNX Runtime (if installed) vs TorchScript; reports `max_abs_err`.
3. Writes `policy.onnx` / `policy.pt` / `policy_meta.json` and SHA-256 of the bundle.
4. With `cross_backend_check=true` and Lab 3 available, runs 10 s on the other physics backend and reports `survival_rate_other_backend`.

**Exercises**

- Set `tolerance` to 1e-6—does ONNX still pass? Which layer diverges first? (Hint: ELU/tanh implementations.)
- Change `control_hz` to 100 in meta—what was training decimation?
- Wheel: confirm wheel entries in `action_kinds` are `velocity`.

## 5. Back to this store

After the bundle is ready, register the run on the home **Deployments** page; the store reruns lesson 6 gates (survival, upright score) before release.
