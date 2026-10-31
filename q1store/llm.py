"""Local LLM runtime for the tutorial: detect GPU/runtimes, list Hermes models, download with
progress, launch an OpenAI-compatible server (vLLM or llama.cpp) or run transformers in-process,
and stream chat completions.

Only the standard library is imported at module level; vllm / llama.cpp / transformers /
huggingface_hub are optional and probed at runtime so the store starts on any machine and tells
the user exactly what is missing instead of pretending.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

MODELS = [
    {"id": "hermes3-8b-gguf", "label": "Hermes 3 · Llama 3.1 8B · Q4_K_M (GGUF)", "repo": "NousResearch/Hermes-3-Llama-3.1-8B-GGUF",
     "files": ["Hermes-3-Llama-3.1-8B.Q4_K_M.gguf"], "size_bytes": 4920733824, "backends": ["llama_cpp"], "vram_gb_min": 6,
     "recommended": True, "note": "Hermes 原生 <tool_call> 函式呼叫；16 GB 筆電 GPU 的首選。"},
    {"id": "hermes3-3b-gguf", "label": "Hermes 3 · Llama 3.2 3B · Q4_K_M (GGUF)", "repo": "NousResearch/Hermes-3-Llama-3.2-3B-GGUF",
     "files": ["Hermes-3-Llama-3.2-3B.Q4_K_M.gguf"], "size_bytes": 2019373888, "backends": ["llama_cpp"], "vram_gb_min": 3,
     "note": "最快下載、最省 VRAM；推理品質較 8B 弱。"},
    {"id": "qwen25-coder-7b-gguf", "label": "Qwen2.5 Coder 7B Instruct · Q4_K_M (GGUF)", "repo": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
     "files": ["qwen2.5-coder-7b-instruct-q4_k_m.gguf"], "size_bytes": 4683073536, "backends": ["llama_cpp"], "vram_gb_min": 6,
     "note": "改 example.py 時程式碼品質較佳的替代選擇。"},
    {"id": "hermes3-3b-hf", "label": "Hermes 3 · Llama 3.2 3B (safetensors)", "repo": "NousResearch/Hermes-3-Llama-3.2-3B",
     "files": None, "size_bytes": 6450000000, "backends": ["vllm", "transformers"], "vram_gb_min": 8,
     "note": "vLLM 或 transformers 直接載入 bf16。"},
    {"id": "hermes3-8b-hf", "label": "Hermes 3 · Llama 3.1 8B (safetensors)", "repo": "NousResearch/Hermes-3-Llama-3.1-8B",
     "files": None, "size_bytes": 16070000000, "backends": ["vllm", "transformers"], "vram_gb_min": 18,
     "note": "bf16 需 >16 GB VRAM；在 16 GB GPU 上請改用 GGUF 或 vLLM --quantization。"},
]
HF_IGNORE = ["*.msgpack", "*.h5", "*.onnx", "*.ot", "original/*", "*.pth", "*.bin"]
SERVER_PORT_DEFAULT = 8877


def module_available(name):
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def gpu_info():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"available": False, "reason": "nvidia-smi not found"}
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": str(exc)}
    gpus = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            gpus.append({"name": parts[0], "memory_total_mb": int(float(parts[1])), "memory_used_mb": int(float(parts[2])),
                         "driver": parts[3] if len(parts) > 3 else None})
    return {"available": bool(gpus), "gpus": gpus, "reason": None if gpus else (out.stderr.strip() or "no GPU reported")}


def hf_cache_dir():
    return Path(os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME", Path.home() / ".cache/huggingface") / "hub").expanduser()


def dir_size(path):
    total = 0
    for p in Path(path).rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
            elif p.is_symlink() and p.resolve().is_file():
                total += p.resolve().stat().st_size
        except OSError:
            pass
    return total


class LLMManager:
    def __init__(self, data_dir, clock=time.time):
        self.data_dir = Path(data_dir)
        self.models_dir = self.data_dir / "models"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "llm-server.json"
        self.clock = clock
        self.lock = threading.RLock()
        self.downloads = {}
        self.process = None
        self.server = self._load_state()
        self.external_url = os.environ.get("Q1_LLM_BASE_URL")
        self.external_model = os.environ.get("Q1_LLM_MODEL")
        self._pipeline = None

    # ------------------------------------------------------------ detection
    def runtimes(self):
        llama = shutil.which("llama-server") or os.environ.get("Q1_LLAMA_SERVER")
        return {
            "vllm": {"available": module_available("vllm"), "python": sys.executable},
            "llama_cpp": {"available": bool(llama and Path(llama).exists()) or module_available("llama_cpp"),
                          "server": llama if llama and Path(llama).exists() else None, "python_binding": module_available("llama_cpp"),
                          "hint": None if llama else "pip install 'llama-cpp-python[server]' 或安裝 llama.cpp 的 llama-server 並加入 PATH。"},
            "transformers": {"available": module_available("transformers") and module_available("torch")},
            "huggingface_hub": {"available": module_available("huggingface_hub"), "cli": shutil.which("hf") or shutil.which("huggingface-cli")},
            "external": {"available": bool(self.external_url), "base_url": self.external_url, "model": self.external_model},
        }

    def local_path(self, model):
        """Where the model lives if it is already on disk (store dir first, then the HF cache)."""
        local = self.models_dir / model["repo"].replace("/", "--")
        if model["files"]:
            for f in model["files"]:
                if (local / f).is_file():
                    return local / f
        elif local.is_dir() and any(local.glob("*.safetensors")):
            return local
        cache = hf_cache_dir() / ("models--" + model["repo"].replace("/", "--")) / "snapshots"
        if cache.is_dir():
            for snap in sorted(cache.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if model["files"]:
                    for f in model["files"]:
                        if (snap / f).exists():
                            return snap / f
                elif any(snap.glob("*.safetensors")):
                    return snap
        return None

    def models(self):
        runtimes = self.runtimes()
        out = []
        for model in MODELS:
            path = self.local_path(model)
            download = self.downloads.get(model["id"])
            out.append({**model, "local": path is not None, "path": str(path) if path else None,
                        "runnable_backends": [b for b in model["backends"] if runtimes.get(b, {}).get("available")],
                        "download": download.snapshot() if download else None})
        return out

    def status(self):
        with self.lock:
            server = dict(self.server)
        server["running"] = self.server_alive()
        if not server["running"] and server.get("state") == "running":
            server["state"] = "stopped"
        return {"gpu": gpu_info(), "runtimes": self.runtimes(), "models": self.models(), "server": server,
                "chat_ready": self.chat_ready(), "data_dir": str(self.models_dir)}

    # ------------------------------------------------------------- download
    def start_download(self, model_id):
        model = next((m for m in MODELS if m["id"] == model_id), None)
        if model is None:
            raise ValueError("unknown model")
        with self.lock:
            existing = self.downloads.get(model_id)
            if existing and existing.alive():
                return existing.snapshot()
            if self.local_path(model):
                return {"model_id": model_id, "state": "complete", "percent": 100, "note": "already local"}
            target = self.models_dir / model["repo"].replace("/", "--")
            job = Download(model, target, self.runtimes()["huggingface_hub"], self.clock)
            self.downloads[model_id] = job
            job.start()
            return job.snapshot()

    def download_status(self, model_id=None):
        if model_id:
            job = self.downloads.get(model_id)
            return job.snapshot() if job else None
        return [job.snapshot() for job in self.downloads.values()]

    # --------------------------------------------------------------- server
    def _load_state(self):
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"state": "stopped", "backend": None, "model_id": None, "base_url": None, "pid": None, "port": None, "log": None}

    def _save_state(self):
        self.state_path.write_text(json.dumps(self.server))

    def server_alive(self):
        pid = self.server.get("pid")
        if self.server.get("backend") == "transformers":
            return self._pipeline is not None
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
        except (OSError, ValueError):
            return False
        return True

    def chat_ready(self):
        if self.external_url:
            return True
        return self.server.get("state") == "running" and self.server_alive() and self._health()

    def _health(self):
        url = self.server.get("base_url")
        if self.server.get("backend") == "transformers":
            return self._pipeline is not None
        if not url:
            return False
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/models", timeout=2) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def start_server(self, model_id, backend=None, port=None, gpu_layers=99, context=8192):
        model = next((m for m in MODELS if m["id"] == model_id), None)
        if model is None:
            raise ValueError("unknown model")
        path = self.local_path(model)
        if path is None:
            raise ValueError("model not downloaded yet")
        runtimes = self.runtimes()
        backend = backend or next((b for b in model["backends"] if runtimes[b]["available"]), None)
        if backend not in model["backends"]:
            raise ValueError(f"backend {backend!r} cannot serve {model_id}")
        if not runtimes.get(backend, {}).get("available"):
            raise ValueError(f"runtime {backend} is not installed on this machine")
        self.stop_server()
        port = int(port or os.environ.get("Q1_LLM_PORT") or SERVER_PORT_DEFAULT)
        log = self.data_dir / f"llm-{backend}.log"
        if backend == "transformers":
            threading.Thread(target=self._load_pipeline, args=(path, model_id), daemon=True).start()
            with self.lock:
                self.server = {"state": "starting", "backend": backend, "model_id": model_id, "base_url": None, "pid": None,
                               "port": None, "log": str(log), "started_at": self.clock(), "model_path": str(path)}
                self._save_state()
            return dict(self.server)
        if backend == "llama_cpp":
            if runtimes["llama_cpp"]["server"]:
                cmd = [runtimes["llama_cpp"]["server"], "-m", str(path), "--port", str(port), "--host", "127.0.0.1",
                       "-ngl", str(gpu_layers), "-c", str(context), "--jinja"]
            else:
                cmd = [sys.executable, "-m", "llama_cpp.server", "--model", str(path), "--port", str(port), "--host", "127.0.0.1",
                       "--n_gpu_layers", str(gpu_layers), "--n_ctx", str(context), "--chat_format", "chatml-function-calling"]
        else:  # vllm
            cmd = [sys.executable, "-m", "vllm.entrypoints.openai.api_server", "--model", str(path), "--served-model-name", model_id,
                   "--port", str(port), "--host", "127.0.0.1", "--max-model-len", str(context), "--gpu-memory-utilization", "0.85",
                   "--enable-auto-tool-choice", "--tool-call-parser", "hermes"]
        with log.open("ab") as stream:
            self.process = subprocess.Popen(cmd, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        with self.lock:
            self.server = {"state": "starting", "backend": backend, "model_id": model_id, "base_url": f"http://127.0.0.1:{port}/v1",
                           "pid": self.process.pid, "port": port, "log": str(log), "started_at": self.clock(), "model_path": str(path),
                           "command": cmd}
            self._save_state()
        threading.Thread(target=self._wait_ready, daemon=True).start()
        return dict(self.server)

    def _wait_ready(self, timeout=900):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.server_alive():
                with self.lock:
                    self.server["state"] = "failed"
                    self.server["error"] = "server process exited; see log"
                    self._save_state()
                return
            if self._health():
                with self.lock:
                    self.server["state"] = "running"
                    self._save_state()
                return
            time.sleep(2)

    def _load_pipeline(self, path, model_id):
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(str(path))
            model = AutoModelForCausalLM.from_pretrained(str(path), torch_dtype=torch.bfloat16, device_map="auto")
            self._pipeline = (tokenizer, model)
            with self.lock:
                self.server["state"] = "running"
                self._save_state()
        except Exception as exc:  # report, never hide
            with self.lock:
                self.server["state"] = "failed"
                self.server["error"] = f"{type(exc).__name__}: {exc}"
                self._save_state()

    def stop_server(self):
        pid = self.server.get("pid")
        if pid:
            try:
                os.killpg(int(pid), signal.SIGTERM)
            except (OSError, ValueError):
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except (OSError, ValueError):
                    pass
        self._pipeline = None
        self.process = None
        with self.lock:
            self.server = {"state": "stopped", "backend": None, "model_id": None, "base_url": None, "pid": None, "port": None, "log": None}
            self._save_state()
        return dict(self.server)

    def server_log(self, lines=60):
        log = self.server.get("log")
        if not log or not Path(log).exists():
            return []
        return Path(log).read_text(errors="replace").splitlines()[-lines:]

    # ----------------------------------------------------------------- chat
    def chat_stream(self, messages, temperature=0.3, max_tokens=1024, stop=None):
        """Yield text deltas. Raises RuntimeError when no runtime is ready."""
        if self.external_url:
            yield from self._openai_stream(self.external_url, self.external_model or "default", messages, temperature, max_tokens, stop)
            return
        if self.server.get("backend") == "transformers" and self._pipeline is not None:
            yield from self._transformers_stream(messages, temperature, max_tokens)
            return
        if self.server.get("state") == "running" and self.server.get("base_url"):
            yield from self._openai_stream(self.server["base_url"], self.server.get("model_id") or "default", messages, temperature, max_tokens, stop)
            return
        raise RuntimeError("no LLM runtime is running")

    def _openai_stream(self, base_url, model, messages, temperature, max_tokens, stop):
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": True}
        if stop:
            payload["stop"] = stop
        request = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ.get("Q1_LLM_API_KEY", "none")})
        with urllib.request.urlopen(request, timeout=600) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    text = delta.get("content") or delta.get("reasoning_content")
                    if text:
                        yield text

    def _transformers_stream(self, messages, temperature, max_tokens):
        import torch
        from transformers import TextIteratorStreamer
        tokenizer, model = self._pipeline
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)
        kwargs = dict(**inputs, streamer=streamer, max_new_tokens=max_tokens, do_sample=temperature > 0, temperature=max(temperature, 1e-3))
        thread = threading.Thread(target=model.generate, kwargs=kwargs, daemon=True)
        thread.start()
        for text in streamer:
            yield text


class Download:
    """Background Hugging Face download with byte-level progress derived from the target directory."""

    def __init__(self, model, target, hub, clock):
        self.model, self.target, self.hub, self.clock = model, Path(target), hub, clock
        self.id = uuid.uuid4().hex
        self.state = "queued"
        self.error = None
        self.started_at = clock()
        self.finished_at = None
        self.thread = None
        self.process = None
        self.lock = threading.Lock()

    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def downloaded_bytes(self):
        if not self.target.exists():
            return 0
        total = 0
        for p in self.target.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
        return total

    def snapshot(self):
        done = self.downloaded_bytes()
        total = self.model.get("size_bytes") or 0
        percent = 100 if self.state == "complete" else min(99, int(done * 100 / total)) if total else None
        elapsed = (self.finished_at or self.clock()) - self.started_at
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 and total and self.state == "downloading" else None
        return {"id": self.id, "model_id": self.model["id"], "repo": self.model["repo"], "state": self.state, "error": self.error,
                "downloaded_bytes": done, "total_bytes": total, "percent": percent, "rate_bytes_s": int(rate),
                "eta_s": None if eta is None else int(eta), "target": str(self.target)}

    def _run(self):
        self.state = "downloading"
        self.target.mkdir(parents=True, exist_ok=True)
        try:
            if self.hub.get("available"):
                from huggingface_hub import hf_hub_download, snapshot_download
                if self.model["files"]:
                    for f in self.model["files"]:
                        hf_hub_download(self.model["repo"], f, local_dir=str(self.target))
                else:
                    snapshot_download(self.model["repo"], local_dir=str(self.target), ignore_patterns=HF_IGNORE)
            elif self.hub.get("cli"):
                cmd = [self.hub["cli"], "download", self.model["repo"], *(self.model["files"] or []), "--local-dir", str(self.target)]
                if not self.model["files"]:
                    for pattern in HF_IGNORE:
                        cmd += ["--exclude", pattern]
                self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                _, err = self.process.communicate()
                if self.process.returncode != 0:
                    raise RuntimeError(err.strip()[-800:] or f"exit {self.process.returncode}")
            else:
                raise RuntimeError("neither huggingface_hub nor the hf CLI is installed: pip install huggingface_hub")
            self.state = "complete"
        except Exception as exc:
            self.state = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.finished_at = self.clock()
            for stray in self.target.rglob("*.incomplete"):
                if self.state == "complete":
                    stray.unlink(missing_ok=True)
