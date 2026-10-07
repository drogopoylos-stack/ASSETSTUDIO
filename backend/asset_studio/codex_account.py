"""Codex's supported stdio RPC: credentials stay in Codex's own auth store."""
from __future__ import annotations

import atexit
import json
import queue
import subprocess
import threading
import time

from . import agents


class Client:
    def __init__(self):
        exe = agents.detect("codex")
        if not exe:
            raise RuntimeError("Codex CLI is not installed. Install it in Coding agents first.")
        self.pending = {}
        self.lock = threading.Lock()
        self.events = queue.Queue()
        self.serial = 0
        self.process = subprocess.Popen(
            [exe, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.call("initialize", {"clientInfo": {"name": "asset_studio", "title": "Asset Studio", "version": "0.1.0"}})
            self.send({"method": "initialized"})
        except Exception:
            self.close()
            raise

    def send(self, message):
        with self.lock:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if "id" in message and "method" not in message:
                    with self.lock:
                        target = self.pending.get(message["id"])
                    if target is not None:
                        target.put(message)
                elif "id" in message:
                    # This integration never grants tool approvals.
                    self.send({"id": message["id"], "error": {"code": -32601, "message": "Unsupported client request"}})
                else:
                    self.events.put(message)
        finally:
            with self.lock:
                for target in self.pending.values():
                    target.put({"error": {"message": "Codex stopped. Please retry."}})

    def call(self, method, params=None, timeout=45):
        target = queue.Queue()
        with self.lock:
            self.serial += 1
            request_id = self.serial
            self.pending[request_id] = target
        try:
            self.send({"id": request_id, "method": method, "params": params or {}})
            try:
                response = target.get(timeout=timeout)
            except queue.Empty:
                raise RuntimeError(f"Codex timed out during {method}. Please retry.") from None
            if response.get("error"):
                raise RuntimeError(response["error"].get("message", "Codex request failed"))
            return response.get("result", {})
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def close(self):
        # EOF also reaches the real child when the CLI was started via an npm shim.
        try:
            self.process.stdin.close()
        except OSError:
            pass
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.kill()
                self.process.wait(timeout=5)


_client = None
_guard = threading.RLock()
_login = None


def client():
    global _client, _login
    with _guard:
        if _client is None or _client.process.poll() is not None:
            _client = Client()
            _login = None
        return _client


def close():
    if _client:
        _client.close()


atexit.register(close)


def status():
    global _login
    with _guard:
        c = client()
        while True:
            try:
                event = c.events.get_nowait()
            except queue.Empty:
                break
            if event.get("method") == "account/login/completed":
                completed = event.get("params", {})
                if _login and completed.get("loginId") == _login.get("loginId"):
                    _login = completed
        account = c.call("account/read", {"refreshToken": False}).get("account")
        return {"ok": True, "connected": bool(account), "account": account, "login": _login}


def login():
    global _login
    with _guard:
        c = client()
        if _login and _login.get("loginId") and "success" not in _login:
            return {"ok": True, **_login}
        _login = c.call("account/login/start", {"type": "chatgpt"})
        return {"ok": True, **_login}


def cancel_login():
    global _login
    with _guard:
        if _login and _login.get("loginId") and "success" not in _login:
            client().call("account/login/cancel", {"loginId": _login["loginId"]})
        _login = None
    return {"ok": True}


def models():
    c = client()
    rows, seen, cursors = [], set(), set()
    cursor = None
    while True:
        page = c.call("model/list", {"limit": 100, "cursor": cursor, "includeHidden": True})
        for row in page.get("data", []):
            model = row.get("model") or row.get("id")
            if model and model not in seen:
                rows.append({**row, "model": model})
                seen.add(model)
        next_cursor = page.get("nextCursor")
        if not next_cursor:
            break
        if next_cursor in cursors:
            raise RuntimeError("Codex returned an invalid model page. Please retry.")
        cursors.add(next_cursor)
        cursor = next_cursor
    return {"ok": True, "models": rows, "source": "codex", "access_verified": False}


def test_connection(model):
    c = Client()
    try:
        if not c.call("account/read").get("account"):
            raise RuntimeError("Sign in to Codex first.")
        params = {"sandbox": "read-only", "approvalPolicy": "never", "ephemeral": True,
                  "baseInstructions": "Reply with OK only. Do not use any tools."}
        if model and model != "default":
            params["model"] = model
        thread = c.call("thread/start", params)
        thread_id = thread["thread"]["id"]
        turn = c.call("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": "Reply OK to verify the connection."}]})
        turn_id = turn["turn"]["id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                event = c.events.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            p = event.get("params", {})
            if event.get("method") == "turn/completed" and p.get("turn", {}).get("id") == turn_id:
                finished = p["turn"]
                if finished.get("status") != "completed":
                    raise RuntimeError((finished.get("error") or {}).get("message") or "GPT did not complete the test.")
                return {"ok": True, "model": thread.get("model") or model, "message": "GPT completed the connection test."}
        c.call("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=5)
        raise RuntimeError("GPT connection test timed out.")
    finally:
        c.close()
