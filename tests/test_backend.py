"""Meaningful state, persistence, upload, and HTTP isolation checks; no GPU needed."""
import json
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

    def test_youtube_url_is_canonical_and_rejects_arbitrary_network_targets(self):
        self.assertEqual(youtube_url("https://youtu.be/abcdefghijk?t=4"), "https://www.youtube.com/watch?v=abcdefghijk")
        for value in ["http://127.0.0.1/", "https://youtube.com.evil.test/watch?v=abcdefghijk", "https://youtube.com:abc/watch?v=abcdefghijk", "https://youtube.com/playlist?list=abc", "https://youtube.com@127.0.0.1/watch?v=abcdefghijk", "x" * 3000, [], "https://youtube.com/watch/../watch?v=abcdefghijk"]:
            with self.subTest(value=value), self.assertRaises(APIError):
                youtube_url(value)

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

    def test_internal_files_are_inaccessible(self):
        for path in ["/.data/store.sqlite3", "/server.py", "/assets/../server.py", "/assets/%2e%2e/server.py", "/.git/config"]:
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_api_errors_and_cross_site_posts_are_json(self):
        self.assertEqual(self.request("POST", "/api/jobs", b"{}", {"Content-Type": "application/json", "Origin": "https://evil.test"})[0], 403)
        status, headers, body = self.request("POST", "/api/jobs", b"[]", {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))
        self.assertEqual(self.request("GET", "/api/health")[0], 200)


if __name__ == "__main__":
    unittest.main()
