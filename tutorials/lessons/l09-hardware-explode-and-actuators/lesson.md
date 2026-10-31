# 硬體／機構爆炸圖 → Isaac Lab 致動器

參考 RoboParty 硬體牆的爆炸動畫（本課 Lab 面板內嵌同一支 webm），把 **Q1 URDF** 當成可編輯的機構模型：加 ToF／音訊、換 skin、調相機，再按「分析／分解」看爆炸圖。同一組參數會餵給 **Isaac Lab 3 `ImplicitActuatorCfg`**，示範硬體負載如何反映到 PD 增益與 effort limit。

## 為什麼要爆炸圖？

機構設計與感測器佈局常在「組合體」裡看不清楚。爆炸圖保留關節拓樸，只把每個 link 沿遠離軀幹的方向推開，方便檢查：

- ToF／麥克風／喇叭相對 `center_torso_link` 的位置與尺寸  
- 外殼 skin（僅視覺）與附加質量（影響控制）  
- 相機機位是否遮擋重要零件  

瀏覽器端用 Three.js + 原生 URDF（與首頁 Studio 同一套 `robot-viewer`），**不必開 Isaac Sim** 就能互動。

## Lab 面板操作

1. 看參考影片（RoboParty hardware wall）  
2. 調 skin、相機預設、ToF／Audio 的 XYZ／尺寸／質量  
3. 按 **重新生成外形** — 重建配件 mesh 與材質  
4. 按 **分析／分解** — 動畫展開到「爆炸展開量」  
5. 調好後按 **執行** — 把參數寫進 `params.json`，用 Isaac Lab 跑致動器追蹤  

## 硬體參數 → 致動器

附加質量 \(m_{\text{extra}} = m_{\text{tof}} + m_{\text{audio}}\)（僅在對應開關開啟時計入）相對基準質量 \(m_0\) 的比例：

\[
s = 1 + \frac{m_{\text{extra}}}{m_0},\quad
K_p' = s\,K_p,\quad
K_d' = s\,K_d
\]

例程建立：

```python
from isaaclab.actuators import ImplicitActuatorCfg

actuators = {
    "pd": ImplicitActuatorCfg(
        joint_names_expr=pd_expr,
        stiffness=Kp_scaled,
        damping=Kd_scaled,
        actuator_effort_limit=A_LIM,
        joint_effort_limit=J_LIM,
        armature=0.01,
    ),
}
```

3.0 記得分開設 `actuator_effort_limit`（模型額定）與 `joint_effort_limit`（solver）；後者應 ≥ 前者，否則力矩會被二次裁切。

## 執行產物

| 檔案 | 用途 |
|------|------|
| `hardware.json` | skin、配件位姿／質量、縮放後的 PD |
| `rollout.json` | 爆炸展開關鍵影格（瀏覽器可重播） |
| `result.json` | 追蹤 RMSE、力矩峰值、質量縮放 |
| `result.mp4`／`result.gif` | 若本機有 ffmpeg，store 會從 rollout 產預覽 |

## 與第五課的關係

第五課專注比較 Implicit／DC／Delayed PD。本課固定 Implicit，把焦點放在 **機構配置（質量／感測器）如何驅動同一組 actuator API**。做完兩邊對照，就能把「硬體 BOM」接到「控制參數」。
