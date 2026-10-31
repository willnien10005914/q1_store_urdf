"""Hermes tutor agent: streams reasoning + answer, calls tools (web/arXiv/YouTube search, fetch,
read/propose example code, set params, start a run, diagnose/repair runtime, propose a course,
remember facts) and keeps per-user, per-lesson conversation memory in the tutorial store.

The agent speaks Hermes' native function-calling format (``<tool_call>{json}</tool_call>`` with
``<tool_response>`` replies) and asks the model to think inside ``<think>`` tags so the browser can
show a live reasoning window. When no local LLM is running an offline tutor answers from the lesson
text and still exposes the tools through slash commands; it labels itself as offline.
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
import time
from pathlib import Path

from . import research
from .service import APIError

MAX_TOOL_ROUNDS = 6
HOLDBACK = 16  # characters kept back while streaming so tags are never split across events
TOOLS = [
    {"name": "web_search", "description": "Search the web (DuckDuckGo). Use for Isaac Lab / Isaac Sim docs, GitHub issues and release notes.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "arxiv_search", "description": "Search arXiv papers.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "youtube_search", "description": "Search YouTube videos.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "fetch_url", "description": "Fetch a web page as text.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "read_example", "description": "Read the current example.py the learner is editing (returns the full source).", "parameters": {"type": "object", "properties": {}}},
    {"name": "read_run_log", "description": "Read the latest (or given) tutorial run error + run.log tail for this lesson. Call this first when the learner says the run failed or asks to fix an error.",
     "parameters": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": []}},
    {"name": "diagnose_runtime", "description": "Diagnose Isaac Lab 3 / torch / NVIDIA driver / Hermes GPU / Q1_ISAACLAB3_ROOT. Use when CUDA, driver, uv, or Lab checkout errors appear.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "repair_isaaclab_cuda", "description": "Repair Isaac Lab 3 torch wheel to match the local NVIDIA driver (e.g. cu130→cu128 on CUDA 12.8 drivers). Safe to call when diagnose_runtime reports a mismatch.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "propose_code", "description": "Propose a full replacement example.py. The learner sees a diff and must accept it; explain why.",
     "parameters": {"type": "object", "properties": {"code": {"type": "string"}, "explanation": {"type": "string"}}, "required": ["code", "explanation"]}},
    {"name": "set_params", "description": "Set lesson parameters (sliders) for the learner, e.g. {\"num_envs\": 2048}.",
     "parameters": {"type": "object", "properties": {"params": {"type": "object"}}, "required": ["params"]}},
    {"name": "start_run", "description": "Run the example (or schedule PPO training for trainable lessons) with the current code and params.",
     "parameters": {"type": "object", "properties": {"params": {"type": "object"}}}},
    {"name": "propose_course", "description": "File a new course proposal (markdown) for human review when you find new Isaac Lab 3 material.",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "category": {"type": "string"},
                                                     "sources": {"type": "array", "items": {"type": "object"}}, "body": {"type": "string"}},
                    "required": ["title", "summary", "sources"]}},
    {"name": "remember", "description": "Store durable facts about the learner (skill level, preferences, goals).",
     "parameters": {"type": "object", "properties": {"facts": {"type": "array", "items": {"type": "string"}}}, "required": ["facts"]}},
]
REPAIR_HINT = re.compile(
    r"修|錯誤|失败|失敗|error|traceback|runtimeerror|cuda|driver|blocked|fix|repair|fail",
    re.I,
)
TAG_RE = re.compile(r"<think>|</think>|<tool_call>|</tool_call>")


def tokenize(text):
    text = (text or "").lower()
    words = set(re.findall(r"[a-z0-9_\.]{2,}", text))
    cjk = re.findall(r"[\u3400-\u9fff]", text)
    words.update("".join(pair) for pair in zip(cjk, cjk[1:]))
    return words


class TagStreamer:
    """Splits a streamed Hermes reply into reasoning / answer / tool-call events."""

    def __init__(self):
        self.mode = "answer"
        self.buffer = ""
        self.tool_buffer = ""
        self.answer = ""
        self.reasoning = ""
        self.tool_calls = []

    def feed(self, delta, final=False):
        self.buffer += delta
        events = []
        while True:
            match = TAG_RE.search(self.buffer)
            if match:
                before, tag = self.buffer[:match.start()], match.group(0)
                self.buffer = self.buffer[match.end():]
                events += self._emit(before)
                if tag == "<think>":
                    self.mode = "think"
                elif tag == "</think>":
                    self.mode = "answer"
                elif tag == "<tool_call>":
                    self.mode = "tool"
                    self.tool_buffer = ""
                elif tag == "</tool_call>":
                    self.mode = "answer"
                    events.append(self._parse_tool())
                continue
            keep = 0 if final else HOLDBACK
            if len(self.buffer) > keep:
                chunk, self.buffer = self.buffer[:len(self.buffer) - keep], self.buffer[len(self.buffer) - keep:]
                events += self._emit(chunk)
            break
        return [e for e in events if e]

    def _emit(self, text):
        if not text:
            return []
        if self.mode == "think":
            self.reasoning += text
            return [{"type": "reasoning", "text": text}]
        if self.mode == "tool":
            self.tool_buffer += text
            return []
        self.answer += text
        return [{"type": "token", "text": text}]

    def _parse_tool(self):
        raw = self.tool_buffer.strip()
        try:
            call = json.loads(raw)
        except json.JSONDecodeError:
            fixed = re.search(r"\{.*\}", raw, re.S)
            try:
                call = json.loads(fixed.group(0)) if fixed else None
            except json.JSONDecodeError:
                call = None
        if not isinstance(call, dict) or "name" not in call:
            return {"type": "reasoning", "text": f"\n[無法解析的 tool_call：{raw[:200]}]\n"}
        call.setdefault("arguments", {})
        self.tool_calls.append(call)
        return {"type": "tool_call", "name": call["name"], "arguments": call["arguments"]}


class Agent:
    def __init__(self, tutorial, llm, searcher=None, clock=time.time):
        self.t = tutorial
        self.llm = llm
        self.searcher = searcher or research
        self.clock = clock

    # --------------------------------------------------------------- prompts
    def system_prompt(self, uid, lesson, context):
        memory = self.t.memory_get(uid)["facts"]
        progress = self.t.progress(uid)
        params = context.get("params") or {}
        last_run = context.get("last_run") or {}
        last_run_blob = ""
        if last_run:
            last_run_blob = (
                f"Latest run for this lesson: id={last_run.get('id')} state={last_run.get('state')} "
                f"error={last_run.get('error')!r}\nlog_tail:\n{(last_run.get('log_tail') or '')[:3500]}\n"
            )
        return (
            "You are Hermes, the Q1 Store tutor for Isaac Lab 3.0 and Isaac Sim. You teach by explaining the APIs in the current lesson, "
            "pointing at exact lines of example.py, proposing concrete edits and parameter changes, diagnosing failed runs, and repairing "
            "the local Isaac Lab / CUDA environment when needed. "
            "Answer in the learner's language (Traditional Chinese by default; English if they write English). Be precise about 3.0 facts: "
            "ProxyArray (.torch/.warp), XYZW quaternions, write_*_to_sim_index/_mask, robot.actuators.target_command.set_*_index, "
            "actuator_effort_limit vs joint_effort_limit, launch_simulation, physics=newton_mjwarp|isaacsim_physx|ovphysx, HEADLESS=1 (no --headless), "
            "VisualizerCfg/VideoRecorderCfg, --checkpoint latest|best. If unsure, search instead of guessing, and cite URLs.\n\n"
            "When the learner reports a failed run, asks to 修復/fix an error, or pastes a Traceback: "
            "1) call read_run_log (or use the Latest run block below), 2) if the error mentions CUDA/driver/torch call diagnose_runtime, "
            "3) if diagnose reports torch/driver mismatch call repair_isaaclab_cuda, 4) for example.py bugs use propose_code, "
            "5) then start_run to verify. Do NOT ask which lesson — you already know it from context. Never reply that you cannot understand a fix request.\n\n"
            "Always start your reply with your reasoning inside <think></think>, then the answer. "
            "You have tools. To call one, emit exactly: <tool_call>{\"name\": \"tool_name\", \"arguments\": {...}}</tool_call> and stop; the result arrives in a "
            "<tool_response> message, after which you continue. Never fabricate tool results. Use propose_code for any code change (full file), "
            "set_params for slider changes, start_run to execute, propose_course when you discover new Isaac Lab 3 material worth a lesson.\n"
            f"<tools>{json.dumps(TOOLS, ensure_ascii=False)}</tools>\n\n"
            f"Learner: {progress.get('name')} · level {progress['level']['id']} · {progress['completed']}/{progress['total']} lessons done.\n"
            f"Memory about the learner: {json.dumps(memory, ensure_ascii=False) if memory else 'none yet'}\n"
            f"Lesson: {lesson['id']} — {lesson.get('title')} (category {lesson.get('category')}, robot {context.get('robot', 'leg')}).\n"
            f"Lesson APIs: {', '.join(lesson.get('apis', []))}\nCurrent params: {json.dumps(params, ensure_ascii=False)}\n"
            f"{last_run_blob}"
            f"Lesson text (markdown):\n{lesson.get('markdown', '')[:5000]}\n"
        )

    def messages(self, uid, lesson, context, user_message):
        history = self.t.history(uid, lesson["id"], limit=24)
        msgs = [{"role": "system", "content": self.system_prompt(uid, lesson, context)}]
        for m in history:
            if m["role"] in ("user", "assistant") and m.get("content"):
                msgs.append({"role": m["role"], "content": m["content"]})
        msgs.append({"role": "user", "content": user_message})
        return msgs

    # ------------------------------------------------------------------ chat
    def chat(self, uid, lesson_id, message, context=None):
        context = context or {}
        message = (message or "").strip()
        if not message:
            raise APIError("訊息不可為空。")
        if len(message) > 8000:
            raise APIError("訊息太長。")
        robot = context.get("robot") or "leg"
        lesson = self.t.lesson(lesson_id, robot=robot, user_id=uid)
        # Always attach the newest run for this lesson so Hermes can repair without asking.
        if not context.get("last_run"):
            try:
                recent = self.t.runs(uid=uid, lesson_id=lesson_id, limit=1)
                if recent:
                    full = self.t.run(recent[0]["id"], log_lines=60)
                    tail = full.get("log_tail") or []
                    context["last_run"] = {
                        "id": full["id"],
                        "state": full.get("state"),
                        "error": full.get("error"),
                        "log_tail": "\n".join(tail) if isinstance(tail, list) else str(tail),
                        "runtime": (full.get("runtime") or {}).get("label"),
                    }
            except Exception:
                pass
        self.t.append_message(uid, lesson_id, "user", message)
        self.t.touch_last(uid, lesson_id, robot)
        if self.llm is not None and self.llm.chat_ready():
            yield from self._hermes(uid, lesson, context, message)
        else:
            yield from OfflineTutor(self).respond(uid, lesson, context, message)

    def _hermes(self, uid, lesson, context, message):
        msgs = self.messages(uid, lesson, context, message)
        yield {"type": "status", "text": "Hermes 思考中…", "mode": "hermes"}
        final_answer, all_reasoning = "", ""
        for round_index in range(MAX_TOOL_ROUNDS + 1):
            streamer = TagStreamer()
            try:
                for delta in self.llm.chat_stream(msgs):
                    for event in streamer.feed(delta):
                        yield event
                        if event["type"] == "tool_call":
                            break
                    if streamer.tool_calls and streamer.mode == "answer":
                        break
                for event in streamer.feed("", final=True):
                    yield event
            except Exception as exc:  # runtime died mid-stream: fall back honestly
                yield {"type": "error", "text": f"LLM 串流中斷：{type(exc).__name__}: {exc}"}
                yield from OfflineTutor(self).respond(uid, lesson, context, message, note="LLM 中斷，改用離線模式")
                return
            final_answer += streamer.answer
            all_reasoning += streamer.reasoning
            if not streamer.tool_calls or round_index == MAX_TOOL_ROUNDS:
                break
            call = streamer.tool_calls[0]
            assistant_text = (f"<think>{streamer.reasoning}</think>" if streamer.reasoning else "") + streamer.answer + \
                f"<tool_call>{json.dumps(call, ensure_ascii=False)}</tool_call>"
            msgs.append({"role": "assistant", "content": assistant_text})
            result, events = self.run_tool(uid, lesson, context, call["name"], call.get("arguments") or {})
            for event in events:
                yield event
            yield {"type": "tool_result", "name": call["name"], "result": self.summarize(result)}
            msgs.append({"role": "tool", "content": f"<tool_response>{json.dumps(result, ensure_ascii=False)[:12000]}</tool_response>"})
        saved = self.t.append_message(uid, lesson["id"], "assistant", final_answer.strip(), reasoning=all_reasoning.strip()[:6000], mode="hermes")
        yield {"type": "done", "message_id": saved["id"], "mode": "hermes"}

    @staticmethod
    def summarize(result):
        text = json.dumps(result, ensure_ascii=False)
        return text if len(text) <= 1500 else text[:1500] + "…"

    # ----------------------------------------------------------------- tools
    def run_tool(self, uid, lesson, context, name, arguments):
        """Returns (result for the model, [events for the browser])."""
        events = []
        try:
            if name == "web_search":
                return self.searcher.web_search(str(arguments.get("query", ""))[:200]), events
            if name == "arxiv_search":
                return self.searcher.arxiv_search(str(arguments.get("query", ""))[:200]), events
            if name == "youtube_search":
                return self.searcher.youtube_search(str(arguments.get("query", ""))[:200]), events
            if name == "fetch_url":
                return self.searcher.fetch_url(str(arguments.get("url", ""))[:500]), events
            if name == "read_example":
                code = context.get("code") or lesson.get("draft_code") or lesson.get("code", "")
                return {"path": f"tutorials/lessons/{lesson['id']}/example.py", "code": code}, events
            if name == "read_run_log":
                return self._read_run_log(uid, lesson["id"], arguments.get("run_id") or (context.get("last_run") or {}).get("id")), events
            if name == "diagnose_runtime":
                return self._diagnose_runtime(context), events
            if name == "repair_isaaclab_cuda":
                return self._repair_isaaclab_cuda(), events
            if name == "propose_code":
                code = str(arguments.get("code", ""))
                if not code.strip():
                    return {"error": "empty code"}, events
                current = context.get("code") or lesson.get("draft_code") or lesson.get("code", "")
                diff = "".join(difflib.unified_diff(current.splitlines(True), code.splitlines(True), "example.py (current)", "example.py (proposed)"))
                proposal = self.t.append_message(uid, lesson["id"], "proposal", code, explanation=str(arguments.get("explanation", ""))[:2000], diff=diff[:20000])
                events.append({"type": "code_proposal", "proposal_id": proposal["id"], "code": code, "diff": diff[:20000],
                               "explanation": arguments.get("explanation", "")})
                return {"ok": True, "proposal_id": proposal["id"], "diff_lines": diff.count("\n"), "note": "learner must accept the diff in the editor"}, events
            if name == "set_params":
                params = arguments.get("params") or {}
                if not isinstance(params, dict):
                    return {"error": "params must be an object"}, events
                meta = self.t.lesson_meta(lesson["id"])
                merged = {**(context.get("params") or {}), **params}
                clean = self.t.validate_params(meta, merged)
                context["params"] = clean
                self.t.save_draft(uid, lesson["id"], params=clean)
                events.append({"type": "params", "params": clean})
                return {"ok": True, "params": clean}, events
            if name == "start_run":
                params = {**(context.get("params") or {}), **(arguments.get("params") or {})}
                run = self.t.create_run(uid, lesson["id"], context.get("robot") or "leg", params, context.get("code"))
                events.append({"type": "run", "run": {k: run[k] for k in ("id", "state", "lesson_id", "robot", "trainable", "runtime", "error")}})
                return {"ok": True, "run_id": run["id"], "state": run["state"], "runtime": run["runtime"]["label"], "error": run.get("error")}, events
            if name == "propose_course":
                sources = arguments.get("sources") or []
                item = self.t.write_proposal(str(arguments.get("title", "Untitled"))[:120], str(arguments.get("summary", ""))[:500],
                                             str(arguments.get("category", "other")), sources,
                                             str(arguments.get("body") or arguments.get("summary") or ""), origin="hermes-chat")
                events.append({"type": "proposal", "proposal": {"id": item["id"], "title": item["meta"].get("title")}})
                return {"ok": True, "proposal_id": item["id"], "path": item["path"]}, events
            if name == "remember":
                facts = arguments.get("facts") or []
                mem = self.t.memory_add(uid, facts if isinstance(facts, list) else [facts])
                return {"ok": True, "facts": mem["facts"][-10:]}, events
            return {"error": f"unknown tool {name}"}, events
        except APIError as exc:
            return {"error": str(exc)}, events

    def _read_run_log(self, uid, lesson_id, run_id=None):
        run = None
        if run_id:
            try:
                run = self.t.run(run_id, log_lines=80)
            except APIError:
                run = None
        if run is None:
            runs = self.t.runs(uid=uid, lesson_id=lesson_id, limit=1)
            if not runs:
                return {"ok": False, "error": "此課程尚無執行紀錄"}
            run = self.t.run(runs[0]["id"], log_lines=80)
        log_tail = run.get("log_tail") or []
        if isinstance(log_tail, list):
            log_text = "\n".join(log_tail)
        else:
            log_text = str(log_tail)
        return {
            "ok": True,
            "run_id": run["id"],
            "state": run.get("state"),
            "error": run.get("error"),
            "runtime": (run.get("runtime") or {}).get("label"),
            "log_tail": log_text[-6000:],
        }

    def _diagnose_runtime(self, context=None):
        root = self.t.isaaclab3_root
        info = {
            "ok": True,
            "Q1_ISAACLAB3_ROOT": str(root) if root else None,
            "uv": bool(self.t.uv),
            "pyproject": bool(root and (root / "pyproject.toml").is_file()),
            "driver_cuda": None,
            "gpu": None,
            "torch": None,
            "issues": [],
            "hints": [],
        }
        try:
            smi = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=8)
            match = re.search(r"CUDA Version:\s*([0-9.]+)", smi.stdout or "")
            info["driver_cuda"] = match.group(1) if match else None
            mem = re.search(r"(\d+)MiB\s*/\s*(\d+)MiB", smi.stdout or "")
            name_m = re.search(r"NVIDIA GeForce [^\n|]+|NVIDIA RTX [^\n|]+|Tesla [^\n|]+", smi.stdout or "")
            if mem:
                info["gpu"] = {
                    "name": (name_m.group(0).strip() if name_m else "GPU"),
                    "memory_used_mib": int(mem.group(1)),
                    "memory_total_mib": int(mem.group(2)),
                }
                if info["gpu"]["memory_used_mib"] > info["gpu"]["memory_total_mib"] * 0.55:
                    info["hints"].append(
                        "Hermes/其他程序已佔用過半 VRAM；跑 Isaac Lab 前可先停止 Hermes（左側「停止伺服器」）以騰出記憶體。"
                    )
        except Exception as exc:
            info["issues"].append(f"nvidia-smi failed: {exc}")

        if not root:
            info["issues"].append("Q1_ISAACLAB3_ROOT unset — run bash scripts/setup_store.sh then restart store")
        elif not (root / "pyproject.toml").is_file():
            info["issues"].append(f"{root} missing pyproject.toml")
        else:
            py = root / ".venv" / "bin" / "python"
            if not py.is_file():
                info["issues"].append("Isaac Lab .venv missing — run setup_store.sh / uv sync")
            else:
                try:
                    proc = subprocess.run(
                        [str(py), "-c",
                         "import torch; print(torch.__version__); print(torch.version.cuda); "
                         "print(int(torch.cuda.is_available()))"],
                        capture_output=True, text=True, timeout=60)
                    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
                    info["torch"] = {
                        "version": lines[0] if lines else None,
                        "cuda": lines[1] if len(lines) > 1 else None,
                        "available": (lines[2] == "1") if len(lines) > 2 else False,
                        "stderr": (proc.stderr or "")[-800:],
                    }
                    if not info["torch"]["available"]:
                        info["issues"].append("torch.cuda.is_available() is False — usually torch wheel CUDA > driver (Lab 3 default cu130 needs driver CUDA 13; CUDA 12.8 drivers need cu128).")
                        info["hints"].append("Call repair_isaaclab_cuda or: bash scripts/repair_isaaclab_cuda.sh")
                    ver = info["torch"]["version"] or ""
                    drv = info["driver_cuda"] or ""
                    if "+cu130" in ver and drv.startswith("12."):
                        info["issues"].append(f"torch {ver} requires CUDA 13-capable driver; host reports {drv}")
                        info["hints"].append("repair_isaaclab_cuda will pin torch to cu128")
                except Exception as exc:
                    info["issues"].append(f"torch probe failed: {exc}")

        if self.llm is not None:
            try:
                status = self.llm.status()
                info["hermes"] = {"chat_ready": status.get("chat_ready"), "gpu": status.get("gpu")}
            except Exception:
                info["hermes"] = {"chat_ready": False}

        info["ok"] = not info["issues"]
        return info

    def _repair_isaaclab_cuda(self):
        script = Path(self.t.root) / "scripts" / "repair_isaaclab_cuda.sh"
        if not script.is_file():
            return {"ok": False, "error": f"missing {script}"}
        root = self.t.isaaclab3_root
        cmd = ["bash", str(script)]
        if root:
            cmd.append(str(root))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "repair timed out (>15 min)"}
        out = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-4000:]
        ok = proc.returncode == 0
        result = {"ok": ok, "exit_code": proc.returncode, "log": out}
        if ok:
            result["diagnose"] = self._diagnose_runtime()
            result["note"] = "Torch repaired. Ask the learner to re-run the lesson (start_run)."
        return result

    # ---------------------------------------------------------- code accept
    def accept_proposal(self, uid, lesson_id, proposal_id):
        for m in self.t.select("messages", k1=uid, k2=lesson_id):
            if m["id"] == proposal_id and m["role"] == "proposal":
                self.t.save_draft(uid, lesson_id, code=m["content"])
                self.t.append_message(uid, lesson_id, "system", f"採用了 Hermes 的程式碼提案 {proposal_id[:8]}。")
                return {"ok": True, "code": m["content"]}
        raise APIError("找不到這個提案。", 404)


class OfflineTutor:
    """Deterministic fallback when no LLM is running: answers from lesson.md, supports slash commands."""

    COMMANDS = ("/search", "/arxiv", "/youtube", "/fetch", "/run", "/params", "/code", "/remember",
                "/fix", "/diagnose", "/log", "/help")

    def __init__(self, agent):
        self.agent = agent
        self.t = agent.t

    def sections(self, markdown):
        parts, current, title = [], [], "簡介"
        for line in markdown.splitlines():
            if line.startswith("## "):
                if current:
                    parts.append((title, "\n".join(current).strip()))
                title, current = line[3:].strip(), []
            else:
                current.append(line)
        if current:
            parts.append((title, "\n".join(current).strip()))
        return parts

    def respond(self, uid, lesson, context, message, note=None):
        yield {"type": "status", "text": note or "離線模式：未偵測到執行中的本機 LLM，由規則式助教回答（可在上方「本機 LLM」面板下載並啟動 Hermes）。", "mode": "offline"}
        answer_parts, reasoning = [], []
        lowered = message.strip()
        cmd, _, rest = lowered.partition(" ")
        wants_fix = bool(REPAIR_HINT.search(message)) or bool((context.get("last_run") or {}).get("error"))
        if cmd in ("/search", "/arxiv", "/youtube", "/fetch"):
            tool = {"/search": "web_search", "/arxiv": "arxiv_search", "/youtube": "youtube_search", "/fetch": "fetch_url"}[cmd]
            args = {"url": rest.strip()} if tool == "fetch_url" else {"query": rest.strip()}
            reasoning.append(f"執行工具 {tool}({json.dumps(args, ensure_ascii=False)})")
            yield {"type": "reasoning", "text": reasoning[-1] + "\n"}
            yield {"type": "tool_call", "name": tool, "arguments": args}
            result, events = self.agent.run_tool(uid, lesson, context, tool, args)
            for e in events:
                yield e
            yield {"type": "tool_result", "name": tool, "result": self.agent.summarize(result)}
            if result.get("error"):
                answer_parts.append(f"工具錯誤：{result['error']}")
            elif tool == "fetch_url":
                answer_parts.append(f"**{result.get('title') or result['url']}**\n\n{result.get('text', '')[:3000]}")
            else:
                lines = [f"- [{r['title']}]({r['url']})" + (f" — {r['snippet'][:160]}" if r.get("snippet") else "") for r in result.get("results", [])]
                answer_parts.append("找到：\n" + ("\n".join(lines) if lines else "（沒有結果）"))
                if lines and cmd != "/fetch":
                    answer_parts.append("若值得做成課程，輸入 `/propose <標題>` 可建立待審提案。")
        elif cmd == "/propose":
            title = rest.strip() or "Hermes 提案"
            result, events = self.agent.run_tool(uid, lesson, context, "propose_course",
                                                 {"title": title, "summary": f"由離線助教在 {lesson['id']} 對話中建立。", "category": lesson.get("category", "other"),
                                                  "sources": [{"type": "docs", "title": r["title"], "url": r["url"]} for r in lesson.get("references", [])[:3]],
                                                  "body": f"# {title}\n\n待補充：請描述新功能、對應 API 與練習。"})
            for e in events:
                yield e
            answer_parts.append(f"已建立待審提案 `{result.get('proposal_id')}`，可在 tutorial 頁面的「待審課程」檢視。" if result.get("ok") else f"提案失敗：{result.get('error')}")
        elif cmd == "/params":
            kv = dict(re.findall(r"([a-zA-Z_][\w]*)\s*=\s*([^\s,]+)", rest))
            result, events = self.agent.run_tool(uid, lesson, context, "set_params", {"params": kv})
            for e in events:
                yield e
            answer_parts.append(f"參數已更新：`{json.dumps(result.get('params', {}), ensure_ascii=False)}`" if result.get("ok") else f"參數錯誤：{result.get('error')}")
        elif cmd == "/run":
            yield {"type": "tool_call", "name": "start_run", "arguments": {}}
            result, events = self.agent.run_tool(uid, lesson, context, "start_run", {})
            for e in events:
                yield e
            yield {"type": "tool_result", "name": "start_run", "result": self.agent.summarize(result)}
            if result.get("ok"):
                answer_parts.append(f"已送出執行 `{result['run_id'][:8]}`（{result['runtime']}），狀態：{result['state']}。" + (f"\n\n{result['error']}" if result.get("error") else ""))
            else:
                answer_parts.append(f"無法執行：{result.get('error')}")
        elif cmd == "/code":
            code = context.get("code") or lesson.get("draft_code") or lesson.get("code", "")
            hits = [f"L{i + 1}: {line.strip()}" for i, line in enumerate(code.splitlines()) if rest.strip() and rest.strip().lower() in line.lower()]
            answer_parts.append(("在 example.py 找到：\n```\n" + "\n".join(hits[:30]) + "\n```") if hits else "example.py 裡沒有這個字串。")
        elif cmd == "/remember":
            self.t.memory_add(uid, [rest.strip()])
            answer_parts.append("已記住。")
        elif cmd in ("/log", "/diagnose", "/fix") or (wants_fix and cmd not in self.COMMANDS):
            yield from self._repair_flow(uid, lesson, context, answer_parts, reasoning, do_repair=(cmd == "/fix" or "修" in message or "fix" in message.lower() or "repair" in message.lower()))
        elif cmd == "/help" or not lesson.get("markdown"):
            answer_parts.append(self.help_text(lesson))
        else:
            words = tokenize(message)
            scored = []
            for title, body in self.sections(lesson.get("markdown", "")):
                overlap = len(words & tokenize(title + " " + body))
                scored.append((overlap, title, body))
            scored.sort(key=lambda s: -s[0])
            reasoning.append(f"關鍵字比對課文章節：{[(s[1], s[0]) for s in scored[:3]]}")
            yield {"type": "reasoning", "text": reasoning[-1] + "\n"}
            best = scored[0] if scored and scored[0][0] > 0 else None
            if best:
                answer_parts.append(f"### {best[1]}\n\n{best[2][:2500]}")
            else:
                answer_parts.append("我在課文裡找不到直接相關的段落。")
            api_hits = [api for api in lesson.get("apis", []) if any(w in api.lower() for w in words)]
            if api_hits:
                answer_parts.append("相關 API：" + "、".join(f"`{a}`" for a in api_hits))
            answer_parts.append(self.help_text(lesson, short=True))
        text = "\n\n".join(answer_parts)
        for chunk in re.findall(r".{1,120}(?:\s|$)", text, re.S) or [text]:
            yield {"type": "token", "text": chunk}
        saved = self.t.append_message(uid, lesson["id"], "assistant", text, reasoning="\n".join(reasoning), mode="offline")
        yield {"type": "done", "message_id": saved["id"], "mode": "offline"}

    def _repair_flow(self, uid, lesson, context, answer_parts, reasoning, do_repair=False):
        reasoning.append("讀取最近執行紀錄並診斷 runtime")
        yield {"type": "reasoning", "text": reasoning[-1] + "\n"}
        yield {"type": "tool_call", "name": "read_run_log", "arguments": {}}
        log_info, _ = self.agent.run_tool(uid, lesson, context, "read_run_log", {})
        yield {"type": "tool_result", "name": "read_run_log", "result": self.agent.summarize(log_info)}
        if log_info.get("ok"):
            answer_parts.append(
                f"**最近執行** `{log_info['run_id'][:8]}` · {log_info.get('state')} · {log_info.get('runtime')}\n\n"
                f"錯誤：{log_info.get('error') or '（無）'}\n\n```\n{(log_info.get('log_tail') or '')[-1800:]}\n```"
            )
        else:
            answer_parts.append(log_info.get("error") or "沒有執行紀錄。")

        yield {"type": "tool_call", "name": "diagnose_runtime", "arguments": {}}
        diag, _ = self.agent.run_tool(uid, lesson, context, "diagnose_runtime", {})
        yield {"type": "tool_result", "name": "diagnose_runtime", "result": self.agent.summarize(diag)}
        issues = diag.get("issues") or []
        hints = diag.get("hints") or []
        answer_parts.append(
            "**環境診斷**\n"
            f"- Lab root：`{diag.get('Q1_ISAACLAB3_ROOT')}`\n"
            f"- driver CUDA：`{diag.get('driver_cuda')}`\n"
            f"- torch：`{(diag.get('torch') or {}).get('version')}` "
            f"(cuda={(diag.get('torch') or {}).get('cuda')}, available={(diag.get('torch') or {}).get('available')})\n"
            + (("\n".join(f"- ⚠ {i}" for i in issues) + "\n") if issues else "- ✓ 未發現已知環境問題\n")
            + (("\n".join(f"- 提示：{h}" for h in hints)) if hints else "")
        )

        err_blob = f"{log_info.get('error') or ''} {log_info.get('log_tail') or ''}".lower()
        needs_cuda = any(k in err_blob for k in ("cuda", "driver", "12080", "cu130")) or any("torch" in i.lower() or "cuda" in i.lower() for i in issues)
        if do_repair and needs_cuda:
            reasoning.append("偵測到 CUDA/driver 不符，執行 repair_isaaclab_cuda")
            yield {"type": "reasoning", "text": reasoning[-1] + "\n"}
            yield {"type": "tool_call", "name": "repair_isaaclab_cuda", "arguments": {}}
            repaired, _ = self.agent.run_tool(uid, lesson, context, "repair_isaaclab_cuda", {})
            yield {"type": "tool_result", "name": "repair_isaaclab_cuda", "result": self.agent.summarize(repaired)}
            if repaired.get("ok"):
                answer_parts.append("已將 Isaac Lab 的 torch 對齊本機 driver。請再按「執行」驗證。")
            else:
                answer_parts.append(f"自動修復失敗：{repaired.get('error') or repaired.get('log')}")
        elif needs_cuda:
            answer_parts.append("這像是 **torch CUDA 與 NVIDIA driver 不符**。輸入 `/fix` 讓我執行 `repair_isaaclab_cuda`，或手動：`bash scripts/repair_isaaclab_cuda.sh`。")
        else:
            answer_parts.append("若錯誤在 example.py，請貼出關鍵行或輸入 `/code <關鍵字>`；修好後用 `/run` 再跑一次。")

    @staticmethod
    def help_text(lesson, short=False):
        params = ", ".join(f"`{p['key']}`" for p in lesson.get("params", [])[:8])
        lines = ["可用指令：`/search`、`/arxiv`、`/youtube`、`/fetch`、`/params`、`/run`、`/code`、`/log`、`/diagnose`、`/fix`、`/propose`、`/remember`、`/help`。"]
        if not short:
            lines.append(f"這堂課的參數：{params or '（無）'}。執行失敗時直接說「請修復」或打 `/fix`。")
        return "\n".join(lines)
