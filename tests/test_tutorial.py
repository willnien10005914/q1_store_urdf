"""Tutorial catalog, progress, quiz, sandboxed runs, proposals, LLM status, agent offline chat."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from q1store.agent import Agent, OfflineTutor, TagStreamer
from q1store.llm import LLMManager, MODELS
from q1store.service import Store
from q1store.tutorial import Tutorial, parse_front_matter, dump_front_matter
from server import Handler, attach_services


ROOT = Path(__file__).resolve().parent.parent


class TutorialUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.t = Tutorial(ROOT, self.data, start_threads=False)

    def tearDown(self):
        self.temp.cleanup()

    def test_catalog_has_eight_lessons_and_categories(self):
        cat = self.t.catalog()
        ids = [m["id"] for m in self.t.lessons()]
        self.assertEqual(len(ids), 8)
        self.assertTrue(all(i.startswith("l0") for i in ids))
        self.assertEqual(cat["lesson_count"], 8)
        self.assertGreaterEqual(len(cat["categories"]), 8)
        self.assertIn("novice", {l["id"] for l in cat["levels"]})

    def test_register_same_name_reuses_progress(self):
        a = self.t.register_user("Ada")
        b = self.t.register_user("ada")
        self.assertEqual(a["user"]["id"], b["user"]["id"])
        self.t.start_lesson(a["user"]["id"], "l01-isaaclab3-overview", "leg")
        progress = self.t.progress(a["user"]["id"])
        self.assertEqual(progress["in_progress"], 1)
        self.assertEqual(progress["level"]["id"], "novice")

    def test_quiz_grading_and_level(self):
        uid = self.t.register_user("Bob")["user"]["id"]
        meta = self.t.lesson_meta("l03-migrating-2x-to-3")
        answers = {q["id"]: q["answer"] for q in meta["quiz"]}
        result = self.t.grade_quiz(uid, "l03-migrating-2x-to-3", answers)
        self.assertEqual(result["score"], 100)
        self.assertTrue(result["passed"])
        self.assertEqual(result["progress"]["completed"], 1)

    def test_l03_scanner_run_completes(self):
        uid = self.t.register_user("Carol")["user"]["id"]
        run = self.t.create_run(uid, "l03-migrating-2x-to-3", "leg", {"max_findings": 20})
        for _ in range(40):
            run = self.t.run(run["id"])
            if run["state"] in ("complete", "failed"):
                break
            time.sleep(0.25)
        self.assertEqual(run["state"], "complete", run.get("error") or run.get("log_tail"))
        self.assertGreater(run["result"]["total_findings"], 0)

    def test_l07_fallback_rollout_without_isaaclab3(self):
        uid = self.t.register_user("Dana")["user"]["id"]
        run = self.t.create_run(uid, "l07-visualizers-and-recording", "wheel", {"sim_seconds": 1, "fps": 10, "viz": "none"})
        for _ in range(40):
            run = self.t.run(run["id"])
            if run["state"] in ("complete", "failed", "blocked"):
                break
            time.sleep(0.25)
        self.assertEqual(run["state"], "complete", run.get("error"))
        self.assertEqual(run["runtime"]["mode"], "python-fallback")
        self.assertIn("rollout.json", run["files"])

    def test_isaac_lesson_blocks_without_checkout(self):
        uid = self.t.register_user("Eve")["user"]["id"]
        run = self.t.create_run(uid, "l01-isaaclab3-overview", "leg", {})
        self.assertEqual(run["state"], "blocked")
        self.assertIn("Q1_ISAACLAB3_ROOT", run["error"])

    def test_proposal_lifecycle(self):
        before = self.t.proposal_counts()
        item = self.t.write_proposal("Newton soft contact test", "compare solref", "physics",
                                     [{"type": "docs", "title": "x", "url": "https://example.com"}], "# body\n")
        self.addCleanup(lambda: self._purge_proposal(item["id"]))
        self.assertEqual(self.t.proposal_counts()["pending"], before["pending"] + 1)
        self.t.set_proposal_status(item["id"], "approved")
        self.assertEqual(self.t.proposal(item["id"])["status"], "approved")
        prompt = self.t.cursor_prompt(item["id"])
        self.assertIn("isaac_v3", prompt)
        self.assertIn(item["id"], prompt)

    def _purge_proposal(self, proposal_id):
        for status in ("pending", "approved", "rejected"):
            path = self.t.proposals_dir / status / f"{proposal_id}.md"
            if path.exists():
                path.unlink()

    def test_front_matter_roundtrip(self):
        meta = {"title": "T", "sources": ["a", "b"], "status": "pending"}
        text = dump_front_matter(meta, "hello")
        parsed, body = parse_front_matter(text)
        self.assertEqual(parsed["title"], "T")
        self.assertEqual(parsed["sources"], ["a", "b"])
        self.assertIn("hello", body)

    def test_discovery_propose_from_search_hit(self):
        fake = {"title": "Isaac Lab 3.0 Newton tutorial test", "url": "https://example.com/isaac-lab-3-test",
                "snippet": "new physics backend mujoco warp", "type": "docs", "topic": "Isaac Lab 3", "score": 5}
        prop = self.t.discovery.propose_from(fake)
        self.addCleanup(lambda: self._purge_proposal(prop["id"]))
        self.assertEqual(prop["folder"], "pending")
        self.assertIn("Isaac Lab", prop["meta"]["title"])


class LlmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.llm = LLMManager(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_status_lists_models_without_claiming_ready(self):
        status = self.llm.status()
        self.assertEqual(len(status["models"]), len(MODELS))
        self.assertFalse(status["chat_ready"])
        self.assertIn("runtimes", status)
        self.assertIn("gpu", status)

    def test_unknown_model_download_raises(self):
        with self.assertRaises(ValueError):
            self.llm.start_download("no-such-model")


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.t = Tutorial(ROOT, self.temp.name, start_threads=False)
        self.uid = self.t.register_user("Fran")["user"]["id"]
        self.agent = Agent(self.t, llm=None)

    def tearDown(self):
        self.temp.cleanup()

    def test_tag_streamer_splits_think_and_tool(self):
        s = TagStreamer()
        events = []
        for chunk in ["<think>hmm", "</think>answer ", "<tool_call>", '{"name":"web_search","arguments":{"query":"x"}}', "</tool_call>"]:
            events += s.feed(chunk)
        events += s.feed("", final=True)
        types = [e["type"] for e in events]
        self.assertIn("reasoning", types)
        self.assertIn("token", types)
        self.assertIn("tool_call", types)
        self.assertEqual(s.tool_calls[0]["name"], "web_search")

    def test_offline_help_and_params(self):
        lesson = self.t.lesson("l02-multi-backend-physics", robot="leg", user_id=self.uid)
        events = list(self.agent.chat(self.uid, lesson["id"], "/help", {"robot": "leg", "params": {}}))
        self.assertTrue(any(e.get("type") == "done" for e in events))
        self.assertTrue(any(e.get("mode") == "offline" for e in events if e.get("type") in ("status", "done")))
        events = list(self.agent.chat(self.uid, lesson["id"], "/params num_envs=64", {"robot": "leg", "params": {}}))
        self.assertTrue(any(e.get("type") == "params" for e in events))


class TutorialHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root / "public").mkdir()
        (cls.root / "public/index.html").write_text("<html>home</html>")
        (cls.root / "public/tutorial.html").write_text("<html>tutorial</html>")
        # Point tutorials at the real repo content via symlink so HTTP tests exercise real lessons.
        (cls.root / "tutorials").symlink_to(ROOT / "tutorials")
        (cls.root / "assets").mkdir()
        (cls.root / "assets/robots").mkdir()
        urdf = b'<robot name="t"><joint name="hip" type="revolute"><limit lower="-1" upper="1"/></joint></robot>'
        (cls.root / "assets/robots/leg").mkdir(parents=True)
        (cls.root / "assets/robots/leg/native.urdf").write_bytes(urdf)
        (cls.root / "assets/robots/manifest.json").write_text(json.dumps({"robots": [
            {"id": "leg", "name": "Leg", "urdf_url": "/assets/robots/leg/native.urdf", "control_joints": ["hip"],
             "default_joint_positions": {"hip": 0.0}},
            {"id": "wheel", "name": "Wheel", "urdf_url": "/assets/robots/leg/native.urdf", "control_joints": ["hip"],
             "default_joint_positions": {"hip": 0.0}},
        ]}))
        (cls.root / "assets/examples").mkdir()
        (cls.root / "assets/examples/catalog.json").write_text(json.dumps({"examples": []}))
        (cls.root / "assets/video-examples").mkdir()
        (cls.root / "assets/video-examples/catalog.json").write_text(json.dumps({"examples": [], "sources": []}))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.store = Store(cls.root)
        attach_services(cls.server, start_threads=False)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, method, path, body=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=30)
        payload = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json", "Content-Length": str(len(payload or b""))}
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, json.loads(data) if data and resp.getheader("Content-Type", "").startswith("application/json") else data

    def test_tutorial_page_and_catalog(self):
        status, body = self.request("GET", "/tutorial")
        self.assertEqual(status, 200)
        self.assertIn(b"tutorial", body)
        status, data = self.request("GET", "/api/tutorial/catalog")
        self.assertEqual(status, 200)
        self.assertEqual(data["lesson_count"], 8)

    def test_user_quiz_and_llm_status(self):
        status, data = self.request("POST", "/api/tutorial/users", {"name": "HTTP-User"})
        self.assertEqual(status, 200)
        uid = data["user"]["id"]
        status, lesson = self.request("GET", f"/api/tutorial/lessons/l08-policy-export-deployment?user={uid}&robot=leg")
        self.assertEqual(status, 200)
        self.assertNotIn("answer", lesson["quiz"][0])  # answers stripped for the client
        answers = {q["id"]: 0 for q in self.server.tutorial.lesson_meta("l08-policy-export-deployment")["quiz"]}
        # force correct answers from server-side meta
        meta = self.server.tutorial.lesson_meta("l08-policy-export-deployment")
        answers = {q["id"]: q["answer"] for q in meta["quiz"]}
        status, graded = self.request("POST", f"/api/tutorial/users/{uid}/lessons/l08-policy-export-deployment/quiz", {"answers": answers})
        self.assertEqual(status, 200)
        self.assertEqual(graded["score"], 100)
        status, llm = self.request("GET", "/api/llm/status")
        self.assertEqual(status, 200)
        self.assertIn("models", llm)
        self.assertFalse(llm["chat_ready"])

    def test_discovery_toggle(self):
        status, data = self.request("POST", "/api/tutorial/discovery", {"enabled": True, "interval_minutes": 60})
        self.assertEqual(status, 200)
        self.assertTrue(data["enabled"])
        status, data = self.request("POST", "/api/tutorial/discovery", {"enabled": False})
        self.assertFalse(data["enabled"])


if __name__ == "__main__":
    unittest.main()
