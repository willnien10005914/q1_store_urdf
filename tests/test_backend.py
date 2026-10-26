"""Meaningful state, persistence, upload, and HTTP isolation checks; no GPU needed."""
import json
import io
import shutil
import stat
import zipfile
import copy
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from q1store.service import APIError, Store, youtube_url
from server import Handler


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 1000.0
        (self.root / "assets/examples").mkdir(parents=True)
        (self.root / "assets/examples/catalog.json").write_text(json.dumps([
            {"id": "leg-example", "robot_type": "leg", "title": "Leg PPO", "video_url": "/assets/examples/leg.mp4", "provenance": "historical leg repo"},
            {"id": "wheel-example", "robot_type": "wheel", "title": "Wheel PPO", "video_url": "/assets/examples/wheel.mp4", "provenance": "historical wheel repo"},
        ]))
        (self.root / "assets/video-examples").mkdir(parents=True)
        (self.root / "assets/video-examples/catalog.json").write_text(json.dumps({"examples": [
            {"id": "leg-punch", "robot_type": "leg", "title": "H2-X punch", "source_url": "https://youtu.be/Y3zb4o47yqA",
             "source_video_id": "Y3zb4o47yqA", "start_s": 12, "end_s": 18, "video_url": "/assets/video-examples/leg-punch.mp4"},
            {"id": "wheel-turn", "robot_type": "wheel", "title": "X2 turn", "source_url": "https://youtu.be/TV6ctYnA9Io",
             "source_video_id": "TV6ctYnA9Io", "start_s": 45, "end_s": 50, "video_url": "/assets/video-examples/wheel-turn.mp4"},
        ], "sources": [{"id": "Y3zb4o47yqA", "url": "https://youtu.be/Y3zb4o47yqA"}]}))
        (self.root / "assets/robots/leg").mkdir(parents=True)
        urdf = b'<robot name="test"><joint name="hip" type="revolute"><limit lower="-1" upper="1"/></joint><joint name="fixed" type="fixed"/></robot>'
        (self.root / "assets/robots/leg/native.urdf").write_bytes(urdf)
        (self.root / "assets/robots/manifest.json").write_text(json.dumps({"robots": [
            {"id": "leg", "urdf_url": "/assets/robots/leg/native.urdf", "urdf_sha256": hashlib.sha256(urdf).hexdigest(),
             "commit": "abc123", "repository": "https://github.com/example/leg", "control_joints": ["hip"]}
        ]}))
        self.store = Store(self.root, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, robot="leg", **updates):
        payload = {"robot_type": robot, "source": {"type": "example", "id": robot + "-example"},
                   "target_person": {"id": 1, "color": "#40aaff"},
                   "config": {"library": "rsl_rl", "physics": "isaacsim_physx", "rewards": ["motion_tracking", "balance"], "hours": 4, "landmark": "taipei101"}}
        payload.update(updates)
        return payload

    def test_demo_uses_historical_result_and_never_claims_new_training(self):
        job = self.store.create_job(self.payload())
        self.assertEqual(job["state"], "retargeting")
        self.assertIsNone(job["result"])
        self.now += 10
        self.assertEqual(self.store.job(job["id"])["state"], "training_demo")
        self.now += 21
        finished = self.store.job(job["id"])
        self.assertEqual(finished["state"], "demo_complete")
        self.assertFalse(finished["newly_trained"])
        self.assertFalse(finished["result"]["newly_trained"])
        self.assertFalse(finished["result"]["landmark_rendered"])
        self.assertEqual(finished["result"]["video_url"], "/assets/examples/leg.mp4")

    def test_num_envs_scale_with_hours_and_ignore_a_fixed_override(self):
        from q1store.service import num_envs_for_hours
        from q1store.worker import Worker, envs_for_job
        self.assertEqual([num_envs_for_hours(h) for h in (1, 3, 4, 4.9, 5, 6, 7, 24)],
                         [1024, 1024, 1024, 1024, 2048, 3072, 4096, 21504])
        base = self.payload()["config"]
        for hours, envs in ((4, 1024), (5, 2048), (6, 3072)):
            job = self.store.create_job(self.payload(config={**base, "hours": hours, "num_envs": 99999}))
            self.assertEqual(job["config"]["num_envs"], envs)
            self.assertEqual(job["plan"]["num_envs"], envs)
            self.assertEqual(job["plan"]["config"]["num_envs"], envs)
        os_env = __import__("os").environ
        previous = os_env.get("Q1_WORKER_NUM_ENVS")
        os_env["Q1_WORKER_NUM_ENVS"] = "2048"
        try:
            motion_id = self.store.create_motion(self.motion())["id"]
            job = self.store.create_job(self.payload(mode="worker", motion_id=motion_id, config={**base, "hours": 6}))
            worker = Worker(self.root, leg_root=self.root, num_envs=64, clock=lambda: self.now)
            self.assertEqual(envs_for_job(job), 3072)
            captured = {}

            def run_phase(job_id, phase, extra):
                captured[phase] = list(extra)
                directory = worker.job_dir(job_id)
                if phase == "train":
                    (directory / "evaluation.json").write_text(json.dumps({"num_envs": 3072, "iterations": 1, "passed": False}))
                if phase == "record":
                    (directory / "record.json").write_text("{}")
                return 0

            worker.acquire_gpu = lambda job_id: True
            worker.release_gpu = lambda: None
            worker.run_phase = run_phase
            self.assertTrue(worker.run_once())
            self.assertIn("3072", captured["train"])
            self.assertEqual(captured["train"][captured["train"].index("--num_envs") + 1], "3072")
            finished = self.store.job(job["id"])
            self.assertEqual(finished["training"]["num_envs"], 3072)
            self.assertEqual(finished["state"], "complete")
            self.assertIsNone(worker.active_envs)
        finally:
            if previous is None:
                os_env.pop("Q1_WORKER_NUM_ENVS", None)
            else:
                os_env["Q1_WORKER_NUM_ENVS"] = previous

    def test_worker_never_completes_without_actual_worker(self):
        job = self.store.create_job(self.payload(mode="worker"))
        self.now += 100000
        future = self.store.job(job["id"])
        self.assertEqual(future["state"], "queued_for_worker")
        self.assertIsNone(future["result"])
        self.assertFalse(future["plan"]["task_registered"])
        self.assertIn("isaacsim_physx", " ".join(future["plan"]["train_command"]))
        self.assertEqual(future["plan"]["job_id"], job["id"])
        self.assertEqual(future["plan"]["robot"]["control_joints"], ["hip"])
        self.assertEqual(future["plan"]["robot"]["source_commit"], "abc123")
        self.assertEqual(len(future["plan"]["robot"]["urdf_sha256"]), 64)
        self.assertEqual(future["plan"]["config"]["physics"], "isaacsim_physx")
        self.assertEqual(future["config"]["num_envs"], 1024)
        self.assertEqual(future["plan"]["num_envs"], 1024)
        self.assertEqual(future["training"]["num_envs"], 1024)

    def test_kungfu_procedural_source_is_explicit_without_fake_video(self):
        job = self.store.create_job(self.payload(source={"type": "example", "id": "kungfu-demo"}))
        self.assertTrue(job["source"]["procedural"])
        self.assertNotIn("url", job["source"])
        self.assertIsNone(job["result"])

    def test_listing_requires_completed_job_and_purchase_never_charges(self):
        job = self.store.create_job(self.payload("wheel"))
        with self.assertRaises(APIError):
            self.store.create_listing({"job_id": job["id"], "title": "Wheel trick", "price": 20})
        self.now += 31
        listing = self.store.create_listing({"job_id": job["id"], "title": "Wheel trick", "price": 20})
        receipt = self.store.purchase({"listing_id": listing["id"]})
        self.assertEqual(receipt["charged_amount"], 0)
        self.assertTrue(receipt["is_demo"])
        reopened = Store(self.root)
        self.assertEqual(reopened.get("listings", listing["id"])["title"], "Wheel trick")
        self.assertEqual(len(reopened.all("purchases")), 1)

    def test_cancel_persists_across_clock_advance(self):
        job = self.store.create_job(self.payload())
        self.store.cancel(job["id"])
        self.now += 100
        self.assertEqual(self.store.job(job["id"])["state"], "cancelled")

    def test_upload_validates_container_and_does_not_trust_path(self):
        with self.assertRaises(APIError):
            self.store.save_upload("attack.mp4", b"<script>alert(1)</script>")
        with self.assertRaises(APIError):
            self.store.save_upload("attack.html", b"0000ftyp" + b"x" * 20)
        result = self.store.save_upload("../../test.mp4", b"0000ftyp" + b"x" * 20)
        self.assertEqual(result["filename"], "test.mp4")
        self.assertNotIn("path", result)
        job = self.store.create_job(self.payload(source={"type": "upload", "id": result["id"], "url": "https://evil.test/file"}))
        self.assertEqual(job["source"]["url"], result["url"])

    def test_source_and_config_validation(self):
        bad_values = [
            {"robot_type": "other"}, {"mode": "execute_shell"}, {"source": {"type": "example", "id": "missing"}},
            {"config": {"hours": float("nan")}}, {"config": {"hours": 10000}},
            {"config": {"physics": ["x"]}}, {"config": {"rewards": ["wheel_velocity"]}},
            {"robot_type": []}, {"mode": {}}, {"source": {"type": []}},
            {"source": {"type": "upload", "id": {}}}, {"target_person": {"value": float("nan")}},
        ]
        for bad in bad_values:
            with self.subTest(bad=bad), self.assertRaises(APIError):
                self.store.create_job(self.payload(**bad))

    def test_video_example_catalog_and_segment_plan_preserve_original_offsets(self):
        catalog = self.store.catalog()
        self.assertEqual(len(catalog["video_examples"]), 2)
        self.assertEqual(catalog["video_sources"][0]["id"], "Y3zb4o47yqA")
        job = self.store.create_job(self.payload(source={"type": "video_example", "id": "leg-punch"},
            segment={"start_s": 1.25, "end_s": 4.5}, action_name="H2-X left punch", mode="worker"))
        self.assertEqual(job["action_name"], "H2-X left punch")
        self.assertEqual(job["plan"]["action_name"], job["action_name"])
        self.assertEqual(job["segment"]["start_s"], 1.25)
        self.assertEqual(job["segment"]["source_start_s"], 13.25)
        self.assertEqual(job["segment"]["source_end_s"], 16.5)
        self.assertEqual(job["plan"]["segment"], job["segment"])
        self.assertEqual(job["source"]["source_url"], "https://www.youtube.com/watch?v=Y3zb4o47yqA")
        self.assertEqual(job["source"]["tracking_scope"], "robot_video_reference")
        self.assertFalse(job["source"]["is_training_result"])
        self.assertFalse(job["plan"]["motion_extraction_ready"])
        self.now += 100000
        self.assertEqual(self.store.job(job["id"])["state"], "queued_for_worker")
        self.assertIsNone(self.store.job(job["id"])["result"])

    def test_video_example_default_segment_and_robot_mismatch(self):
        source = {"type": "video_example", "id": "wheel-turn"}
        job = self.store.create_job(self.payload("wheel", source=source))
        self.assertEqual((job["segment"]["start_s"], job["segment"]["end_s"]), (0, 5))
        self.assertEqual(job["segment"]["source_start_s"], 45)
        with self.assertRaises(APIError):
            self.store.create_job(self.payload("leg", source=source))
        self.now += 31
        self.assertFalse(self.store.job(job["id"])["result"]["newly_trained"])

    def test_segment_and_action_name_validation_rejects_invalid_or_out_of_clip_bounds(self):
        for segment in [{"start_s": 0, "end_s": .49}, {"start_s": 0, "end_s": 61},
                        {"start_s": -1, "end_s": 2}, {"start_s": 5, "end_s": 2},
                        {"start_s": True, "end_s": 2}, {"start_s": "0", "end_s": 2},
                        {"start_s": 0, "end_s": float("inf")}, {"start_s": 0}, [],
                        {"start_s": 5, "end_s": 7}]:
            with self.subTest(segment=segment), self.assertRaises(APIError):
                self.store.create_job(self.payload(source={"type": "video_example", "id": "leg-punch"}, segment=segment))
        for name in ["", "  ", 3, "x" * 81]:
            with self.subTest(name=name), self.assertRaises(APIError):
                self.store.create_job(self.payload(action_name=name))
        # Uploaded duration is unknown: bounds are saved explicitly without claiming probing.
        upload = self.store.save_upload("motion.mp4", b"0000ftyp" + b"x" * 20)
        job = self.store.create_job(self.payload(source={"type": "upload", "id": upload["id"]},
            segment={"start_s": 2, "end_s": 8}))
        self.assertFalse(job["segment"]["duration_verified"])

    def test_motion_and_job_segment_must_agree_and_all_source_frames_stay_in_segment(self):
        motion = self.motion()
        motion["segment"] = {"start_s": 2, "end_s": 3}
        saved = self.store.create_motion(motion)
        job = self.store.create_job(self.payload(source={"type": "video_example", "id": "leg-punch"},
            segment={"start_s": 2, "end_s": 3}, motion_id=saved["id"]))
        self.assertTrue(job["plan"]["motion_extraction_ready"])
        self.assertEqual(job["plan"]["segment"]["source_start_s"], 14)
        self.assertEqual(job["plan"]["motion"]["segment"]["start_s"], 2)
        with self.assertRaises(APIError):
            self.store.create_job(self.payload(motion_id=saved["id"], segment={"start_s": 1, "end_s": 3}))
        motion["frames"][1]["source_time"] = 3.1
        with self.assertRaises(APIError):
            self.store.create_motion(motion)
        raw_motion = self.motion()
        raw_motion["frames"][1]["source_time"] = "2.1"
        with self.assertRaises(APIError):
            self.store.create_motion(raw_motion)

    def test_youtube_ingest_saves_named_seconds_without_streaming_the_remote_cut(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg is required to build the local clip")
        script = self.root / "ytdlp"
        script.write_text("""#!/bin/sh
set -eu
out=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--output" ]; then out="$arg"; fi
  prev="$arg"
done
printf '%s\\n' "$@" >> "$(dirname "$0")/args"
printf 'x\\n' >> "$(dirname "$0")/calls"
file=$(printf '%s' "$out" | sed 's/%(ext)s/mp4/')
mkdir -p "$(dirname "$file")"
ffmpeg -y -f lavfi -i testsrc=size=64x64:rate=10:duration=6 -c:v libx264 -pix_fmt yuv420p -t 6 "$file" -loglevel error
""")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        self.store.downloader = str(script)
        self.store.ffmpeg = ffmpeg
        with self.assertRaises(APIError):
            self.store.ingest({"youtube_url": "https://youtu.be/abcdefghijk"})
        saved = self.store.ingest({"youtube_url": "https://youtu.be/abcdefghijk?t=4", "action_name": "護架跨步",
                                   "start_s": 1, "end_s": 3})
        self.assertEqual(saved["type"], "youtube")
        self.assertEqual(saved["action_name"], "護架跨步")
        self.assertEqual(saved["youtube_url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertAlmostEqual(saved["source_start_s"], 1, delta=.15)
        self.assertAlmostEqual(saved["duration_s"], 2, delta=.15)
        self.assertTrue((self.store.upload_dir / (saved["id"] + ".mp4")).is_file())
        self.assertEqual((self.root / "calls").read_text().count("x"), 1)
        args = (self.root / "args").read_text()
        self.assertIn("--force-ipv4", args)
        self.assertNotIn("--download-sections", args)
        with self.assertRaises(APIError):
            self.store.ingest({"youtube_url": "https://www.youtube.com/watch?v=abcdefghijk", "action_name": "太長",
                               "start_s": 1, "end_s": 10})
        self.assertEqual((self.root / "calls").read_text().count("x"), 1)
        again = self.store.ingest({"youtube_url": "https://www.youtube.com/watch?v=abcdefghijk", "action_name": "第二段",
                                   "start_s": 2, "end_s": 4})
        self.assertEqual((self.root / "calls").read_text().count("x"), 1)
        self.assertNotEqual(again["id"], saved["id"])
        job = self.store.create_job(self.payload(source={"type": "youtube", "id": saved["id"]},
            segment={"start_s": 0, "end_s": saved["duration_s"]}, action_name="護架跨步", mode="worker"))
        self.assertEqual(job["state"], "queued_for_worker")
        self.assertEqual(job["source"]["url"], saved["url"])
        self.assertAlmostEqual(job["segment"]["source_start_s"], saved["source_start_s"], delta=.05)
        self.assertFalse(job["newly_trained"])
        renamed = self.store.save_clip({"upload_id": saved["id"], "action_name": "護架跨步·改名",
                                        "start_s": 0, "end_s": saved["duration_s"]})
        self.assertEqual(renamed["id"], saved["id"])
        self.assertEqual(renamed["action_name"], "護架跨步·改名")

    def test_youtube_url_is_canonical_and_rejects_arbitrary_network_targets(self):
        self.assertEqual(youtube_url("https://youtu.be/abcdefghijk?t=4"), "https://www.youtube.com/watch?v=abcdefghijk")
        for value in ["http://127.0.0.1/", "https://youtube.com.evil.test/watch?v=abcdefghijk", "https://youtube.com:abc/watch?v=abcdefghijk", "https://youtube.com/playlist?list=abc", "https://youtube.com@127.0.0.1/watch?v=abcdefghijk", "x" * 3000, [], "https://youtube.com/watch/../watch?v=abcdefghijk"]:
            with self.subTest(value=value), self.assertRaises(APIError):
                youtube_url(value)

    def registered_robot(self, **updates):
        payload = {"name": "My Q1 Leg", "robot_type": "leg", "serial": "Q1-LEG-001"}
        payload.update(updates)
        return self.store.register_robot(payload)

    def test_registered_robot_contract_and_serial_validation(self):
        robot = self.registered_robot(urdf_sha256="forged", control_joints=["wrong"])
        self.assertEqual(robot["control_joints"], ["hip"])
        self.assertEqual(robot["urdf_sha256"], self.store.catalog()["robots"][0]["urdf_sha256"])
        self.assertEqual(robot["connection_state"], "not_connected")
        self.assertFalse(robot["hardware_delivery"])
        self.assertEqual(Store(self.root).get("registered_robots", robot["id"]), robot)
        for update in [{"serial": "q1-leg-001"}, {"serial": "../../robot"}, {"serial": "bad\nserial"},
                       {"serial": "x" * 81}, {"name": ""}, {"name": "\nRobot"}, {"name": "x" * 61},
                       {"robot_type": "other"}, {"serial": None}]:
            with self.subTest(update=update), self.assertRaises(APIError):
                self.registered_robot(**update)

    def test_robot_registration_checks_actual_native_file_hash(self):
        (self.root / "assets/robots/leg/native.urdf").write_bytes(b"changed model")
        with self.assertRaises(APIError):
            self.registered_robot()

    def test_deployment_download_is_targeted_inert_and_persistent(self):
        motion = self.store.create_motion(self.motion())
        job = self.store.create_job(self.payload(motion_id=motion["id"], action_name="Leg retarget"))
        robot = self.registered_robot()
        with self.assertRaises(APIError):
            self.store.create_deployment({"job_id": job["id"], "robot_id": robot["id"]})
        self.now += 31
        deployment = self.store.create_deployment({"job_id": job["id"], "robot_id": robot["id"],
                                                  "deployable": True, "hardware_activation_allowed": True})
        self.assertEqual(deployment["status"], "package_ready")
        self.assertEqual(deployment["target_robot"]["serial"], robot["serial"])
        for field in ["deployable", "hardware_activation_allowed", "checkpoint_included", "device_acknowledged", "newly_trained"]:
            self.assertIs(deployment[field], False)
        metadata, content = Store(self.root).deployment_package(deployment["id"])
        self.assertEqual(metadata["package_sha256"], hashlib.sha256(content).hexdigest())
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertEqual(set(archive.namelist()), {"manifest.json", "job-plan.json", "robot-contract.json", "motion.json", "README.txt"})
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["robot_id"], robot["id"])
            self.assertEqual(manifest["package_kind"], "preview_only")
            self.assertFalse(manifest["hardware_delivery"])
            self.assertFalse(manifest["deployable"])
            self.assertFalse(manifest["checkpoint_included"])
            self.assertEqual(json.loads(archive.read("motion.json"))["frames"], self.motion()["frames"])
            self.assertEqual(json.loads(archive.read("robot-contract.json"))["control_joints"], ["hip"])
            for item in manifest["files"]:
                self.assertEqual(hashlib.sha256(archive.read(item["path"])).hexdigest(), item["sha256"])
            self.assertIn(b"no runnable PPO checkpoint", archive.read("README.txt"))
        (self.store.deployment_dir / (deployment["id"] + ".zip")).write_bytes(b"tampered")
        with self.assertRaises(APIError):
            self.store.deployment_package(deployment["id"])

    def test_deployment_rejects_wrong_robot_stale_contract_and_queued_job(self):
        robot = self.registered_robot()
        wheel = self.store.create_job(self.payload("wheel"))
        worker = self.store.create_job(self.payload(mode="worker"))
        self.now += 31
        for job in [wheel, worker]:
            with self.subTest(job=job["robot_type"]), self.assertRaises(APIError):
                self.store.create_deployment({"job_id": job["id"], "robot_id": robot["id"]})
        leg = self.store.create_job(self.payload())
        self.now += 31
        changed = dict(robot, urdf_sha256="0" * 64)
        self.store.put("registered_robots", changed)
        with self.assertRaises(APIError):
            self.store.create_deployment({"job_id": leg["id"], "robot_id": robot["id"]})
        self.store.put("registered_robots", robot)
        manifest = json.loads((self.root / "assets/robots/manifest.json").read_text())
        manifest["robots"][0]["urdf_sha256"] = "1" * 64
        (self.root / "assets/robots/manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(APIError):
            self.store.create_deployment({"job_id": leg["id"], "robot_id": robot["id"]})

    def motion(self):
        return {"schema": 1, "source": "mediapipe_pose_landmarker", "robot_type": "leg", "target_person": "person-1", "demo": False,
                "physical_simulation": False, "mapping": {"method": "geometric_native_axes_v1", "joint_limits_enforced": True, "root_motion": False},
                "joint_names": ["hip"], "frames": [
                    {"time": 0, "source_time": 2, "person": "person-1", "confidence": .95, "q": [.1], "joints": {"hip": .1}},
                    {"time": .1, "source_time": 2.1, "person": "person-1", "confidence": .9, "q": [.2], "joints": {"hip": .2}},
                ]}

    def test_motion_preserves_mapping_and_is_linked_to_worker_plan(self):
        exported = self.motion()
        result = self.store.create_motion(exported)
        self.assertEqual((result["frame_count"], result["duration_s"]), (2, .1))
        saved = Store(self.root).get("motions", result["id"])
        self.assertEqual(saved["mapping"], exported["mapping"])
        self.assertEqual(saved["frames"], exported["frames"])
        self.assertFalse(saved["validation"]["physical_validation"])
        job = self.store.create_job(self.payload(mode="worker", motion_id=result["id"]))
        self.assertEqual(job["plan"]["motion"]["url"], result["url"])
        self.assertEqual(job["plan"]["motion"]["source"], "mediapipe_pose_landmarker")
        with self.assertRaises(APIError):
            self.store.create_job(self.payload("wheel", motion_id=result["id"]))

    def test_motion_rejects_unknown_joints_limits_nan_and_inconsistent_frames(self):
        changes = [
            lambda m: m.update(joint_names=["fixed"]),
            lambda m: m.update(urdf_sha256="wrong"),
            lambda m: m["frames"][0].update(q=[2], joints={"hip": 2}),
            lambda m: m["frames"][0].update(q=[float("nan")]),
            lambda m: m["frames"][1].update(time=0),
            lambda m: m["frames"][1].update(q=[.3]),
            lambda m: m["frames"][1].update(confidence=2),
            lambda m: m["frames"][1].update(joints={"unregistered": .2}),
        ]
        for change in changes:
            motion = self.motion()
            change(motion)
            with self.subTest(motion=motion), self.assertRaises(APIError):
                self.store.create_motion(motion)


    def test_reference_builder_resamples_subset_joints_and_blends_from_default_stance(self):
        from q1store.reference import ReferenceError, build_reference
        motion = {"id": "m1", "robot_type": "leg", "joint_names": ["hip", "extra"],
                  "frames": [{"time": 0, "q": [0.9, 0.3]}, {"time": .5, "q": [-0.2, 0.1]}, {"time": 1.0, "q": [0.4, 0.0]}]}
        reference = build_reference(motion, joint_names=["hip", "knee"], default=[0.1, 0.3], lower=[-1, -1], upper=[.5, 1],
                                    urdf_sha256="deadbeef", grounded_height=lambda q: .8 + .05 * q[0], job_id="job1")
        self.assertEqual(reference["joint_names"], ["hip", "knee"])
        self.assertEqual(reference["source"], "mediapipe")
        self.assertEqual(reference["clip"]["missing_joints_default"], ["knee"])
        self.assertEqual(len(reference["time"]), len(reference["q"]))
        self.assertEqual(len(reference["root"]), len(reference["q"]))
        self.assertTrue(all(b > a for a, b in zip(reference["time"], reference["time"][1:])))
        self.assertEqual(reference["q"][0], [0.1, 0.3])  # starts from the default stance
        self.assertAlmostEqual(reference["q"][-1][0], 0.1, places=9)  # returns to the default stance
        self.assertTrue(all(-1 <= q[0] <= .5 for q in reference["q"]))  # clamped to limits
        self.assertTrue(all(q[1] == 0.3 for q in reference["q"]))
        self.assertAlmostEqual(reference["duration_s"], 1.0 + 1.0 + .5, places=6)
        self.assertEqual(reference["root"][0][2], .8 + .05 * .1)
        with self.assertRaises(ReferenceError):
            build_reference({"joint_names": ["hip"], "frames": [{"time": 0, "q": [0]}, {"time": .2, "q": [0]}]}, joint_names=["hip"],
                            default=[0], lower=[-1], upper=[1], urdf_sha256="x", grounded_height=lambda q: .8)

    def test_worker_updates_are_visible_through_job_api_and_survive_store_restart(self):
        from q1store.worker import Worker, parse_metrics
        exported = self.motion()
        motion_id = self.store.create_motion(exported)["id"]
        job = self.store.create_job(self.payload(mode="worker", motion_id=motion_id))
        self.assertTrue(job["training"]["trainable"])
        self.assertEqual(job["stage_label"], "等待 Spark GPU worker")
        self.assertEqual(job["training"]["queue_position"], 1)
        worker = Worker(self.root, leg_root=self.root, num_envs=64, clock=lambda: self.now)
        self.assertEqual([j["id"] for j in worker.pending()], [job["id"]])
        worker.heartbeat("training", job["id"])
        directory = worker.job_dir(job["id"])
        (directory / "native.log").write_text("\x1b[1m Learning iteration 12/20 \x1b[0m\nSteps per second: 4790\nMean reward: 84.07\nMean episode length: 62.77\n")
        worker.update(job["id"], state="training", progress=42, training={"eta_s": 600, "eta_at": self.now + 600, "worker_pid": 999999, "num_envs": 64})
        live = Store(self.root, clock=lambda: self.now).job(job["id"])
        self.assertEqual((live["state"], live["progress"], live["stage_label"]), ("training", 42, "Isaac Lab PPO 訓練中"))
        self.assertTrue(live["worker"]["online"])
        self.assertIn("Mean reward: 84.07", live["log_tail"])
        self.assertEqual(live["log_url"], "/media/jobs/" + job["id"] + "/native.log")
        self.assertEqual(parse_metrics(live["log_tail"]), {"mean_reward": 84.07, "mean_episode_length": 62.77, "steps_per_second": 4790.0, "learning_iteration": 12})
        self.now += 100
        later = self.store.job(job["id"])
        self.assertEqual(later["training"]["eta_s"], 500)
        self.assertFalse(later["worker"]["online"])  # heartbeat went stale
        self.assertIn("worker 離線", later["stage_label"])
        self.assertEqual(self.store.jobs_summary()[0]["state"], "training")
        self.store.cancel(job["id"])
        self.assertEqual(worker.update(job["id"], state="complete", progress=100)["state"], "cancelled")
        self.assertEqual(self.store.job(job["id"])["state"], "cancelled")
        with self.assertRaises(APIError):
            self.store.job_media(job["id"], "result.mp4")
        with self.assertRaises(APIError):
            self.store.job_media(job["id"], "../store.sqlite3")
        path, content_type = self.store.job_media(job["id"], "native.log")
        self.assertEqual((path, content_type), (directory / "native.log", "text/plain; charset=utf-8"))

    def test_worker_fails_jobs_it_cannot_train_and_specs_report_completed_training(self):
        from q1store.worker import Worker
        worker = Worker(self.root, leg_root=self.root, num_envs=64, clock=lambda: self.now)
        planned = self.store.create_job(self.payload(mode="worker"))
        self.assertFalse(planned["training"]["trainable"])
        self.assertTrue(worker.run_once())
        failed = self.store.job(planned["id"])
        self.assertEqual(failed["state"], "failed")
        self.assertIn("關節參考", failed["error"])
        self.assertFalse(worker.run_once())
        (self.root / "assets/specs").mkdir()
        (self.root / "assets/specs/catalog.json").write_text(json.dumps({"schema": 1, "robots": {"leg": {"name": "Q1 Leg", "specs": []}},
            "motions": [{"id": "stand", "groups": ["hip", "wheel"], "support": {"leg": {"status": "store_trainable"}}}], "joint_groups": {}}))
        specs = self.store.specs()
        self.assertEqual(specs["robots"]["leg"]["urdf"]["control_joints"], 1)
        self.assertEqual([s["id"] for s in specs["robots"]["leg"]["specs"]], ["mass", "control", "limits"])
        self.assertEqual(specs["motions"][0]["support"]["leg"]["missing_groups"], ["wheel"])
        self.assertEqual(specs["trained"], [])
        motion_id = self.store.create_motion(self.motion())["id"]
        job = self.store.create_job(self.payload(mode="worker", motion_id=motion_id, action_name="起跳"))
        worker.update(job["id"], state="complete", progress=100, result={"newly_trained": True, "passed": False, "video_url": None,
                      "rollout_url": "/media/jobs/" + job["id"] + "/rollout.json", "provenance": "Spark PPO",
                      "mechanical": {"status": "simulated", "energy_j": 12.5, "mean_power_w": 2.1, "peak_power_w": 9.0, "scope": "sim"}})
        trained = self.store.specs()["trained"]
        self.assertEqual((trained[0]["label"], trained[0]["power"]["status"], trained[0]["power"]["energy_j"]), ("起跳", "simulated", 12.5))
        listing = self.store.create_listing({"job_id": job["id"], "title": "起跳 PPO", "price": 0})
        self.assertTrue(listing["newly_trained"])
        self.assertFalse(listing["is_demo"])
        with self.assertRaises(APIError):
            self.store.cancel(job["id"])


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root / "public/vendor").mkdir(parents=True)
        (cls.root / "public/index.html").write_text("<html>Q1 public app</html>")
        (cls.root / "public/vendor/test.js").write_text("export const working=true;")
        (cls.root / "assets").mkdir()
        (cls.root / "assets/test.mp4").write_bytes(b"0123456789")
        (cls.root / "assets/robots/leg").mkdir(parents=True)
        urdf = b'<robot name="test"><joint name="hip" type="revolute"><limit lower="-1" upper="1"/></joint></robot>'
        (cls.root / "assets/robots/leg/native.urdf").write_bytes(urdf)
        (cls.root / "assets/robots/manifest.json").write_text(json.dumps({"robots": [
            {"id": "leg", "urdf_url": "/assets/robots/leg/native.urdf", "urdf_sha256": hashlib.sha256(urdf).hexdigest(), "control_joints": ["hip"]}]}))
        (cls.root / "assets/examples").mkdir()
        (cls.root / "assets/examples/catalog.json").write_text(json.dumps({"examples": [
            {"id": "leg-example", "robot_type": "leg", "video_url": "/assets/test.mp4", "provenance": "historical demo"}]}))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.store = Store(cls.root)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_static_and_range_playback(self):
        self.assertIn(b"Q1 public app", self.request("GET", "/")[2])
        self.assertEqual(self.request("GET", "/vendor/test.js")[0], 200)
        status, headers, body = self.request("GET", "/assets/test.mp4", headers={"Range": "bytes=2-5"})
        self.assertEqual((status, body), (206, b"2345"))
        self.assertEqual(headers["Content-Range"], "bytes 2-5/10")
        self.assertEqual(self.request("GET", "/assets/test.mp4", headers={"Range": "bytes=90-"})[0], 416)
        self.assertEqual(self.request("HEAD", "/assets/test.mp4")[2], b"")
        self.assertEqual(self.request("HEAD", "/api/health")[2], b"")
        self.assertEqual(self.request("HEAD", "/.data/store.sqlite3")[0], 404)

    def test_job_list_specs_log_and_media_routes(self):
        status, _, body = self.request("GET", "/api/specs")
        self.assertEqual(status, 200)
        self.assertIn("trained", json.loads(body))
        status, _, body = self.request("GET", "/api/jobs")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["worker"]["online"], False)
        self.assertIsInstance(payload["jobs"], list)
        job = json.loads(self.request("POST", "/api/jobs", json.dumps({"robot_type": "leg", "mode": "worker", "source": {"type": "example", "id": "leg-example"},
                                                                       "config": {"rewards": ["balance"]}}).encode(), {"Content-Type": "application/json"})[2])
        self.assertEqual(job["state"], "queued_for_worker")
        status, _, body = self.request("GET", "/api/jobs/" + job["id"] + "/log?lines=5")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["log_tail"], [])
        self.assertEqual(self.request("GET", "/media/jobs/" + job["id"] + "/result.mp4")[0], 404)
        self.assertEqual(self.request("GET", "/media/jobs/" + job["id"] + "/../store.sqlite3")[0], 404)
        directory = self.server.store.jobs_dir / job["id"]
        directory.mkdir()
        (directory / "rollout.json").write_text('{"frames": []}')
        status, headers, body = self.request("GET", "/media/jobs/" + job["id"] + "/rollout.json")
        self.assertEqual((status, headers["Content-Type"], body), (200, "application/json", b'{"frames": []}'))
        self.assertEqual(json.loads(self.request("GET", "/api/health")[2])["gpu_worker"], False)

    def test_internal_files_are_inaccessible(self):
        for path in ["/.data/store.sqlite3", "/server.py", "/assets/../server.py", "/assets/%2e%2e/server.py", "/.git/config"]:
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_registry_and_deployment_zip_http_contract(self):
        status, _, body = self.request("POST", "/api/robots", json.dumps({"name": "HTTP Q1", "robot_type": "leg", "serial": "HTTP-001"}).encode(), {"Content-Type": "application/json"})
        self.assertEqual(status, 201)
        robot = json.loads(body)
        self.assertTrue(any(r["id"] == robot["id"] for r in json.loads(self.request("GET", "/api/robots")[2])["robots"]))
        store = self.server.store
        job = store.create_job({"robot_type": "leg", "source": {"type": "example", "id": "leg-example"}})
        job["created_at"] -= 31
        store.put("jobs", job)
        status, _, body = self.request("POST", "/api/deployments", json.dumps({"job_id": job["id"], "robot_id": robot["id"]}).encode(), {"Content-Type": "application/json"})
        self.assertEqual(status, 201)
        deployment = json.loads(body)
        self.assertFalse(deployment["device_acknowledged"])
        status, headers, content = self.request("GET", deployment["download_url"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/zip")
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(int(headers["Content-Length"]), len(content))
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertFalse(json.loads(archive.read("manifest.json"))["deployable"])
        self.assertEqual(self.request("HEAD", deployment["download_url"])[2], b"")
        self.assertEqual(len(json.loads(self.request("GET", "/api/deployments")[2])["deployments"]), 1)
        self.assertEqual(self.request("GET", "/.data/deployment-packages/" + deployment["id"] + ".zip")[0], 404)

    def test_api_errors_and_cross_site_posts_are_json(self):
        self.assertEqual(self.request("POST", "/api/jobs", b"{}", {"Content-Type": "application/json", "Origin": "https://evil.test"})[0], 403)
        status, headers, body = self.request("POST", "/api/jobs", b"[]", {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))
        self.assertEqual(self.request("GET", "/api/health")[0], 200)


if __name__ == "__main__":
    unittest.main()
