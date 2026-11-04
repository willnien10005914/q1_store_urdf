"""HHTools import + BeyondMimic reference builder (no GPU)."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from q1store.roboparty.hhtools_import import (
    HHToolsImportError,
    motion_payload_from_hhtools,
    parse_hhtools_csv,
    remap_joint_name,
)
from q1store.roboparty.reference_bm import build_beyondmimic_reference
from q1store.roboparty.paths import status as roboparty_status
from q1store.roboparty.youtube_pipeline import (
    qa_leg_motion,
    repair_frozen_hip_pitch,
    slice_hhtools_csv,
    youtube_video_id,
)
from q1store.service import Store


SAMPLE_CSV = """# sample_rate: 10
# name: wave
time,root_x,root_y,root_z,root_qx,root_qy,root_qz,root_qw,dof_left_hip_pitch_joint,dof_right_hip_pitch_joint,dof_left_knee_joint,dof_right_knee_joint
0.0,0.0,0.0,0.85,0,0,0,1,0.1,-0.1,0.2,-0.2
0.1,0.01,0.0,0.84,0,0,0,1,0.12,-0.12,0.22,-0.22
0.2,0.02,0.0,0.85,0,0,0,1,0.1,-0.1,0.2,-0.2
0.3,0.03,0.0,0.86,0,0,0,1,0.08,-0.08,0.18,-0.18
0.4,0.04,0.0,0.85,0,0,0,1,0.1,-0.1,0.2,-0.2
0.5,0.05,0.0,0.85,0,0,0,1,0.11,-0.11,0.21,-0.21
"""


class HHToolsTests(unittest.TestCase):
    def test_remap_rp1_aliases(self):
        self.assertEqual(remap_joint_name("left_hip_pitch_joint"), "left_hip_Pitch_joint")
        self.assertEqual(remap_joint_name("left_knee_joint"), "left_knee_Pitch_joint")
        self.assertEqual(remap_joint_name("waist_yaw_joint"), "center_waist_Yaw_joint")

    def test_parse_and_payload(self):
        parsed = parse_hhtools_csv(SAMPLE_CSV)
        self.assertEqual(parsed["fps"], 10.0)
        self.assertIn("left_hip_Pitch_joint", parsed["joint_names"])
        self.assertAlmostEqual(parsed["frames"][1]["root"][0], 0.01)
        body = motion_payload_from_hhtools(parsed, robot_type="leg", action_name="wave")
        self.assertEqual(body["source"], "hhtools")
        self.assertEqual(body["mapping"]["pipeline"], "beyondmimic")
        self.assertTrue(body["mapping"]["root_motion"])
        self.assertEqual(body["frames"][0]["time"], 0.0)

    def test_rejects_bad_csv(self):
        with self.assertRaises(HHToolsImportError):
            parse_hhtools_csv("time,x\n0,1\n")

    def test_beyondmimic_reference_keeps_xy(self):
        parsed = parse_hhtools_csv(SAMPLE_CSV)
        motion = motion_payload_from_hhtools(parsed, robot_type="leg")
        motion["id"] = "test"
        names = motion["joint_names"]
        reference = build_beyondmimic_reference(
            motion,
            joint_names=names,
            default=[0.0] * len(names),
            lower=[-2] * len(names),
            upper=[2] * len(names),
            urdf_sha256="abc",
            grounded_height=lambda q: 0.85,
        )
        self.assertEqual(reference["source"], "hhtools")
        self.assertEqual(reference["pipeline"], "beyondmimic")
        xs = [r[0] for r in reference["root"]]
        self.assertGreater(max(xs), 0.01)
        self.assertEqual(len(reference["root"]), len(reference["q"]))
        self.assertEqual(len(reference["root_quat"]), len(reference["q"]))

    def test_store_import_endpoint_path(self):
        self.assertIn("beyondmimic", roboparty_status()["pipeline"])
        self.assertTrue(roboparty_status()["ready_for_import"])


class StoreHHToolsIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "assets/examples").mkdir(parents=True)
        (self.root / "assets/examples/catalog.json").write_text("[]")
        (self.root / "assets/video-examples").mkdir(parents=True)
        (self.root / "assets/video-examples/catalog.json").write_text(json.dumps({"examples": [], "sources": []}))
        (self.root / "assets/robots/leg").mkdir(parents=True)
        urdf = (
            b'<robot name="test">'
            b'<joint name="left_hip_Pitch_joint" type="revolute"><limit lower="-1" upper="1"/></joint>'
            b'<joint name="right_hip_Pitch_joint" type="revolute"><limit lower="-1" upper="1"/></joint>'
            b'<joint name="left_knee_Pitch_joint" type="revolute"><limit lower="-1" upper="1"/></joint>'
            b'<joint name="right_knee_Pitch_joint" type="revolute"><limit lower="-1" upper="1"/></joint>'
            b'<joint name="fixed" type="fixed"/></robot>'
        )
        (self.root / "assets/robots/leg/native.urdf").write_bytes(urdf)
        (self.root / "assets/robots/manifest.json").write_text(json.dumps({"robots": [{
            "id": "leg",
            "urdf_url": "/assets/robots/leg/native.urdf",
            "urdf_sha256": hashlib.sha256(urdf).hexdigest(),
            "control_joints": [
                "left_hip_Pitch_joint", "right_hip_Pitch_joint",
                "left_knee_Pitch_joint", "right_knee_Pitch_joint",
            ],
        }]}))
        self.store = Store(self.root, clock=lambda: 1.0)

    def tearDown(self):
        self.temp.cleanup()

    def test_import_creates_beyondmimic_job(self):
        motion = self.store.import_hhtools_motion({"csv": SAMPLE_CSV, "robot_type": "leg", "action_name": "wave"})
        self.assertEqual(motion["frame_count"], 6)
        self.assertTrue(motion["validation"]["root_motion"])
        full = self.store.get("motions", motion["id"])
        self.assertEqual(full["source"], "hhtools")
        job = self.store.create_job({
            "robot_type": "leg",
            "mode": "worker",
            "motion_id": motion["id"],
            "source": {"type": "hhtools", "title": "wave", "id": motion["id"]},
            "target_person": {"id": "hhtools"},
            "config": {"library": "rsl_rl", "physics": "isaacsim_physx",
                       "rewards": ["motion_tracking", "balance"], "hours": 4, "landmark": "taipei101"},
            "pipeline": "beyondmimic",
        })
        self.assertEqual(job["plan"]["pipeline"], "beyondmimic")
        self.assertTrue(job["training"]["trainable"])


class YouTubeV3PipelineTests(unittest.TestCase):
    def test_video_id_shorts(self):
        self.assertEqual(
            youtube_video_id("https://youtube.com/shorts/a5R0Xf46hyg?si=abc"),
            "a5R0Xf46hyg",
        )

    def test_slice_and_repair_hip_pitch(self):
        # Build a mini CSV where right hip Pitch is frozen but Roll moves (ballet fail mode).
        lines = [
            "# sample_rate: 10",
            "time,root_x,root_y,root_z,root_qx,root_qy,root_qz,root_qw,"
            "dof_left_hip_Pitch_joint,dof_right_hip_Pitch_joint,"
            "dof_right_hip_Roll_joint,dof_right_knee_Pitch_joint,"
            "dof_left_knee_Pitch_joint",
        ]
        for i in range(12):
            t = i * 0.1
            roll = -0.2 - 0.1 * i
            knee = -0.05 * (i % 5)
            lines.append(
                f"{t:.1f},0,0,0.85,0,0,0,1,0.1,0.0,{roll:.3f},{knee:.3f},0.05"
            )
        csv_text = "\n".join(lines) + "\n"
        sliced = slice_hhtools_csv(csv_text, 0.2, 1.0)
        repaired, notes = repair_frozen_hip_pitch(sliced)
        self.assertTrue(notes)
        qa = qa_leg_motion(repaired)
        self.assertGreater(qa["dof_right_hip_Pitch_joint"], 0.15)


if __name__ == "__main__":
    unittest.main()
