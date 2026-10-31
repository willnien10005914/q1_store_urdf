"""Hermes tutor agent: streams reasoning + answer, calls tools (web/arXiv/YouTube search, fetch,
read/propose example code, set params, start a run, propose a course, remember facts) and keeps
per-user, per-lesson conversation memory in the tutorial store.

The agent speaks Hermes' native function-calling format (``<tool_call>{json}</tool_call>`` with
``<tool_response>`` replies) and asks the model to think inside ``<think>`` tags so the browser can
show a live reasoning window. When no local LLM is running an offline tutor answers from the lesson
text and still exposes the tools through slash commands; it labels itself as offline.
"""
from __future__ import annotations

import difflib
import json
import re
import time

from . import research
from .service import APIError

MAX_TOOL_ROUNDS = 4
HOLDBACK = 16  # characters kept back while streaming so tags are never split across events
TOOLS = [
    {"name": "web_search", "description": "Search the web (DuckDuckGo). Use for Isaac Lab / Isaac Sim docs, GitHub issues and release notes.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "arxiv_search", "description": "Search arXiv papers.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "youtube_search", "description": "Search YouTube videos.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "fetch_url", "description": "Fetch a web page as text.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "read_example", "description": "Read the current example.py the learner is editing (returns the full source).", "parameters": {"type": "object", "properties": {}}},
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
        return (
            "You are Hermes, the Q1 Store tutor for Isaac Lab 3.0 and Isaac Sim. You teach by explaining the APIs in the current lesson, "
            "pointing at exact lines of example.py, proposing concrete edits and parameter changes, and running experiments. "
            "Answer in the learner's language (Traditional Chinese by default; English if they write English). Be precise about 3.0 facts: "
            "ProxyArray (.torch/.warp), XYZW quaternions, write_*_to_sim_index/_mask, robot.actuators.target_command.set_*_index, "
            "actuator_effort_limit vs joint_effort_limit, launch_simulation, physics=newton_mjwarp|isaacsim_physx|ovphysx, HEADLESS=1 (no --headless), "
            "VisualizerCfg/VideoRecorderCfg, --checkpoint latest|best. If unsure, search instead of guessing, and cite URLs.\n\n"
            "Always start your reply with your reasoning inside <think></think>, then the answer. "
            "You have tools. To call one, emit exactly: <tool_call>{\"name\": \"tool_name\", \"arguments\": {...}}</tool_call> and stop; the result arrives in a "
            "<tool_response> message, after which you continue. Never fabricate tool results. Use propose_code for any code change (full file), "
            "set_params for slider changes, start_run to execute, propose_course when you discover new Isaac Lab 3 material worth a lesson.\n"
            f"<tools>{json.dumps(TOOLS, ensure_ascii=False)}</tools>\n\n"
            f"Learner: {progress.get('name')} · level {progress['level']['id']} · {progress['completed']}/{progress['total']} lessons done.\n"
            f"Memory about the learner: {json.dumps(memory, ensure_ascii=False) if memory else 'none yet'}\n"
            f"Lesson: {lesson['id']} — {lesson.get('title')} (category {lesson.get('category')}, robot {context.get('robot', 'leg')}).\n"
            f"Lesson APIs: {', '.join(lesson.get('apis', []))}\nCurrent params: {json.dumps(params, ensure_ascii=False)}\n"
            f"Lesson text (markdown):\n{lesson.get('markdown', '')[:6000]}\n"
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

    COMMANDS = ("/search", "/arxiv", "/youtube", "/fetch", "/run", "/params", "/code", "/remember", "/help")

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

    @staticmethod
    def help_text(lesson, short=False):
        params = ", ".join(f"`{p['key']}`" for p in lesson.get("params", [])[:8])
        lines = ["可用指令：`/search <關鍵字>`、`/arxiv <關鍵字>`、`/youtube <關鍵字>`、`/fetch <url>`、`/params key=value …`、`/run`、`/code <字串>`、`/propose <標題>`、`/remember <事實>`。"]
        if not short:
            lines.append(f"這堂課的參數：{params or '（無）'}。右側可直接改 example.py 再按「執行」。")
        return "\n".join(lines)
