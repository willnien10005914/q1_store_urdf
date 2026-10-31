# 策略匯出與部署

訓練完成只是一半。要讓 Q1 的控制器（或本站的 deployments 流程）能用，你需要一個**不依賴 Isaac Lab 的推論檔**，以及一份講清楚輸入輸出語意的 **policy_meta.json**。

## 1. 從 latest.pt 到推論函式

RSL-RL 的 checkpoint 存的是 `OnPolicyRunner` 狀態：actor、critic、optimizer 與（若啟用）`EmpiricalNormalization` 的統計。

```python
from rsl_rl.runners import OnPolicyRunner
runner = OnPolicyRunner(env, agent_cfg.to_dict(), log_dir=None, device="cuda:0")
runner.load("latest.pt")
policy = runner.get_inference_policy(device="cuda:0")   # normalizer + actor，eval 模式
```

3.0 的統一 CLI 用 `--checkpoint latest|best|<path>` 挑 checkpoint；本站的 `checkpoint=latest` 會從你在第六課完成的 run 中挑最新的 `latest.pt`。

## 2. 匯出 ONNX／TorchScript

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

**最常見的部署錯誤**不是匯出失敗，而是：

1. 忘了把 normalizer 包進去。
2. observation 順序與訓練時的 `ObservationGroupCfg` 不同（`concatenate_terms=True` 的順序＝宣告順序）。
3. action 沒有乘回 `scale`、加回 `default_joint_pos`。

## 3. policy_meta.json 契約

本站 deployments 讀的 bundle：

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

`quaternion_order` 必填：3.0 內部是 XYZW，而很多實機 IMU 驅動給 WXYZ。

## 4. 數值比對與跨後端檢查

`example.py` 會：

1. 載入 checkpoint（或在沒有時建立同構的隨機 actor，`demo=true`）。
2. 用 `verify_steps` 組隨機 obs 比對 PyTorch vs ONNX Runtime（若有安裝）vs TorchScript 的輸出，回報 `max_abs_err`。
3. 產出 `policy.onnx`／`policy.pt`／`policy_meta.json`，並計算 bundle 的 SHA-256。
4. 若 `cross_backend_check=true` 且有 Isaac Lab 3，用另一個後端跑 10 秒，回報 `survival_rate_other_backend`。

**練習**

- 把 `tolerance` 調到 1e-6，ONNX 還過得了嗎？哪一層先出現差異？（提示：ELU／tanh 的實作差異）
- 把 `control_hz` 改成 100，meta 會變，但策略本身呢？訓練時的 decimation 是多少？
- Wheel：確認 `action_kinds` 裡輪子是 `velocity`。

## 5. 接回本站

bundle 完成後，可在首頁的「部署」把 `policy_meta.json` 所在的 run 註冊成 deployment；本站會做與第六課同一組 gate（存活率、直立度）再放行。
