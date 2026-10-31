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

from q1store.agent import (
    Agent, OfflineTutor, TagStreamer, inject_progress_logging, EDIT_HINT, REPAIR_HINT,
    EXAMPLE_API_HINT, ISAAC_SIM_MISSING_HINT, apply_simple_code_intent, extract_fenced_python,
    fix_known_example_api_errors, heal_stale_example_draft, normalize_hermes_tags,
    strip_leaked_hermes_tags,
)
from q1store.llm import LLMManager, MODELS
from q1store.service import Store
from q1store.tutorial import (
    Tutorial, parse_front_matter, dump_front_matter,
    sanitize_rollout_file, render_rollout_preview, MEDIA,
)
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
        # Force the kitless python path so this stays fast even when Isaac Lab 3 is installed.
        self.t.isaaclab3_root = None
        run = self.t.create_run(
            uid, "l07-visualizers-and-recording", "wheel",
            {"sim_seconds": 1, "fps": 10, "viz": "none"},
        )
        for _ in range(80):
            run = self.t.run(run["id"])
            if run["state"] in ("complete", "failed", "blocked"):
                break
            time.sleep(0.25)
        self.assertEqual(run["state"], "complete", run.get("error"))
        self.assertEqual(run["runtime"]["mode"], "python-fallback")
        self.assertIn("rollout.json", run["files"])
        # Finalize should rewrite NaN-free JSON (browsers reject bare NaN via JSON.parse).
        text = (self.t.run_dir(run["id"]) / "rollout.json").read_text()
        self.assertNotIn("NaN", text)
        json.loads(text)
        self.assertTrue(
            "result.gif" in run["files"] or "result.mp4" in run["files"] or "rollout.json" in run["files"]
        )

    def test_sanitize_rollout_rewrites_bare_nan(self):
        path = self.data / "bad_rollout.json"
        path.write_text(
            '{"fps": 10, "joint_names": ["j0"], "frames": ['
            '{"t": 0.0, "root_pos": [NaN, 0.0, 0.8], "root_quat": [0, 0, 0, 1], "q": [0.1]},'
            '{"t": 0.1, "root_pos": [0.1, Infinity, 0.8], "root_quat": [0, 0, 0, 1], "q": [NaN]}'
            ']}'
        )
        self.assertTrue(sanitize_rollout_file(path))
        data = json.loads(path.read_text())  # strict — no bare NaN
        self.assertEqual(data["frames"][0]["root_pos"][0], 0.0)  # NaN → previous/fallback
        self.assertEqual(data["frames"][1]["root_pos"][1], 0.0)
        self.assertEqual(data["frames"][1]["q"][0], 0.1)
        self.assertIn("result.gif", MEDIA)
        self.assertEqual(MEDIA["result.gif"], "image/gif")

    def test_run_media_sanitizes_rollout_on_serve(self):
        uid = self.t.register_user("DanaMedia")["user"]["id"]
        run_id = "a" * 32
        directory = self.t.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "rollout.json").write_text(
            '{"fps": 5, "frames": [{"t": 0, "root_pos": [NaN, NaN, NaN], "root_quat": [0,0,0,1], "q": [0]}]}'
        )
        self.t.put("runs", {
            "id": run_id, "user_id": uid, "lesson_id": "l07-visualizers-and-recording",
            "state": "complete", "params": {},
        }, k1=uid, k2="l07-visualizers-and-recording")
        path, ctype = self.t.run_media(run_id, "rollout.json")
        self.assertEqual(ctype, "application/json")
        payload = json.loads(path.read_text())
        self.assertTrue(all(isinstance(v, (int, float)) for v in payload["frames"][0]["root_pos"]))
        self.assertNotIn("NaN", path.read_text())

    def test_render_rollout_preview_writes_gif_or_mp4(self):
        directory = self.data / "preview_run"
        directory.mkdir()
        frames = []
        for i in range(12):
            frames.append({
                "t": i * 0.1,
                "root_pos": [0.0, 0.0, 0.8 + 0.01 * i],
                "root_quat": [0.0, 0.0, 0.0, 1.0],
                "q": [0.1 * i, -0.05 * i],
            })
        (directory / "rollout.json").write_text(json.dumps({
            "fps": 10, "joint_names": ["left_hip", "right_hip"], "frames": frames,
        }))
        out = render_rollout_preview(directory, ffmpeg=self.t.ffmpeg)
        # matplotlib may be missing in minimal envs — then None is OK; otherwise media exists.
        if out is None:
            self.skipTest("matplotlib/ffmpeg unavailable for preview")
        self.assertIn(out, ("result.mp4", "result.gif"))
        self.assertTrue((directory / out).is_file())

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
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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

    def test_tag_streamer_accepts_html_escaped_think(self):
        self.assertEqual(normalize_hermes_tags("&lt;think&gt;x&lt;/think&gt;"), "<think>x</think>")
        self.assertEqual(
            normalize_hermes_tags("&amp;lt;think&amp;gt;&amp;lt;/think&amp;gt;"),
            "<think></think>",
        )
        self.assertEqual(strip_leaked_hermes_tags("&lt;think&gt;&lt;/think&gt;hello"), "hello")
        s = TagStreamer()
        events = []
        # Stream escaped tags one character at a time (regression for HOLDBACK / leak).
        for ch in "&lt;think&gt;why&lt;/think&gt;ok":
            events += s.feed(ch)
        events += s.feed("", final=True)
        tokens = "".join(e["text"] for e in events if e["type"] == "token")
        reasoning = "".join(e["text"] for e in events if e["type"] == "reasoning")
        self.assertEqual(reasoning, "why")
        self.assertEqual(tokens, "ok")
        self.assertNotIn("think", tokens.lower())
        self.assertNotIn("&lt;", tokens)
        self.assertNotIn("&amp;", tokens)

    def test_offline_help_and_params(self):
        lesson = self.t.lesson("l02-multi-backend-physics", robot="leg", user_id=self.uid)
        events = list(self.agent.chat(self.uid, lesson["id"], "/help", {"robot": "leg", "params": {}}))
        self.assertTrue(any(e.get("type") == "done" for e in events))
        self.assertTrue(any(e.get("mode") == "offline" for e in events if e.get("type") in ("status", "done")))
        events = list(self.agent.chat(self.uid, lesson["id"], "/params num_envs=64", {"robot": "leg", "params": {}}))
        self.assertTrue(any(e.get("type") == "params" for e in events))

    def test_diagnose_runtime_tool(self):
        lesson = self.t.lesson("l01-isaaclab3-overview", robot="leg", user_id=self.uid)
        result, events = self.agent.run_tool(self.uid, lesson, {"robot": "leg"}, "diagnose_runtime", {})
        self.assertIn("issues", result)
        self.assertIn("Q1_ISAACLAB3_ROOT", result)

    def test_offline_fix_reads_run_without_asking_which_lesson(self):
        lesson = self.t.lesson("l01-isaaclab3-overview", robot="leg", user_id=self.uid)
        events = list(self.agent.chat(
            self.uid, lesson["id"], "請修復執行錯誤",
            {"robot": "leg", "params": {}, "last_run": {"id": "x", "state": "failed",
             "error": "RuntimeError: driver too old", "log_tail": "torch.cuda"}},
        ))
        types = [e.get("type") for e in events]
        self.assertIn("tool_call", types)
        self.assertTrue(any(e.get("name") == "diagnose_runtime" for e in events if e.get("type") == "tool_call"))
        done = next(e for e in events if e.get("type") == "done")
        self.assertEqual(done.get("mode"), "offline")

    def _seed_complete_run(self, lesson_id="l07-visualizers-and-recording", robot="wheel"):
        import uuid
        run_id = uuid.uuid4().hex
        directory = self.t.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        result = {"ok": True, "physics": "fallback", "frames": 10, "survived": True, "message": "seeded"}
        (directory / "result.json").write_text(json.dumps(result), encoding="utf-8")
        (directory / "evaluation.json").write_text(json.dumps({"passed": True, "score": 1.0}), encoding="utf-8")
        (directory / "rollout.json").write_text(json.dumps({
            "fps": 10, "joint_names": ["j0"], "frames": [{"t": 0.0, "q": [0.1]}, {"t": 0.1, "q": [0.2]}],
        }), encoding="utf-8")
        (directory / "result.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
        (directory / "run.log").write_text("seeded run\n", encoding="utf-8")
        run = {
            "id": run_id, "user_id": self.uid, "lesson_id": lesson_id, "lesson_title": "seed",
            "robot": robot, "params": {}, "code_sha256": "0" * 64, "code_modified": False,
            "runtime": {"mode": "python-fallback", "label": "python"}, "trainable": False, "resumable": False,
            "state": "complete", "phase": "complete", "progress": {"phase": "complete"}, "result": result,
            "error": None, "pid": None, "created_at": "2026-10-31T00:00:00Z", "started_at": "2026-10-31T00:00:00Z",
            "finished_at": "2026-10-31T00:00:01Z", "resumed": 0,
        }
        self.t.put("runs", run, k1=self.uid, k2=lesson_id)
        return run_id

    def test_analyze_run_reads_artifacts(self):
        lesson = self.t.lesson("l07-visualizers-and-recording", robot="wheel", user_id=self.uid)
        run_id = self._seed_complete_run()
        info, _ = self.agent.run_tool(self.uid, lesson, {"robot": "wheel", "last_run": {"id": run_id}}, "analyze_run", {})
        self.assertTrue(info.get("ok"), info)
        self.assertIn("rollout.json", info.get("files") or [])
        self.assertIn("result.mp4", info.get("files") or [])
        self.assertTrue(info.get("video_url"))
        self.assertTrue(info.get("rollout_url"))
        self.assertTrue((info.get("evaluation") or {}).get("passed"))
        self.assertIn("summary", info)

    def test_offline_analyze_command(self):
        lesson = self.t.lesson("l07-visualizers-and-recording", robot="wheel", user_id=self.uid)
        run_id = self._seed_complete_run()
        events = list(self.agent.chat(
            self.uid, lesson["id"], "請分析結果",
            {"robot": "wheel", "params": {}, "last_run": {"id": run_id}},
        ))
        self.assertTrue(any(e.get("name") == "analyze_run" for e in events if e.get("type") == "tool_call"))
        tokens = "".join(e.get("text", "") for e in events if e.get("type") == "token")
        self.assertIn("執行分析", tokens)

    def test_offline_add_log_applies_to_editor(self):
        self.assertTrue(EDIT_HINT.search("請在第一課程加上 log"))
        lesson = self.t.lesson("l01-isaaclab3-overview", robot="leg", user_id=self.uid)
        code = (ROOT / "tutorials/lessons/l01-isaaclab3-overview/example.py").read_text()
        instrumented, _ = inject_progress_logging(code, lesson["id"])
        self.assertIn("[hermes-log]", instrumented)
        self.assertNotEqual(instrumented, code)
        events = list(self.agent.chat(
            self.uid, lesson["id"], "請在第一課程加上 log",
            {"robot": "leg", "params": {}, "code": code},
        ))
        apply = next(e for e in events if e.get("type") == "code_apply")
        self.assertEqual(apply.get("author"), "Hermes")
        self.assertIn("[hermes-log]", apply.get("code") or "")
        draft = self.t.progress_row(self.uid, lesson["id"]).get("draft_code") or ""
        self.assertIn("[hermes-log]", draft)

    def test_fix_newtoncfg_solver_iterations_applies_code(self):
        err = (
            'File "example.py", line 45, in <module>\n'
            "    sim_cfg.physics = NewtonCfg(solver_iterations=ITERS)\n"
            "TypeError: NewtonCfg.__init__() got an unexpected keyword argument 'solver_iterations'\n"
        )
        self.assertTrue(EXAMPLE_API_HINT.search(err))
        broken = (
            "from isaaclab_newton.physics import NewtonCfg\n"
            "sim_cfg.physics = NewtonCfg(solver_iterations=ITERS)\n"
        )
        fixed, note = fix_known_example_api_errors(broken, err, "l02-multi-backend-physics")
        self.assertIn("MJWarpSolverCfg(iterations=ITERS)", fixed)
        self.assertIn("NewtonCfg", note)
        lesson = self.t.lesson("l02-multi-backend-physics", robot="leg", user_id=self.uid)
        import uuid
        run_id = uuid.uuid4().hex
        directory = self.t.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "run.log").write_text(err, encoding="utf-8")
        run = {
            "id": run_id, "user_id": self.uid, "lesson_id": lesson["id"], "lesson_title": "l02",
            "robot": "leg", "params": {}, "code_sha256": "0" * 64, "code_modified": True,
            "runtime": {"mode": "isaaclab3", "label": "Isaac Lab 3"}, "trainable": False, "resumable": False,
            "state": "failed", "phase": None, "progress": None, "result": None,
            "error": "exit code 1; 見 run.log", "pid": None,
            "created_at": "2026-10-31T00:00:00Z", "started_at": "2026-10-31T00:00:00Z",
            "finished_at": "2026-10-31T00:00:01Z", "resumed": 0,
        }
        self.t.put("runs", run, k1=self.uid, k2=lesson["id"])
        events = list(self.agent.chat(
            self.uid, lesson["id"], "請修復執行錯誤",
            {"robot": "leg", "params": {}, "code": broken, "last_run": {
                "id": run_id, "state": "failed", "error": "exit code 1; 見 run.log", "log_tail": err,
            }},
        ))
        apply = next(e for e in events if e.get("type") == "code_apply")
        self.assertIn("MJWarpSolverCfg", apply.get("code") or "")
        tokens = "".join(e.get("text", "") for e in events if e.get("type") == "token")
        self.assertIn("example.py API", tokens)
        self.assertIn("q1_store_urdf", tokens)
        self.assertNotIn("請貼出關鍵行", tokens)

    def test_fix_l06_rsl_rl_actor_class_name(self):
        err = "KeyError: 'class_name'\n  File \"example.py\", line 179\n"
        broken = (
            "from isaaclab_rl.rsl_rl import RslRlOnPolicyRunnerCfg, RslRlPpoActorCriticCfg, RslRlPpoAlgorithmCfg\n"
            "policy = RslRlPpoActorCriticCfg(init_noise_std=1.0, actor_hidden_dims=[512, 256, 128], "
            "critic_hidden_dims=[512, 256, 128], activation=\"elu\")\n"
        )
        canon = (ROOT / "tutorials/lessons/l06-manager-based-ppo/example.py").read_text()
        fixed, note = fix_known_example_api_errors(
            broken, err, "l06-manager-based-ppo", canonical_code=canon,
        )
        body = "\n".join(line for line in fixed.splitlines() if not line.startswith("# [Hermes]"))
        self.assertIn("RslRlMLPModelCfg", body)
        self.assertIn("actor =", body)
        self.assertIn("handle_deprecated_rsl_rl_cfg", body)
        self.assertNotIn("RslRlPpoActorCriticCfg", body)
        self.assertIn("MLPModel", note)

    def test_fix_l06_nefc_nan_ships_canonical(self):
        err = (
            "solver iterations limit reached - please increase iterations to 100\n"
            "nefc overflow - please increase njmax to 314\n"
            'File "example.py", line 222, in <module>\n'
            "    runner.learn(num_learning_iterations=chunk, init_at_random_ep_len=True)\n"
            "ValueError: The observation group 'policy' returned by the environment contains NaN values.\n"
        )
        self.assertTrue(EXAMPLE_API_HINT.search(err))
        broken = (
            "from isaaclab_newton.physics import NewtonCfg\n"
            "sim_cfg.physics = NewtonCfg()\n"
            "runner = OnPolicyRunner(wrapped, runner_cfg.to_dict(), log_dir=str(JOB), device=env.device)\n"
        )
        canon = (ROOT / "tutorials/lessons/l06-manager-based-ppo/example.py").read_text()
        fixed, note = fix_known_example_api_errors(
            broken, err, "l06-manager-based-ppo", canonical_code=canon,
        )
        body = "\n".join(line for line in fixed.splitlines() if not line.startswith("# [Hermes]"))
        self.assertIn("njmax", body)
        self.assertIn("MJWarpSolverCfg", body)
        self.assertIn("implicitfast", body)
        self.assertRegex(note.lower(), r"nefc|njmax|nan")

    def test_heal_stale_l06_draft_without_traceback(self):
        broken = (
            "from isaaclab_newton.physics import NewtonCfg\n"
            "sim_cfg.physics = NewtonCfg()\n"
            "from rsl_rl.runners import OnPolicyRunner\n"
            "runner = OnPolicyRunner(wrapped, runner_cfg.to_dict(), log_dir=str(JOB), device=env.device)\n"
            "runner.learn(num_learning_iterations=chunk, init_at_random_ep_len=True)\n"
        )
        canon = (ROOT / "tutorials/lessons/l06-manager-based-ppo/example.py").read_text()
        fixed, note = heal_stale_example_draft(broken, "l06-manager-based-ppo", canon)
        self.assertIn("njmax", fixed)
        self.assertIn("MJWarpSolverCfg", fixed)
        self.assertTrue(note)

    def test_create_run_heals_stale_l06_newtoncfg_draft(self):
        broken = (
            "from isaaclab_newton.physics import NewtonCfg\n"
            "sim_cfg.physics = NewtonCfg()\n"
            "from rsl_rl.runners import OnPolicyRunner\n"
            "runner = OnPolicyRunner(wrapped, runner_cfg.to_dict(), log_dir='.', device='cpu')\n"
            "runner.learn(num_learning_iterations=1, init_at_random_ep_len=True)\n"
        )
        # Block actual launch: runtime will be isaaclab3 but we cancel after create by not pumping —
        # create_run only queues; with start_threads=False _pump is still called but may no-op.
        run = self.t.create_run(self.uid, "l06-manager-based-ppo", "leg", {"num_envs": 16, "max_iterations": 10}, broken)
        self.assertTrue(run.get("healed"))
        written = (self.t.run_dir(run["id"]) / "example.py").read_text()
        self.assertIn("njmax", written)
        self.assertIn("MJWarpSolverCfg", written)
        self.assertNotRegex(written, r"NewtonCfg\s*\(\s*\)")
        draft = self.t.progress_row(self.uid, "l06-manager-based-ppo").get("draft_code") or ""
        self.assertIn("njmax", draft)

    def test_fix_l06_stochastic_ships_canonical(self):
        err = (
            'File "example.py", line 186, in <module>\n'
            "    runner = OnPolicyRunner(wrapped, runner_cfg.to_dict(), log_dir=str(JOB), device=env.device)\n"
            "TypeError: MLPModel.__init__() got an unexpected keyword argument 'stochastic'\n"
        )
        self.assertTrue(EXAMPLE_API_HINT.search(err))
        broken = (
            "from isaaclab_rl.rsl_rl import RslRlMLPModelCfg, RslRlOnPolicyRunnerCfg\n"
            "from rsl_rl.runners import OnPolicyRunner\n"
            "runner_cfg = Q1RunnerCfg()\n"
            "runner = OnPolicyRunner(wrapped, runner_cfg.to_dict(), log_dir=str(JOB), device=env.device)\n"
        )
        canon = (ROOT / "tutorials/lessons/l06-manager-based-ppo/example.py").read_text()
        fixed, note = fix_known_example_api_errors(
            broken, err, "l06-manager-based-ppo", canonical_code=canon,
        )
        body = "\n".join(line for line in fixed.splitlines() if not line.startswith("# [Hermes]"))
        self.assertIn("handle_deprecated_rsl_rl_cfg", body)
        self.assertIn("check_rsl_rl_version", body)
        self.assertIn("stochastic", note.lower() + body.lower())

    def test_fix_l07_visualizer_kind_ships_canonical(self):
        err = (
            'File "example.py", line 115, in <module>\n'
            '    visualizers.append(VisualizerCfg(kind="viser", port=PORT))\n'
            "TypeError: VisualizerCfg.__init__() got an unexpected keyword argument 'kind'\n"
        )
        self.assertTrue(EXAMPLE_API_HINT.search(err))
        broken = (
            "from isaaclab.visualizers import VisualizerCfg\n"
            "visualizers = []\n"
            'visualizers.append(VisualizerCfg(kind="viser", port=8081, eye=(2.5, 2.5, 1.5), lookat=(0.0, 0.0, 0.6)))\n'
            "sim_cfg.visualizers = visualizers\n"
            'recorder = VideoRecorderCfg(source="viz:kit", fps=30, length=10, output_dir="." ).create()\n'
        )
        canon = (ROOT / "tutorials/lessons/l07-visualizers-and-recording/example.py").read_text()
        fixed, note = fix_known_example_api_errors(
            broken, err, "l07-visualizers-and-recording", canonical_code=canon,
        )
        body = "\n".join(line for line in fixed.splitlines() if not line.startswith("# [Hermes]"))
        self.assertIn("ViserVisualizerCfg", body)
        self.assertIn("visualizer_cfgs", body)
        self.assertNotIn('VisualizerCfg(kind=', body)
        self.assertIn("visualizer", note.lower())

    def test_runtime_l08_prefers_isaaclab_venv_python(self):
        meta = self.t.lesson_meta("l08-policy-export-deployment")
        runtime = self.t.runtime_for(meta)
        self.assertEqual(runtime["mode"], "python")
        lab = self.t.isaaclab3_root
        if lab and (lab / ".venv" / "bin" / "python").is_file():
            self.assertEqual(runtime["command"][0], str(lab / ".venv" / "bin" / "python"))
            self.assertIn("Isaac Lab 3 venv", runtime["label"])

    def test_runtime_l07_adds_viser_extra(self):
        meta = self.t.lesson_meta("l07-visualizers-and-recording")
        runtime = self.t.runtime_for(meta, {"viz": "viser"})
        if runtime.get("mode") == "isaaclab3":
            cmd = runtime["command"]
            self.assertIn("--extra", cmd)
            # importers + viser
            extras = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--extra"]
            self.assertIn("importers", extras)
            self.assertIn("viser", extras)

    def test_fix_get_masses_to_body_mass(self):
        err = "AttributeError: 'ArticulationView' object has no attribute 'get_masses'\n"
        broken = "masses = robot.root_view.get_masses() if hasattr(robot, \"root_view\") else None\n"
        fixed, note = fix_known_example_api_errors(broken, err, "l04-urdf-to-usd-articulation")
        self.assertIn("robot.data.body_mass.torch", fixed)
        body = "\n".join(line for line in fixed.splitlines() if not line.startswith("# [Hermes]"))
        self.assertNotIn("get_masses()", body)
        self.assertIn("body_mass", note)

    def test_fix_l04_isaac_sim_missing_applies_kitless(self):
        err = (
            "[ERROR] Isaac Sim is not installed or not found on PYTHONPATH.\n"
            "  PhysX backend and Kit visualizer currently requires Isaac Sim.\n"
        )
        self.assertTrue(ISAAC_SIM_MISSING_HINT.search(err))
        self.assertTrue(EXAMPLE_API_HINT.search(err))
        broken = (
            "from isaaclab.app import launch_simulation\n"
            "from isaaclab.sim import SimulationCfg\n"
            "from isaaclab_physx.physics import PhysxCfg\n"
            "sim_cfg = SimulationCfg(dt=1 / 200, device=\"cuda:0\", physics=PhysxCfg())\n"
            "with launch_simulation(sim_cfg):\n"
            "    from isaaclab.sim.converters import UrdfConverter, UrdfConverterCfg\n"
            "    usd_path = UrdfConverter(UrdfConverterCfg(asset_path=P['urdf'])).usd_path\n"
        )
        canon = (ROOT / "tutorials/lessons/l04-urdf-to-usd-articulation/example.py").read_text()
        fixed, note = fix_known_example_api_errors(
            broken, err, "l04-urdf-to-usd-articulation", canonical_code=canon,
        )
        self.assertIn("NewtonCfg", fixed)
        self.assertIn("UrdfFileCfg", fixed)
        self.assertIn("newton_mjwarp", fixed)
        self.assertIn("[Hermes]", fixed)
        self.assertIn("kitless", note.lower() + fixed.lower())
        lesson = self.t.lesson("l04-urdf-to-usd-articulation", robot="leg", user_id=self.uid)
        import uuid
        run_id = uuid.uuid4().hex
        directory = self.t.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "run.log").write_text(err, encoding="utf-8")
        run = {
            "id": run_id, "user_id": self.uid, "lesson_id": lesson["id"], "lesson_title": "l04",
            "robot": "leg", "params": {}, "code_sha256": "0" * 64, "code_modified": True,
            "runtime": {"mode": "isaaclab3", "label": "Isaac Lab 3"}, "trainable": False, "resumable": False,
            "state": "failed", "phase": None, "progress": None, "result": None,
            "error": "exit code 1; 見 run.log", "pid": None,
            "created_at": "2026-10-31T00:00:00Z", "started_at": "2026-10-31T00:00:00Z",
            "finished_at": "2026-10-31T00:00:01Z", "resumed": 0,
        }
        self.t.put("runs", run, k1=self.uid, k2=lesson["id"])
        events = list(self.agent.chat(
            self.uid, lesson["id"], "請修復執行錯誤",
            {"robot": "leg", "params": {}, "code": broken, "last_run": {
                "id": run_id, "state": "failed", "error": "exit code 1; 見 run.log", "log_tail": err,
            }},
        ))
        apply = next(e for e in events if e.get("type") == "code_apply")
        self.assertIn("NewtonCfg", apply.get("code") or "")
        self.assertIn("UrdfFileCfg", apply.get("code") or "")
        tokens = "".join(e.get("text", "") for e in events if e.get("type") == "token")
        self.assertIn("example.py API", tokens)
        self.assertNotIn("請貼出關鍵行", tokens)

    def test_any_coding_edit_applies_not_only_log(self):
        # 「修改」must be EDIT, not REPAIR (bare 修 used to steal these).
        self.assertTrue(EDIT_HINT.search("請修改這堂課的 coding"))
        self.assertFalse(REPAIR_HINT.search("請修改這堂課的 coding"))
        self.assertTrue(REPAIR_HINT.search("請修復執行錯誤"))
        lesson = self.t.lesson("l01-isaaclab3-overview", robot="leg", user_id=self.uid)
        code = (ROOT / "tutorials/lessons/l01-isaaclab3-overview/example.py").read_text()
        edited, note = apply_simple_code_intent(code, "請修改課程 coding，把 sim_seconds 改成 5", lesson["id"])
        self.assertIn("5", note + edited)
        self.assertNotEqual(edited, code)
        events = list(self.agent.chat(
            self.uid, lesson["id"], "請修改這堂課的 coding，把 sim_seconds 改成 5",
            {"robot": "leg", "params": {}, "code": code},
        ))
        apply = next(e for e in events if e.get("type") == "code_apply")
        self.assertEqual(apply.get("author"), "Hermes")
        self.assertRegex(apply["code"], r'P\.get\([\'"]sim_seconds[\'"],\s*5')
        # Fenced full-file paste also applies.
        fenced = "```python\n" + edited + "\n```"
        self.assertTrue(extract_fenced_python(fenced))
        events2 = list(self.agent.chat(
            self.uid, lesson["id"], "請改 example.py：\n" + fenced,
            {"robot": "leg", "params": {}, "code": code},
        ))
        apply2 = next(e for e in events2 if e.get("type") == "code_apply")
        self.assertRegex(apply2["code"], r'P\.get\([\'"]sim_seconds[\'"],\s*5')


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
