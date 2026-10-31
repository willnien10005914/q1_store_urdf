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
# Hold back enough chars for the longest escaped close tag: &amp;lt;/think&amp;gt; (23).
HOLDBACK = 32
TOOLS = [
    {"name": "web_search", "description": "Search the web (DuckDuckGo). Use for Isaac Lab / Isaac Sim docs, GitHub issues and release notes.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "arxiv_search", "description": "Search arXiv papers.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "youtube_search", "description": "Search YouTube videos.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "fetch_url", "description": "Fetch a web page as text.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "read_example", "description": "Read the current example.py the learner is editing (returns the full source).", "parameters": {"type": "object", "properties": {}}},
    {"name": "read_run_log", "description": "Read the latest (or given) tutorial run error + run.log tail for this lesson. Call this first when the learner says the run failed or asks to fix an error.",
     "parameters": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": []}},
    {"name": "analyze_run", "description": "Analyze a completed (or latest) tutorial run: result.json, evaluation.json, progress, media files (result.mp4 / rollout.json), and give a tutor-facing summary. Use when the learner asks about training results, metrics, video, rollout, or inference playback.",
     "parameters": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": []}},
    {"name": "diagnose_runtime", "description": "Diagnose Isaac Lab 3 / torch / NVIDIA driver / Hermes GPU / Q1_ISAACLAB3_ROOT. Use when CUDA, driver, uv, or Lab checkout errors appear.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "repair_isaaclab_cuda", "description": "Repair Isaac Lab 3 torch wheel to match the local NVIDIA driver (e.g. cu130→cu128 on CUDA 12.8 drivers). Safe to call when diagnose_runtime reports a mismatch.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "repair_isaaclab_importers", "description": "Install Isaac Lab 3 standalone URDF/MJCF importers (uv sync --extra importers / isaacsim-asset-isolated). Use when run.log shows ModuleNotFoundError: No module named 'isaacsim'.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "repair_physx_joint", "description": "Patch kitless URDF importer so PhysxJointAPI ApplyAPI failures are skipped (newton_mjwarp without full Isaac Sim). Use when run.log shows ApplyAPI / PhysxJointAPI / PhysxSchema errors.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "propose_code", "description": "Propose a full replacement example.py. By default the learner sees a diff and must accept it. Set apply=true to push the edit into the Lab code editor immediately (with Hermes attribution marks). Always call this (or apply_code) before claiming you changed example.py.",
     "parameters": {"type": "object", "properties": {"code": {"type": "string"}, "explanation": {"type": "string"}, "apply": {"type": "boolean"}}, "required": ["code", "explanation"]}},
    {"name": "apply_code", "description": "Write a full replacement example.py straight into the learner's Lab editor (yellow Hermes marks + # [Hermes] notes). Use when the learner asks to 加 log / 改程式 / edit example.py so the middle pane updates without waiting for a diff dialog.",
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
    # Do NOT match 修改／改 coding, and do NOT match incidental "cuda:0" inside pasted example.py.
    r"修復|修好|修一下|請修(?!改)|幫我修(?!改)|錯誤|失败|失敗|traceback|runtimeerror|"
    r"(cuda|driver).{0,16}(錯誤|error|fail|不符|mismatch|太舊|too old)|"
    r"\bfix\b|\brepair\b|physxjointapi|physxschema|add_joint_schemas|applyapi",
    re.I,
)
ANALYZE_HINT = re.compile(
    r"(分析\s*(結果|成果|執行|run)|看結果|解釋結果|/analyze|/result|"
    r"result\.json|evaluation\.json|評估結果|驗收|"
    r"(訓練後|跑完|完成後).{0,12}(結果|影片|rollout|推論)|"
    r"(播放|看).{0,8}(影片|video|mp4|rollout|推論)|"
    r"\b(analyze|metrics?|inference|rollout|result\.mp4)\b)",
    re.I,
)
EDIT_HINT = re.compile(
    r"("
    r"加\s*(上)?\s*(log|print|logging|日誌|除錯|debug)|"
    r"加上?.{0,8}(log|print|logging|日誌)|"
    r"(insert|add)\s+(more\s+)?(log|print|logging)|"
    r"(改|修改|編輯|更新|調整|重寫|改寫|刪除|替換|加上|插入).{0,28}"
    r"(example\.py|程式碼?|coding|code|範例|源碼|腳本|script)|"
    r"(幫我|請|麻煩).{0,12}(改|寫|加|刪|調|編輯|更新).{0,28}"
    r"(example\.py|程式碼?|coding|code|範例|這堂課|課程)|"
    r"(課程|這堂課|lesson).{0,12}(coding|code|程式|範例).{0,12}(改|修改|編輯|更新)|"
    r"改一下\s*(code|coding|程式|example)|"
    r"\b(edit|change|update|modify)\b.{0,24}\b(example\.py|code|coding|script)\b|"
    r"/edit|/logcode"
    r")",
    re.I,
)
LOG_EDIT_HINT = re.compile(
    r"(log|print|logging|日誌|除錯|debug|progress\.json|run\.log)",
    re.I,
)
FENCED_PY_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.I | re.S)
PHYSX_HINT = re.compile(r"physxjointapi|physxschema|add_joint_schemas|cannot find a valid schema", re.I)
# PhysX/Kit launch without full Isaac Sim — fix by rewriting example.py to newton_mjwarp, not by installing extras.
ISAAC_SIM_MISSING_HINT = re.compile(
    r"Isaac Sim is not installed|not found on PYTHONPATH|"
    r"PhysX backend and Kit visualizer currently requires Isaac Sim|"
    r"requires Isaac Sim and Omniverse Kit",
    re.I,
)
EXAMPLE_API_HINT = re.compile(
    r"unexpected keyword argument|TypeError:|AttributeError:|KeyError:|got an unexpected keyword|"
    r"NewtonCfg\.__init__|PhysxCfg\.__init__|has no attribute|"
    r"get_masses|body_mass|class_name|RslRlPpoActorCriticCfg|"
    r"File \"example\.py\"|"
    r"Isaac Sim is not installed|PhysX backend and Kit visualizer currently requires Isaac Sim",
    re.I,
)
TAG_RE = re.compile(
    r"<think>|</think>|<tool_call>|</tool_call>|"
    r"&lt;think&gt;|&lt;/think&gt;|&lt;tool_call&gt;|&lt;/tool_call&gt;|"
    r"&amp;lt;think&amp;gt;|&amp;lt;/think&amp;gt;|"
    r"&amp;lt;tool_call&amp;gt;|&amp;lt;/tool_call&amp;gt;",
    re.I,
)
LEAKED_TAG_RE = re.compile(
    r"</?(?:think|tool_call|tool_response)\s*>|"
    r"&lt;/?\s*(?:think|tool_call|tool_response)\s*&gt;|"
    r"&amp;lt;/?\s*(?:think|tool_call|tool_response)\s*&amp;gt;",
    re.I,
)


def tokenize(text):
    text = (text or "").lower()
    words = set(re.findall(r"[a-z0-9_\.]{2,}", text))
    cjk = re.findall(r"[\u3400-\u9fff]", text)
    words.update("".join(pair) for pair in zip(cjk, cjk[1:]))
    return words


def normalize_hermes_tags(text: str) -> str:
    """Decode HTML-escaped Hermes control tags to literal XML so TagStreamer can split them."""
    if not text:
        return text
    out = text
    for a, b in (
        ("&amp;lt;think&amp;gt;", "<think>"),
        ("&amp;lt;/think&amp;gt;", "</think>"),
        ("&amp;lt;tool_call&amp;gt;", "<tool_call>"),
        ("&amp;lt;/tool_call&amp;gt;", "</tool_call>"),
        ("&amp;lt;tool_response&amp;gt;", "<tool_response>"),
        ("&amp;lt;/tool_response&amp;gt;", "</tool_response>"),
        ("&lt;think&gt;", "<think>"),
        ("&lt;/think&gt;", "</think>"),
        ("&lt;tool_call&gt;", "<tool_call>"),
        ("&lt;/tool_call&gt;", "</tool_call>"),
        ("&lt;tool_response&gt;", "<tool_response>"),
        ("&lt;/tool_response&gt;", "</tool_response>"),
    ):
        out = out.replace(a, b)
    return out


def strip_leaked_hermes_tags(text: str) -> str:
    """Remove leftover think/tool markup from answer text."""
    if not text:
        return text
    cleaned = LEAKED_TAG_RE.sub("", normalize_hermes_tags(text))
    return re.sub(r"^\s+", "", cleaned)


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
        self.buffer = normalize_hermes_tags(self.buffer + (delta or ""))
        events = []
        while True:
            match = TAG_RE.search(self.buffer)
            if match:
                before, tag = self.buffer[:match.start()], match.group(0)
                self.buffer = self.buffer[match.end():]
                events += self._emit(before)
                tag_l = normalize_hermes_tags(tag).lower()
                if tag_l == "<think>":
                    self.mode = "think"
                elif tag_l == "</think>":
                    self.mode = "answer"
                elif tag_l == "<tool_call>":
                    self.mode = "tool"
                    self.tool_buffer = ""
                elif tag_l == "</tool_call>":
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
        text = strip_leaked_hermes_tags(text)
        if not text:
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
            result_snip = last_run.get("result")
            result_text = ""
            if result_snip is not None:
                try:
                    result_text = json.dumps(result_snip, ensure_ascii=False)[:1800]
                except TypeError:
                    result_text = str(result_snip)[:1800]
            files = last_run.get("files") or []
            last_run_blob = (
                f"Latest run for this lesson: id={last_run.get('id')} state={last_run.get('state')} "
                f"error={last_run.get('error')!r}\n"
                f"files={files} media_url={last_run.get('media_url')!r} "
                f"has_video={last_run.get('has_video')} has_rollout={last_run.get('has_rollout')}\n"
                f"result_json={result_text or '(none — call analyze_run)'}\n"
                f"log_tail:\n{(last_run.get('log_tail') or '')[:3500]}\n"
            )
        return (
            "You are Hermes, the Q1 Store tutor for Isaac Lab 3.0 and Isaac Sim. You teach by explaining the APIs in the current lesson, "
            "pointing at exact lines of example.py, proposing concrete edits and parameter changes, diagnosing failed runs, analyzing completed "
            "run results (metrics, evaluation, result.mp4 / rollout.json), and repairing the local Isaac Lab / CUDA environment when needed. "
            "Answer in the learner's language (Traditional Chinese by default; English if they write English). Be precise about 3.0 facts: "
            "ProxyArray (.torch/.warp), XYZW quaternions, write_*_to_sim_index/_mask, robot.actuators.target_command.set_*_index, "
            "actuator_effort_limit vs joint_effort_limit, launch_simulation, physics=newton_mjwarp|isaacsim_physx|ovphysx, HEADLESS=1 (no --headless), "
            "VisualizerCfg/VideoRecorderCfg, --checkpoint latest|best. If unsure, search instead of guessing, and cite URLs.\n\n"
            "When the learner reports a failed run, asks to 修復/fix an error, or pastes a Traceback: "
            "1) call read_run_log (or use the Latest run block below), 2) if the error mentions CUDA/driver/torch call diagnose_runtime, "
            "3) if diagnose reports torch/driver mismatch call repair_isaaclab_cuda, "
            "4) if the error mentions PhysxJointAPI / PhysxSchema / ApplyAPI call repair_physx_joint (kitless URDF; NOT an example.py string search), "
            "5) if No module named 'isaacsim' (import only) call repair_isaaclab_importers, "
            "6) if 'Isaac Sim is not installed' / PhysX backend requires Kit AND example.py hardcodes PhysxCfg/UrdfConverter: "
            "call apply_code with newton_mjwarp + UrdfFileCfg (kitless) — do NOT tell them to install full Isaac Sim as the only fix; "
            "this store defaults to kitless Newton. "
            "7) if TypeError/unexpected keyword/AttributeError points at example.py (e.g. NewtonCfg(solver_iterations=…)): "
            "call apply_code with a corrected FULL example.py — Lab 3 uses NewtonCfg(solver_cfg=MJWarpSolverCfg(iterations=N)). "
            "Also give a short Cursor prompt if the store lesson file itself is wrong. "
            "8) for other example.py bugs or when the learner asks to 加 log / 改程式: call apply_code "
            "with the FULL updated example.py — never claim you edited the file without that tool. "
            "9) then start_run to verify. "
            "When the learner asks about 結果/影片/rollout/推論/metrics after a run: call analyze_run, summarize metrics honestly, "
            "and tell them the Lab panel plays result.mp4 (if present) or the Three.js rollout player. "
            "Do NOT ask which lesson — you already know it from context. Never reply that you cannot understand a fix request. "
            "Never answer a runtime Traceback with only「example.py 裡沒有這個字串」— that means you used /code instead of repairing. "
            "Never say you cannot analyze run results — use analyze_run. "
            "Never say「已加上 log／已改好」unless apply_code/propose_code succeeded; the Lab editor shows yellow Hermes marks and `# [Hermes]` notes.\n\n"
            "Always start your reply with your reasoning inside literal <think></think> tags "
            "(never HTML-escape them as &lt;think&gt; or &amp;lt;think&amp;gt;), then the answer. "
            "You have tools. To call one, emit exactly: <tool_call>{\"name\": \"tool_name\", \"arguments\": {...}}</tool_call> and stop; the result arrives in a "
            "<tool_response> message, after which you continue. Never fabricate tool results. Use apply_code for learner-requested edits (加 log 等), "
            "propose_code for optional review diffs, set_params for slider changes, start_run to execute, analyze_run for post-run results, "
            "propose_course when you discover new Isaac Lab 3 material worth a lesson.\n"
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
        # Always attach the newest run for this lesson so Hermes can repair/analyze without asking.
        if not context.get("last_run"):
            try:
                recent = self.t.runs(uid=uid, lesson_id=lesson_id, limit=1)
                if recent:
                    context["last_run"] = self._last_run_context(recent[0]["id"])
            except Exception:
                pass
        elif context.get("last_run", {}).get("id") and not context["last_run"].get("files"):
            try:
                context["last_run"] = self._last_run_context(context["last_run"]["id"])
            except Exception:
                pass
        self.t.append_message(uid, lesson_id, "user", message)
        self.t.touch_last(uid, lesson_id, robot)
        # Slash commands + repair/analyze intents must run real tools. The 8B Hermes GGUF often
        # ignores <tool_call> and just asks for more info — never let that block fixes or result review.
        cmd = message.split(None, 1)[0] if message else ""
        # Coding-edit intents win over repair/analyze so pasted example.py (which may contain
        # "cuda:0") still lands in the Lab editor via apply_code — not only 「加 log」.
        wants_edit = cmd in ("/edit", "/logcode") or bool(EDIT_HINT.search(message))
        wants_analyze = (
            bool(ANALYZE_HINT.search(message))
            and not wants_edit
            and not bool(REPAIR_HINT.search(message))
        )
        if wants_edit:
            yield from self._edit_chat(uid, lesson, context, message)
            return
        force_tools = cmd.startswith("/") or bool(REPAIR_HINT.search(message)) or wants_analyze
        if force_tools:
            note = (
                "以工具模式分析最近執行結果（result／evaluation／影片／rollout）。"
                if wants_analyze or cmd in ("/analyze", "/result")
                else "以工具模式處理修復／指令（讀 log、診斷環境、必要時自動修 CUDA／importers／PhysxJointAPI）。"
            )
            yield from OfflineTutor(self).respond(uid, lesson, context, message, note=note)
            return
        if self.llm is not None and self.llm.chat_ready():
            yield from self._hermes(uid, lesson, context, message)
        else:
            yield from OfflineTutor(self).respond(uid, lesson, context, message)

    def _edit_chat(self, uid, lesson, context, message):
        """Route code-edit requests so the middle Lab pane always receives an apply_code event."""
        cmd = message.split(None, 1)[0] if message else ""
        log_intent = bool(LOG_EDIT_HINT.search(message)) or cmd == "/logcode"
        if log_intent or not (self.llm is not None and self.llm.chat_ready()):
            yield from OfflineTutor(self).respond(
                uid, lesson, context, message,
                note="以工具模式修改 example.py（會寫入中間 Lab 編輯器並標註 Hermes）。",
            )
            return
        nudge = (
            "\n\n[REQUIRED] The learner asked to change this lesson's example.py / coding. "
            "You MUST call apply_code now with the COMPLETE updated example.py (full file). "
            "Do not only describe the edit. The middle Lab editor updates only via apply_code; "
            "yellow Hermes marks and # [Hermes] notes will show who changed it."
        )
        applied = False
        final_bits = []
        for ev in self._hermes(uid, lesson, context, message + nudge):
            if ev.get("type") == "code_apply":
                applied = True
            if ev.get("type") == "token":
                final_bits.append(ev.get("text") or "")
            # Skip the premature done from _hermes if we still need a fallback apply.
            if ev.get("type") == "done" and not applied:
                continue
            yield ev
            if ev.get("type") == "done":
                return
        if applied:
            return
        # Model talked about the edit but skipped the tool — recover from fenced python or offline log inject.
        current = context.get("code") or lesson.get("draft_code") or lesson.get("code", "")
        fenced = extract_fenced_python("".join(final_bits)) or extract_fenced_python(message)
        if fenced and fenced.strip() and fenced.strip() != (current or "").strip():
            yield {"type": "status", "text": "Hermes 未呼叫 apply_code，改從回覆中的程式碼區塊套用到 Lab 編輯器。", "mode": "hermes"}
            result, events = self.run_tool(
                uid, lesson, context, "apply_code",
                {"code": fenced, "explanation": "從對話中的程式碼區塊套用（Hermes 漏呼叫工具時的後備）。"},
            )
            for e in events:
                yield e
            yield {"type": "tool_result", "name": "apply_code", "result": self.summarize(result)}
            text = "已將修改寫入中間 Lab／example.py（黃線＝Hermes）。請檢查後按「執行」。"
            for chunk in re.findall(r".{1,120}(?:\s|$)", text, re.S) or [text]:
                yield {"type": "token", "text": chunk}
            saved = self.t.append_message(uid, lesson["id"], "assistant", text, mode="hermes")
            yield {"type": "done", "message_id": saved["id"], "mode": "hermes"}
            return
        yield from OfflineTutor(self).respond(
            uid, lesson, context, message,
            note="LLM 未套用程式碼，改用離線編輯流程寫入 Lab 編輯器。",
        )

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
        clean_answer = strip_leaked_hermes_tags(final_answer).strip()
        saved = self.t.append_message(
            uid, lesson["id"], "assistant", clean_answer,
            reasoning=all_reasoning.strip()[:6000], mode="hermes",
        )
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
            if name == "analyze_run":
                return self._analyze_run(uid, lesson["id"], arguments.get("run_id") or (context.get("last_run") or {}).get("id")), events
            if name == "diagnose_runtime":
                return self._diagnose_runtime(context), events
            if name == "repair_isaaclab_cuda":
                return self._repair_isaaclab_cuda(), events
            if name == "repair_isaaclab_importers":
                return self._repair_isaaclab_importers(), events
            if name == "repair_physx_joint":
                return self._repair_physx_joint(), events
            if name in ("propose_code", "apply_code"):
                return self._code_edit(
                    uid, lesson, context, arguments,
                    apply=(name == "apply_code") or bool(arguments.get("apply")),
                )
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

    def _code_edit(self, uid, lesson, context, arguments, apply=False):
        """Shared propose/apply path. Returns (result, events)."""
        events = []
        code = str(arguments.get("code", ""))
        explanation = str(arguments.get("explanation", ""))[:2000]
        if not code.strip():
            return {"error": "empty code"}, events
        current = context.get("code") or lesson.get("draft_code") or lesson.get("code", "")
        if code.rstrip() + "\n" == (current.rstrip() + "\n") or code == current:
            return {"ok": False, "error": "proposed code is identical to the editor — nothing to apply", "unchanged": True}, events
        diff = "".join(difflib.unified_diff(
            current.splitlines(True), code.splitlines(True),
            "example.py (current)", "example.py (proposed)",
        ))
        proposal = self.t.append_message(
            uid, lesson["id"], "proposal", code,
            explanation=explanation, diff=diff[:20000],
        )
        if apply:
            # Persist as the active draft so Run / reload keep Hermes' edit.
            self.t.save_draft(uid, lesson["id"], code=code, params=context.get("params"))
            context["code"] = code
            events.append({
                "type": "code_apply",
                "proposal_id": proposal["id"],
                "code": code,
                "diff": diff[:20000],
                "explanation": explanation,
                "author": "Hermes",
            })
            return {
                "ok": True,
                "applied": True,
                "proposal_id": proposal["id"],
                "diff_lines": diff.count("\n"),
                "note": "Lab editor updated with Hermes marks; learner should review yellow lines then Run",
            }, events
        events.append({
            "type": "code_proposal",
            "proposal_id": proposal["id"],
            "code": code,
            "diff": diff[:20000],
            "explanation": explanation,
            "author": "Hermes",
        })
        return {
            "ok": True,
            "applied": False,
            "proposal_id": proposal["id"],
            "diff_lines": diff.count("\n"),
            "note": "learner must accept the diff in the editor",
        }, events

    def _resolve_run(self, uid, lesson_id, run_id=None, log_lines=80):
        run = None
        if run_id:
            try:
                run = self.t.run(run_id, log_lines=log_lines)
            except APIError:
                run = None
        if run is None:
            runs = self.t.runs(uid=uid, lesson_id=lesson_id, limit=1)
            if not runs:
                return None
            run = self.t.run(runs[0]["id"], log_lines=log_lines)
        return run

    def _last_run_context(self, run_id):
        full = self.t.run(run_id, log_lines=60)
        tail = full.get("log_tail") or []
        files = full.get("files") or []
        return {
            "id": full["id"],
            "state": full.get("state"),
            "error": full.get("error"),
            "log_tail": "\n".join(tail) if isinstance(tail, list) else str(tail),
            "runtime": (full.get("runtime") or {}).get("label"),
            "files": files,
            "result": full.get("result"),
            "evaluation": full.get("evaluation"),
            "media_url": full.get("media_url"),
            "has_video": "result.mp4" in files,
            "has_rollout": "rollout.json" in files,
        }

    def _read_run_log(self, uid, lesson_id, run_id=None):
        run = self._resolve_run(uid, lesson_id, run_id, log_lines=80)
        if run is None:
            return {"ok": False, "error": "此課程尚無執行紀錄"}
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

    def _analyze_run(self, uid, lesson_id, run_id=None):
        run = self._resolve_run(uid, lesson_id, run_id, log_lines=40)
        if run is None:
            return {"ok": False, "error": "此課程尚無執行紀錄，請先按「執行」。"}
        directory = self.t.run_dir(run["id"])
        files = run.get("files") or []
        result = run.get("result")
        evaluation = run.get("evaluation")
        progress = run.get("progress")
        media_url = run.get("media_url") or f"/media/tutorial/{run['id']}/"
        summary_bits = []
        if result is None:
            summary_bits.append("尚無 result.json（執行可能未完成或寫入失敗）。")
        else:
            err = result.get("error")
            if err:
                summary_bits.append(f"result.json 標記失敗：{err}")
            else:
                keys = [k for k in ("phase", "message", "physics", "num_envs", "steps", "survived", "reward", "mean_reward", "passed") if k in result]
                summary_bits.append("result.json 欄位：" + (", ".join(f"{k}={result.get(k)!r}" for k in keys) or "（有內容，見 result）"))
        if evaluation:
            passed = evaluation.get("passed")
            summary_bits.append(f"evaluation.json passed={passed!r}")
        if "result.mp4" in files:
            summary_bits.append(f"推論影片可在 Lab 面板播放：{media_url}result.mp4")
        elif "rollout.json" in files:
            summary_bits.append(f"無 mp4；可用 Lab 面板 Three.js 重播 rollout：{media_url}rollout.json")
        else:
            summary_bits.append("尚無 result.mp4／rollout.json（記錄階段可能未跑或 viz 未啟用）。")
        return {
            "ok": True,
            "run_id": run["id"],
            "state": run.get("state"),
            "error": run.get("error"),
            "runtime": (run.get("runtime") or {}).get("label"),
            "lesson_id": run.get("lesson_id") or lesson_id,
            "robot": run.get("robot"),
            "files": files,
            "media_url": media_url,
            "video_url": f"{media_url}result.mp4" if "result.mp4" in files else None,
            "rollout_url": f"{media_url}rollout.json" if "rollout.json" in files else None,
            "result": result,
            "evaluation": evaluation,
            "progress": progress,
            "summary": " ".join(summary_bits),
            "path": str(directory),
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
                    try:
                        imp = subprocess.run(
                            [str(py), "-c", "from isaacsim.asset.importer.urdf import URDFImporter; print('ok')"],
                            capture_output=True, text=True, timeout=60)
                        info["urdf_importer"] = {"ok": imp.returncode == 0, "stderr": (imp.stderr or "")[-500:]}
                        if imp.returncode != 0:
                            info["issues"].append("URDF importer missing (No module named 'isaacsim') — need uv sync --extra importers")
                            info["hints"].append("Call repair_isaaclab_importers (standalone isaacsim-asset-isolated; full Isaac Sim not required for newton_mjwarp)")
                        else:
                            stamp = root / ".q1_store_physx_joint_patch"
                            info["physx_joint_patch"] = stamp.is_file()
                            if not stamp.is_file():
                                info["hints"].append(
                                    "Kitless PhysxJointAPI guard missing — call repair_physx_joint "
                                    "or: bash scripts/repair_isaaclab_physx_joint.sh"
                                )
                    except Exception as exc:
                        info["issues"].append(f"urdf importer probe failed: {exc}")
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

    def _repair_isaaclab_importers(self):
        root = self.t.isaaclab3_root
        if not root or not (root / "pyproject.toml").is_file():
            return {"ok": False, "error": "Q1_ISAACLAB3_ROOT unset or invalid"}
        uv = self.t.uv or "uv"
        try:
            proc = subprocess.run(
                [uv, "sync", "--extra", "importers"],
                cwd=str(root), capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "uv sync --extra importers timed out"}
        out = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-4000:]
        ok = proc.returncode == 0
        if ok:
            stamp = root / ".q1_store_uv_sync_stamp"
            prev = stamp.read_text().strip() if stamp.is_file() else "extra="
            if "importers" not in prev:
                stamp.write_text("extra=importers\n")
        result = {"ok": ok, "exit_code": proc.returncode, "log": out}
        if ok:
            result["diagnose"] = self._diagnose_runtime()
            result["note"] = "Importers installed. Re-run the lesson; URDF→USD no longer needs full Isaac Sim."
        return result

    def _repair_physx_joint(self):
        script = Path(self.t.root) / "scripts" / "repair_isaaclab_physx_joint.sh"
        if not script.is_file():
            return {"ok": False, "error": f"missing {script}"}
        root = self.t.isaaclab3_root
        cmd = ["bash", str(script)]
        if root:
            cmd.append(str(root))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "physx joint patch timed out"}
        out = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-4000:]
        ok = proc.returncode == 0
        result = {
            "ok": ok,
            "exit_code": proc.returncode,
            "log": out,
            "note": (
                "Kitless PhysxJointAPI guard applied (venv importer_utils). "
                "Tutorial launches also use lab3_runner + sitecustomize so old drafts without "
                "lab3_compat still work. Re-run the lesson."
            ),
        }
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
                "/fix", "/diagnose", "/log", "/analyze", "/result", "/edit", "/logcode", "/help")

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
        last_err = f"{(context.get('last_run') or {}).get('error') or ''} {(context.get('last_run') or {}).get('log_tail') or ''}"
        wants_fix = bool(REPAIR_HINT.search(message)) or bool((context.get("last_run") or {}).get("error"))
        wants_edit = cmd in ("/edit", "/logcode") or bool(EDIT_HINT.search(message))
        wants_analyze = (
            cmd in ("/analyze", "/result")
            or (bool(ANALYZE_HINT.search(message)) and not wants_edit and not bool(REPAIR_HINT.search(message)))
        )
        physx_in_msg = bool(PHYSX_HINT.search(message)) or bool(PHYSX_HINT.search(last_err))
        # /code PhysxJointAPI must not become「example.py 裡沒有這個字串」— route to repair.
        if cmd == "/code" and (physx_in_msg or PHYSX_HINT.search(rest) or wants_fix and PHYSX_HINT.search(rest + last_err)):
            yield from self._repair_flow(
                uid, lesson, context, answer_parts, reasoning,
                do_repair=True, message=message,
            )
            text = "\n\n".join(answer_parts)
            for chunk in re.findall(r".{1,120}(?:\s|$)", text, re.S) or [text]:
                yield {"type": "token", "text": chunk}
            saved = self.t.append_message(uid, lesson["id"], "assistant", text, reasoning="\n".join(reasoning), mode="offline")
            yield {"type": "done", "message_id": saved["id"], "mode": "offline"}
            return
        if wants_edit:
            yield from self._edit_flow(uid, lesson, context, answer_parts, reasoning, message=message)
            text = "\n\n".join(answer_parts)
            for chunk in re.findall(r".{1,120}(?:\s|$)", text, re.S) or [text]:
                yield {"type": "token", "text": chunk}
            saved = self.t.append_message(uid, lesson["id"], "assistant", text, reasoning="\n".join(reasoning), mode="offline")
            yield {"type": "done", "message_id": saved["id"], "mode": "offline"}
            return
        if wants_analyze:
            yield from self._analyze_flow(uid, lesson, context, answer_parts, reasoning)
            text = "\n\n".join(answer_parts)
            for chunk in re.findall(r".{1,120}(?:\s|$)", text, re.S) or [text]:
                yield {"type": "token", "text": chunk}
            saved = self.t.append_message(uid, lesson["id"], "assistant", text, reasoning="\n".join(reasoning), mode="offline")
            yield {"type": "done", "message_id": saved["id"], "mode": "offline"}
            return
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
            needle = rest.strip()
            if needle and PHYSX_HINT.search(needle):
                yield from self._repair_flow(uid, lesson, context, answer_parts, reasoning, do_repair=True, message=message)
            else:
                hits = [f"L{i + 1}: {line.strip()}" for i, line in enumerate(code.splitlines()) if needle and needle.lower() in line.lower()]
                answer_parts.append(("在 example.py 找到：\n```\n" + "\n".join(hits[:30]) + "\n```") if hits else "example.py 裡沒有這個字串。")
        elif cmd == "/remember":
            self.t.memory_add(uid, [rest.strip()])
            answer_parts.append("已記住。")
        elif cmd in ("/log", "/diagnose", "/fix") or physx_in_msg or (wants_fix and cmd not in self.COMMANDS):
            yield from self._repair_flow(
                uid, lesson, context, answer_parts, reasoning,
                do_repair=(
                    cmd == "/fix"
                    or physx_in_msg
                    or "修" in message
                    or "fix" in message.lower()
                    or "repair" in message.lower()
                ),
                message=message,
            )
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

    def _analyze_flow(self, uid, lesson, context, answer_parts, reasoning):
        reasoning.append("呼叫 analyze_run 解讀最近執行的 result／evaluation／媒體")
        yield {"type": "reasoning", "text": reasoning[-1] + "\n"}
        yield {"type": "tool_call", "name": "analyze_run", "arguments": {}}
        info, _ = self.agent.run_tool(uid, lesson, context, "analyze_run", {})
        yield {"type": "tool_result", "name": "analyze_run", "result": self.agent.summarize(info)}
        if not info.get("ok"):
            answer_parts.append(info.get("error") or "沒有可分析的執行紀錄。")
            return
        result = info.get("result") or {}
        evaluation = info.get("evaluation") or {}
        lines = [
            f"**執行分析** `{info['run_id'][:8]}` · {info.get('state')} · {info.get('runtime') or ''}",
            info.get("summary") or "",
        ]
        if result:
            pretty = json.dumps(result, ensure_ascii=False, indent=2)
            lines.append(f"**result.json**\n```json\n{pretty[:2500]}\n```")
        if evaluation:
            pretty = json.dumps(evaluation, ensure_ascii=False, indent=2)
            lines.append(f"**evaluation.json**\n```json\n{pretty[:1800]}\n```")
        if info.get("video_url"):
            lines.append(f"推論影片：Lab 面板可直接播放，或開啟 `{info['video_url']}`。")
        elif info.get("rollout_url"):
            lines.append(f"無 mp4；Lab 面板可用 Three.js 重播 rollout：`{info['rollout_url']}`。")
        if info.get("error"):
            lines.append(f"執行錯誤：{info['error']}（若要修環境請用 `/fix`）。")
        answer_parts.append("\n\n".join(p for p in lines if p))

    def _edit_flow(self, uid, lesson, context, answer_parts, reasoning, message=""):
        current = context.get("code") or lesson.get("draft_code") or lesson.get("code", "")
        if not current.strip():
            answer_parts.append("目前沒有 example.py 可改。")
            return
        cmd = (message or "").split(None, 1)[0]
        log_intent = bool(LOG_EDIT_HINT.search(message or "")) or cmd == "/logcode"
        new_code, note = None, ""

        fenced = extract_fenced_python(message or "")
        if fenced and fenced.strip() and fenced.strip() != current.strip():
            reasoning.append("從訊息中的 ```python``` 區塊套用完整 example.py")
            new_code, note = fenced, "依對話貼上的程式碼區塊更新 example.py"
        elif log_intent or cmd in ("/edit", "/logcode"):
            reasoning.append("在 example.py 加上 progress／階段 print log，並套用到 Lab 編輯器")
            new_code, note = inject_progress_logging(current, lesson_id=lesson.get("id") or "")
        else:
            reasoning.append("依對話意圖對 example.py 做可判定的修改並套用到 Lab 編輯器")
            new_code, note = apply_simple_code_intent(current, message or "", lesson_id=lesson.get("id") or "")
            if new_code == current:
                # Still give a visible Hermes touch so the middle pane reflects the edit request.
                new_code, note = inject_edit_request_marker(current, message or "", lesson_id=lesson.get("id") or "")

        yield {"type": "reasoning", "text": reasoning[-1] + "\n"}
        if not new_code or new_code == current:
            answer_parts.append(
                "還無法從這句話判定要改哪一段。請貼上完整 `example.py` 的 ```python``` 區塊，"
                "或更具體地說要改的變數／行為（例如「把 sim_seconds 預設改成 5」）；"
                "一有明確內容就會寫入中間 Lab 編輯器並標 `# [Hermes]`。"
            )
            return
        args = {"code": new_code, "explanation": note}
        yield {"type": "tool_call", "name": "apply_code", "arguments": {"explanation": note}}
        result, events = self.agent.run_tool(uid, lesson, context, "apply_code", args)
        for e in events:
            yield e
        yield {"type": "tool_result", "name": "apply_code", "result": self.agent.summarize(result)}
        if result.get("ok") and result.get("applied"):
            answer_parts.append(
                f"已把修改寫入中間 **Lab／example.py** 編輯器（黃線＝Hermes，註解 `# [Hermes]`）。\n\n"
                f"{note}\n\n請切到「實作」分頁檢查後按「執行」。"
            )
        else:
            answer_parts.append(f"無法套用修改：{result.get('error') or result}")

    def _repair_flow(self, uid, lesson, context, answer_parts, reasoning, do_repair=False, message=""):
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

        err_blob = f"{log_info.get('error') or ''} {log_info.get('log_tail') or ''}"
        err_l = err_blob.lower()
        current_code = context.get("code") or lesson.get("draft_code") or lesson.get("code", "") or ""
        needs_cuda = any(k in err_l for k in ("cuda", "driver", "12080", "cu130")) or any("torch" in i.lower() or "cuda" in i.lower() for i in issues)
        isaac_sim_missing = bool(ISAAC_SIM_MISSING_HINT.search(err_blob)) or bool(ISAAC_SIM_MISSING_HINT.search(message or ""))
        hardcodes_physx = bool(re.search(r"physics\s*=\s*PhysxCfg\s*\(|from isaaclab_physx\.physics import PhysxCfg", current_code))
        # Kit-missing PhysX launch is an example.py preset bug when this store runs kitless — not an importers install.
        needs_kitless_rewrite = isaac_sim_missing and (hardcodes_physx or "UrdfConverter" in current_code or (lesson.get("id") or "").startswith("l04"))
        needs_importers = (
            not needs_kitless_rewrite
            and (
                "no module named 'isaacsim'" in err_l
                or "urdf importer" in " ".join(issues).lower()
                or not ((diag.get("urdf_importer") or {}).get("ok", True))
            )
        )
        needs_physx = bool(PHYSX_HINT.search(err_blob)) or bool(PHYSX_HINT.search(message or ""))
        needs_example_api = (
            bool(EXAMPLE_API_HINT.search(err_blob))
            or bool(EXAMPLE_API_HINT.search(message or ""))
            or needs_kitless_rewrite
        )
        # Don't treat incidental "cuda:0" in example.py Tracebacks as a driver mismatch.
        if needs_example_api and re.search(r"unexpected keyword argument|TypeError:|Isaac Sim is not installed", err_blob, re.I):
            needs_cuda = needs_cuda and bool(re.search(
                r"driver|12080|cu130|cuda.*(error|fail|mismatch|unavailable)|torch\.(cuda|version)",
                err_blob, re.I,
            ))
        repaired_ok = False

        if do_repair and needs_example_api:
            reasoning.append("偵測到 example.py API／參數錯誤，自動套用修正到 Lab 編輯器")
            yield {"type": "reasoning", "text": reasoning[-1] + "\n"}
            current = current_code
            fixed, note = fix_known_example_api_errors(
                current, err_blob, lesson_id=lesson.get("id") or "",
                canonical_code=lesson.get("code") or "",
            )
            if fixed and fixed != current:
                yield {"type": "tool_call", "name": "apply_code", "arguments": {"explanation": note}}
                applied, events = self.agent.run_tool(
                    uid, lesson, context, "apply_code",
                    {"code": fixed, "explanation": note},
                )
                for e in events:
                    yield e
                yield {"type": "tool_result", "name": "apply_code", "result": self.agent.summarize(applied)}
                if applied.get("ok") and applied.get("applied"):
                    repaired_ok = True
                    answer_parts.append(
                        f"這是 **example.py API 用法錯誤**（不是 CUDA／importer 環境問題）。\n\n"
                        f"已寫入中間 Lab 編輯器（黃線＝Hermes）：{note}"
                    )
                    cursor_prompt = cursor_prompt_for_example_api_fix(lesson, err_blob, note)
                    answer_parts.append(
                        "若要同步修正 store 課程檔（給 Cursor）：\n```text\n"
                        + cursor_prompt
                        + "\n```"
                    )
                else:
                    answer_parts.append(f"自動套用 example.py 修正失敗：{applied.get('error') or applied}")
            else:
                cursor_prompt = cursor_prompt_for_example_api_fix(lesson, err_blob, "未能自動改寫；請依 Traceback 修正 API。")
                answer_parts.append(
                    "環境診斷正常，錯誤指向 **example.py**。我無法安全自動改寫這次的 API 差異；"
                    "請用下方 Cursor prompt 改課程／store，或貼上你希望的完整 example.py。\n\n"
                    f"```text\n{cursor_prompt}\n```"
                )

        if do_repair and needs_cuda:
            reasoning.append("偵測到 CUDA/driver 不符，執行 repair_isaaclab_cuda")
            yield {"type": "reasoning", "text": reasoning[-1] + "\n"}
            yield {"type": "tool_call", "name": "repair_isaaclab_cuda", "arguments": {}}
            repaired, _ = self.agent.run_tool(uid, lesson, context, "repair_isaaclab_cuda", {})
            yield {"type": "tool_result", "name": "repair_isaaclab_cuda", "result": self.agent.summarize(repaired)}
            if repaired.get("ok"):
                repaired_ok = True
                answer_parts.append("已將 Isaac Lab 的 torch 對齊本機 driver。")
            else:
                answer_parts.append(f"自動修復失敗：{repaired.get('error') or repaired.get('log')}")
        elif needs_cuda:
            answer_parts.append("這像是 **torch CUDA 與 NVIDIA driver 不符**。輸入 `/fix` 讓我執行 `repair_isaaclab_cuda`，或手動：`bash scripts/repair_isaaclab_cuda.sh`。")

        if do_repair and needs_importers:
            reasoning.append("偵測到缺 URDF importer（isaacsim），執行 repair_isaaclab_importers")
            yield {"type": "reasoning", "text": reasoning[-1] + "\n"}
            yield {"type": "tool_call", "name": "repair_isaaclab_importers", "arguments": {}}
            repaired, _ = self.agent.run_tool(uid, lesson, context, "repair_isaaclab_importers", {})
            yield {"type": "tool_result", "name": "repair_isaaclab_importers", "result": self.agent.summarize(repaired)}
            if repaired.get("ok"):
                repaired_ok = True
                answer_parts.append(
                    "已安裝 **isaaclab[importers]**（`isaacsim-asset-isolated`）。"
                    "這不是完整 Isaac Sim；`physics=newton_mjwarp` 用 URDF 時需要它。"
                )
            else:
                answer_parts.append(f"安裝 importers 失敗：{repaired.get('error') or repaired.get('log')}")
        elif needs_importers:
            answer_parts.append(
                "錯誤是 **`No module named 'isaacsim'`**：Lab 3 的 URDF→USD 需要 "
                "`uv sync --extra importers`。輸入 `/fix` 自動安裝，或手動在 `Q1_ISAACLAB3_ROOT` 執行該指令。"
            )

        if do_repair and needs_physx:
            reasoning.append("偵測到 PhysxJointAPI / schema 錯誤，執行 repair_physx_joint（非 example.py 字串問題）")
            yield {"type": "reasoning", "text": reasoning[-1] + "\n"}
            yield {"type": "tool_call", "name": "repair_physx_joint", "arguments": {}}
            repaired, _ = self.agent.run_tool(uid, lesson, context, "repair_physx_joint", {})
            yield {"type": "tool_result", "name": "repair_physx_joint", "result": self.agent.summarize(repaired)}
            if repaired.get("ok"):
                repaired_ok = True
                answer_parts.append(
                    "這是 **kitless URDF importer** 的 `PhysxJointAPI` 問題（完整 Isaac Sim 才有該 schema），"
                    "不是 example.py 缺字串。已套用 venv 防護，且課程啟動會經 `lab3_runner` 自動 patch。"
                )
            else:
                answer_parts.append(f"PhysxJointAPI 修復失敗：{repaired.get('error') or repaired.get('log')}")
        elif needs_physx:
            answer_parts.append(
                "錯誤是 **`PhysxJointAPI` schema 未註冊**（kitless / `newton_mjwarp` 常見）。"
                "輸入 `/fix` 自動修，或手動：`bash scripts/repair_isaaclab_physx_joint.sh`。"
            )

        if do_repair and repaired_ok:
            code_now = context.get("code") or lesson.get("draft_code") or lesson.get("code") or ""
            can_rerun = "launch_simulation" in code_now or "def main" in code_now
            if can_rerun:
                reasoning.append("修復完成，自動重跑課程驗證")
                yield {"type": "reasoning", "text": reasoning[-1] + "\n"}
                yield {"type": "tool_call", "name": "start_run", "arguments": {}}
                rerun, events = self.agent.run_tool(uid, lesson, context, "start_run", {})
                for e in events:
                    yield e
                yield {"type": "tool_result", "name": "start_run", "result": self.agent.summarize(rerun)}
                if rerun.get("ok"):
                    answer_parts.append(
                        f"已自動重跑 `{rerun['run_id'][:8]}`（{rerun.get('runtime')}），狀態：{rerun.get('state')}。"
                        + (f"\n\n{rerun['error']}" if rerun.get("error") else " 請看右側執行進度。")
                    )
                else:
                    answer_parts.append(f"自動重跑失敗：{rerun.get('error')}")
            else:
                answer_parts.append("已寫入修正；請在 Lab 檢查黃線後按「執行」驗證。")
        elif not needs_cuda and not needs_importers and not needs_physx and not needs_example_api:
            answer_parts.append(
                "若錯誤在 example.py，請說「請修復」讓我自動改並寫入中間編輯器，"
                "或貼上完整 ```python```／輸入 `/edit`；修好後我會 `/run` 驗證。"
            )
        elif not repaired_ok and needs_example_api and not do_repair:
            answer_parts.append("這是 example.py API 錯誤。輸入 `/fix` 或說「請修復」讓我改程式並寫入 Lab 編輯器。")

    @staticmethod
    def help_text(lesson, short=False):
        params = ", ".join(f"`{p['key']}`" for p in lesson.get("params", [])[:8])
        lines = ["可用指令：`/search`、`/arxiv`、`/youtube`、`/fetch`、`/params`、`/run`、`/code`、`/edit`、`/log`、`/analyze`、`/diagnose`、`/fix`、`/propose`、`/remember`、`/help`。"]
        if not short:
            lines.append(
                f"這堂課的參數：{params or '（無）'}。執行失敗時直接說「請修復」或打 `/fix`；"
                "跑完想看結果／影片／rollout 請說「分析結果」或打 `/analyze`；"
                "要加 log／改 example.py 請說「加上 log」或 `/edit`（會寫入中間編輯器並標註 Hermes）。"
            )
        return "\n".join(lines)


def fix_known_example_api_errors(
    code: str, err_text: str, lesson_id: str = "", canonical_code: str = "",
) -> tuple[str, str]:
    """Rewrite known Lab-3 API mismatches in example.py. Returns (code, note)."""
    if not code:
        return code, ""
    text = code
    notes: list[str] = []
    err = err_text or ""

    # Hardcoded PhysxCfg / UrdfConverter without Kit → ship kitless Newton + UrdfFileCfg lesson file.
    if ISAAC_SIM_MISSING_HINT.search(err) and (
        re.search(r"physics\s*=\s*PhysxCfg\s*\(", text)
        or "UrdfConverter" in text
        or (lesson_id or "").startswith("l04")
    ):
        canon = (canonical_code or "").strip()
        if canon and "NewtonCfg" in canon and "UrdfFileCfg" in canon and canon != text.strip():
            notes.append(
                "PhysxCfg/UrdfConverter（需完整 Isaac Sim）→ 課程預設 newton_mjwarp + UrdfFileCfg（kitless）"
            )
            note = f"Hermes 修正 {lesson_id or 'example.py'} Lab 3 API：" + "；".join(notes)
            stamped = _stamp_hermes_note(canon if canon.endswith("\n") else canon + "\n", note)
            return stamped, note
        # Fallback: swap the launch block only.
        if re.search(r"physics\s*=\s*PhysxCfg\s*\(", text):
            text2 = re.sub(
                r"from isaaclab_physx\.physics import PhysxCfg[^\n]*\n+",
                "",
                text,
                count=1,
            )
            text2 = re.sub(
                r"sim_cfg\s*=\s*SimulationCfg\([^)]*physics\s*=\s*PhysxCfg\(\)[^)]*\)",
                'sim_cfg = SimulationCfg(dt=1 / 200, device="cuda:0")\n'
                'PHYSICS = P.get("physics", "newton_mjwarp")\n'
                'if PHYSICS == "isaacsim_physx":\n'
                "    from isaaclab_physx.physics import PhysxCfg\n"
                "    sim_cfg.physics = PhysxCfg()\n"
                "else:\n"
                "    from isaaclab_newton.physics import NewtonCfg\n"
                "    sim_cfg.physics = NewtonCfg()",
                text2,
                count=1,
            )
            if text2 != text:
                text = text2
                notes.append("PhysxCfg() 硬編碼 → physics preset（預設 newton_mjwarp）")

    # Deprecated RSL-RL policy=ActorCritic → actor/critic MLPModel (needs class_name).
    if (
        ("class_name" in err and ("actor" in err.lower() or "KeyError" in err))
        or "RslRlPpoActorCriticCfg" in text
    ) and (
        "RslRlPpoActorCriticCfg" in text
        or re.search(r"\bpolicy\s*=\s*RslRlPpoActorCriticCfg\s*\(", text)
        or (lesson_id or "").startswith("l06")
    ):
        canon = (canonical_code or "").strip()
        if canon and "RslRlMLPModelCfg" in canon and "actor =" in canon and canon != text.strip():
            notes.append(
                "RslRlPpoActorCriticCfg(policy=…) → actor/critic=RslRlMLPModelCfg（rsl-rl 5.x class_name）"
            )
            note = f"Hermes 修正 {lesson_id or 'example.py'} Lab 3 API：" + "；".join(notes)
            return _stamp_hermes_note(canon if canon.endswith("\n") else canon + "\n", note), note

    # root_view.get_masses() is PhysX-only; Lab 3 portable path is data.body_mass (ProxyArray).
    if "get_masses" in err or re.search(r"root_view\.get_masses\s*\(", text):
        text2, n = re.subn(
            r"(?:wp\.to_torch\()?(?:robot|articulation)\.root_view\.get_masses\s*\(\s*\)\)?"
            r"(?:\s+if\s+hasattr\s*\(\s*(?:robot|articulation)\s*,\s*['\"]root_view['\"]\s*\)\s*else\s+None)?",
            "robot.data.body_mass.torch",
            text,
        )
        if n:
            text = text2
            notes.append("root_view.get_masses() → robot.data.body_mass.torch（Newton／PhysX 通用）")

    # NewtonCfg(solver_iterations=N) is invalid in Lab 3 — iterations live on MJWarpSolverCfg.
    if (
        "solver_iterations" in err
        or "NewtonCfg" in err
        or re.search(r"NewtonCfg\s*\(\s*solver_iterations\s*=", text)
    ):
        if re.search(r"NewtonCfg\s*\(\s*solver_iterations\s*=", text):
            if "MJWarpSolverCfg" not in text:
                text = re.sub(
                    r"from isaaclab_newton\.physics import NewtonCfg\b",
                    "from isaaclab_newton.physics import MJWarpSolverCfg, NewtonCfg",
                    text,
                    count=1,
                )
                if "MJWarpSolverCfg" not in text:
                    # Already imported NewtonCfg differently — inject a dedicated import.
                    text = "from isaaclab_newton.physics import MJWarpSolverCfg, NewtonCfg\n" + text
            text2, n = re.subn(
                r"NewtonCfg\s*\(\s*solver_iterations\s*=\s*([^)]+)\)",
                r"NewtonCfg(solver_cfg=MJWarpSolverCfg(iterations=\1))",
                text,
            )
            if n:
                text = text2
                notes.append(
                    "NewtonCfg(solver_iterations=…) → NewtonCfg(solver_cfg=MJWarpSolverCfg(iterations=…))"
                )

    if text == code:
        return code, ""
    note = f"Hermes 修正 {lesson_id or 'example.py'} Lab 3 API：" + "；".join(notes)
    return _stamp_hermes_note(text, note), note


def _stamp_hermes_note(code: str, note: str) -> str:
    """Insert a # [Hermes] attribution near the top of example.py (after shebang/docstring)."""
    if not code or not note:
        return code
    marker = f"# [Hermes] {note}"
    if marker in code or "# [Hermes] Hermes 修正" in code:
        return code if code.endswith("\n") else code + "\n"
    lines = code.splitlines(True)
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    if insert_at < len(lines) and lines[insert_at].lstrip().startswith('"""'):
        # Skip module docstring.
        if lines[insert_at].count('"""') >= 2 and lines[insert_at].strip() != '"""':
            insert_at += 1
        else:
            insert_at += 1
            while insert_at < len(lines) and '"""' not in lines[insert_at]:
                insert_at += 1
            if insert_at < len(lines):
                insert_at += 1
    lines.insert(insert_at, marker + "\n")
    out = "".join(lines)
    return out if out.endswith("\n") else out + "\n"


def cursor_prompt_for_example_api_fix(lesson: dict, err_text: str, note: str) -> str:
    lid = (lesson or {}).get("id") or "lesson"
    title = (lesson or {}).get("title") or lid
    err_snip = re.sub(r"\s+", " ", (err_text or "").strip())[:500]
    extra = ""
    if ISAAC_SIM_MISSING_HINT.search(err_text or ""):
        extra = (
            " Prefer physics=newton_mjwarp with sim_utils.UrdfFileCfg (kitless). "
            "Only use UrdfConverter + PhysxCfg when physics=isaacsim_physx and full Isaac Sim is installed. "
        )
    return (
        f"In q1_store_urdf, fix Isaac Lab 3 tutorial lesson `{lid}` ({title}).\n"
        f"Runtime error from example.py:\n{err_snip}\n\n"
        f"Hermes note: {note}\n\n"
        "Update tutorials/lessons/<id>/example.py (and lesson.md if docs show the old API) so the "
        "snippet matches the installed Isaac Lab 3 checkout under Q1_ISAACLAB3_ROOT. "
        "For Newton MJWarp, use NewtonCfg(solver_cfg=MJWarpSolverCfg(iterations=N)) — not "
        "NewtonCfg(solver_iterations=N). Keep the tutorial param key solver_iterations as the UI slider "
        "that maps into MJWarpSolverCfg.iterations."
        + extra
        + " After editing, restart the store "
        "(bash scripts/restart_store.sh --port 8878) and re-run the lesson."
    )


def extract_fenced_python(text: str) -> str | None:
    """Return the largest ```python``` / ``` fence body, if any."""
    if not text:
        return None
    blocks = [m.group(1).strip("\n") for m in FENCED_PY_RE.finditer(text)]
    if not blocks:
        return None
    blocks.sort(key=len, reverse=True)
    body = blocks[0]
    # Ignore tiny snippets that are clearly not a full example.py
    if body.count("\n") < 5 and "def " not in body and "import " not in body:
        return None
    return body + ("\n" if not body.endswith("\n") else "")


def apply_simple_code_intent(code: str, message: str, lesson_id: str = "") -> tuple[str, str]:
    """Best-effort offline edits: assignment changes like 把 sim_seconds 改成 5."""
    if not code or not message:
        return code, ""
    # 把 foo 改成 5 / set foo to 5 / foo=5 in the request targeting example.py values
    patterns = [
        re.compile(
            r"(?:把|將)?\s*[`'\"]?([A-Za-z_][\w]*)[`'\"]?\s*"
            r"(?:改成|改為|設為|設成|设置成|設置成|=|to)\s*[`'\"]?([^`'\",，。\s]+)[`'\"]?",
            re.I,
        ),
        re.compile(r"\b([A-Za-z_][\w]*)\s*=\s*([^\s,，。]+)"),
    ]
    text = code
    notes = []
    for pat in patterns:
        for key, raw in pat.findall(message):
            if key in ("example", "py", "code", "coding", "log", "print"):
                continue
            val = raw.strip().rstrip("。.;；")
            # Prefer P.get("key", default) default replacement
            def_repl = re.compile(
                rf'(P\.get\(\s*[\'"]{re.escape(key)}[\'"]\s*,\s*)([^)]+)(\))'
            )
            if def_repl.search(text):
                text2, n = def_repl.subn(rf"\g<1>{val}\3", text, count=1)
                if n:
                    text = text2
                    notes.append(f"P.get('{key}', …) → {val}")
                    continue
            # Module-level KEY = ...
            assign = re.compile(rf'^(\s*{re.escape(key)}\s*=\s*)([^\n]+)$', re.M)
            if assign.search(text):
                text2, n = assign.subn(rf"\g<1>{val}", text, count=1)
                if n:
                    text = text2
                    notes.append(f"{key} = {val}")
    if text == code:
        return code, ""
    note = f"Hermes 依對話調整 {lesson_id or 'example.py'}：" + "；".join(notes)
    return text, note


def inject_edit_request_marker(code: str, message: str, lesson_id: str = "") -> tuple[str, str]:
    """Ensure an edit request still lands in the editor with Hermes attribution."""
    if not code:
        return code, ""
    summary = re.sub(r"\s+", " ", (message or "").strip())[:120]
    marker = f"# [Hermes] 對話修改請求：{summary}"
    if marker in code or "# [Hermes] 對話修改請求：" in code:
        return code, "already marked"
    lines = code.splitlines(True)
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    if insert_at < len(lines) and lines[insert_at].startswith('"""'):
        # After module docstring
        j = insert_at + 1
        while j < len(lines) and '"""' not in lines[j]:
            j += 1
        if j < len(lines):
            insert_at = j + 1
    lines.insert(insert_at, marker + "\n")
    note = f"已在 {lesson_id or 'example.py'} 標註 Hermes 修改請求；請補充具體改法或貼上完整程式碼以便覆寫。"
    return "".join(lines), note


def inject_progress_logging(code: str, lesson_id: str = "") -> tuple[str, str]:
    """Deterministically add stdout logs around progress() / key phases. Idempotent."""
    if not code:
        return code, "empty code"
    if "[hermes-log]" in code or "Hermes progress log" in code:
        return code, "already instrumented"
    lines = code.splitlines(True)
    out: list[str] = []
    inserted_progress_print = False
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        # After progress() writes progress.json, mirror to stdout for run.log.
        if (
            not inserted_progress_print
            and re.search(r'progress\.json"\)\.write_text\(', line)
        ):
            indent = re.match(r"^(\s*)", line).group(1)
            # Include following continuation lines of write_text(...) if any.
            while i + 1 < len(lines) and not lines[i].rstrip().endswith(")"):
                i += 1
                out.append(lines[i])
            out.append(f'{indent}print(f"[hermes-log] phase={{phase}} iter={{iteration}}/{{total}} extra={{extra}}", flush=True)  # Hermes progress log\n')
            inserted_progress_print = True
        i += 1
    text = "".join(out)
    # Lesson-01 style: log physics choice right after PHYSICS is resolved.
    if "PHYSICS =" in text and 'print(f"[hermes-log] physics=' not in text:
        physics_log = (
            'print(f"[hermes-log] physics={PHYSICS} robot={P.get(\'robot\')} '
            'envs={P.get(\'num_envs\')}", flush=True)  # Hermes startup log\n'
        )
        text = re.sub(r"(PHYSICS\s*=\s*P\.get\([^\n]+\)\n)", r"\1" + physics_log, text, count=1)
    # Before writing result.json, log summary keys if a summary dict exists.
    if "result.json" in text and 'print(f"[hermes-log] result=' not in text:
        result_log = 'print(f"[hermes-log] result={summary}", flush=True)  # Hermes result log\n    '
        text = re.sub(
            r'(\(JOB\s*/\s*"result\.json"\)\.write_text\()',
            result_log + r"\1",
            text,
            count=1,
        )
    if text == code and not inserted_progress_print:
        # Fallback: append a module-level note + print after STARTED if present.
        if "STARTED = time.monotonic()" in text and "[hermes-log] boot" not in text:
            boot_log = (
                "STARTED = time.monotonic()\n"
                "print(f\"[hermes-log] boot lesson={P.get('lesson_id', '?')} "
                "robot={P.get('robot')}\", flush=True)  # Hermes boot log"
            )
            text = text.replace("STARTED = time.monotonic()", boot_log, 1)
    note = (
        f"Hermes 在 {lesson_id or 'example.py'} 加上 `[hermes-log]` 進度／階段 print，"
        "執行時會出現在 run.log 與右側進度 log。"
    )
    return text, note
