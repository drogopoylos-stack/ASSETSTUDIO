"""Preserve a workspace's Codex thread and expose its generated images to the UI."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from . import fsutil
from .config import DATA_DIR

ROOT = DATA_DIR / "codex-runs"
LOCK = threading.RLock()
UUID = re.compile(r"^[0-9a-fA-F-]{36}$")


def folder(project_id):
    return ROOT / hashlib.sha256(project_id.encode()).hexdigest()[:24]


def load(project_id):
    try:
        return json.loads((folder(project_id) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(project_id, state):
    dest = folder(project_id)
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / "state.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    fsutil.replace(tmp, dest / "state.json")


def parse(text):
    """Read CLI JSON events; older plain logs remain viewable after upgrading."""
    thread_id = ""
    messages, error = {}, ""
    activities = {}
    status = "working"
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id", "")
        item = event.get("item") or {}
        if item.get("type") and item.get("type") != "agent_message":
            activities[item.get("id", str(len(activities)))] = item
        if event.get("type") == "turn.completed":
            status = "completed"
        if item.get("type") == "agent_message":
            messages[item.get("id", str(len(messages)))] = item.get("text", "")
        if event.get("type") in ("turn.failed", "error"):
            err = event.get("error") or {}
            error = err.get("message", "") if isinstance(err, dict) else str(err)
            error = error or event.get("message", "Codex request failed")
            status = "failed"
    if not thread_id:
        match = re.search(r"^session id: ([0-9a-f-]{36})", text, re.M)
        if match:
            thread_id = match[1]
        if "\ncodex\n" in text:
            # The CLI prints the final answer twice; don't render its diagnostics as chat.
            messages["legacy"] = text.rsplit("\ncodex\n", 1)[1].split("\ntokens used\n", 1)[0].strip()
    return {"thread_id": thread_id if UUID.fullmatch(thread_id) else "",
            "reply": "\n\n".join(messages.values()), "error": error,
            "activities": list(activities.values()), "status": status}


def import_images(project_id, thread_id):
    if not UUID.fullmatch(thread_id or ""):
        return []
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    source = home / "generated_images" / thread_id
    result = []
    for path in sorted(source.glob("*")):
        if path.suffix.lower() not in (".png", ".webp", ".jpg", ".jpeg") or not path.is_file():
            continue
        dest = folder(project_id) / "images" / thread_id / path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.stat().st_size != path.stat().st_size:
            shutil.copy2(path, dest)
        result.append({"name": path.name, "path": str(dest)})
    return result


def inspect(project_id, log):
    with LOCK:
        state = load(project_id)
        if "turns" not in state:
            state["turns"] = ([{ "id": "recovered", "reply": state.get("reply", ""),
                "images": state.get("images", []), "status": "completed" }]
                if state.get("reply") or state.get("images") else [])
        try:
            stat = log.stat()
            stamp = [stat.st_mtime_ns, stat.st_size]
            if state.get("log_stamp") != stamp:
                parsed = parse(log.read_text(encoding="utf-8", errors="replace"))
                if parsed["thread_id"] or state.get("thread_id"):
                    state.update({k: v for k, v in parsed.items() if k != "thread_id" or v})
                    state["log_stamp"] = stamp
        except OSError:
            pass
        if state.get("thread_id"):
            images = import_images(project_id, state["thread_id"])
            if images != state.get("images"):
                state["images"] = images
        if state.get("active_turn") and state["turns"]:
            turn = state["turns"][-1]
            for key in ("reply", "error", "activities", "status"):
                turn[key] = state.get(key, "" if key != "activities" else [])
            previous = {img["path"] for old in state["turns"][:-1] for img in old.get("images", [])}
            turn["images"] = [img for img in state.get("images", []) if img["path"] not in previous]
        if state == {"turns": []}:
            return {}
        if state and state != load(project_id):
            save(project_id, state)
        return state


def prepare(project_id, log, fresh=False, message=None, images=None, model=""):
    with LOCK:
        state = inspect(project_id, log)
        if fresh:
            if state.get("turns"):
                archive = folder(project_id) / f"conversation-{time.time_ns()}.json"
                archive.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            state = {"log_stamp": state.get("log_stamp"), "turns": []}
        thread_id = state.get("thread_id", "")
        state.update(reply="", error="", activities=[], status="working", active_turn=message is not None)
        if message is not None:
            state.setdefault("turns", []).append({"id": str(time.time_ns()), "user": message,
                "attachments": images or [], "model": model, "status": "working", "reply": "", "images": []})
        save(project_id, state)
        return thread_id
