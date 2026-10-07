"""Offline integration test using the installed official DSH runtime and a local API fixture.

Run with the backend venv: python deepseek_test.py. No vendor API calls/keys.
"""
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_temp = tempfile.TemporaryDirectory(prefix="studio-deepseek-test-")
os.environ["ASSET_STUDIO_DATA"] = _temp.name
os.environ["DEEPSEEK_API_KEY"] = "local-fixture-only"
os.environ["DSH_TELEMETRY_DISABLED"] = "1"

from asset_studio import deepseek_session as dsh, mission, cc_session
from asset_studio.routers.mission import sending, cancel_send

requests = []
# Every POST, parseable or not: (path, content-type, bytes, note). `requests` keeps its old
# meaning — parsed model payloads only — because the tests count it. A body this double cannot
# parse is ANSWERED rather than raised on: an unanswered request makes the runtime retry, and a
# retry storm reads as a product bug when it is only a test double that fell over. It is recorded
# here so a test can see it happened instead of finding a traceback in the log.
posts = []
slow_started = threading.Event()
slow_release = threading.Event()


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _stream(self, blocks):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            self.wfile.write(("".join(f"data: {json.dumps(b)}\n\n" for b in blocks) + "data: [DONE]\n\n").encode())
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        # THE FILES API, and it is not optional: an image travels TWICE. The runtime uploads it here
        # to obtain a durable `file_id`, and the chat request also carries it inline as a base64 data
        # URL. This body is multipart, so it is answered without being parsed — and recorded, so a
        # test can prove the upload happened instead of finding a traceback in the log.
        if self.path.rstrip("/").endswith("/files"):
            posts.append((self.path, ctype, length, "files-api"))
            payload = json.dumps({"id": "file-api-fixture", "object": "file", "bytes": length,
                                  "filename": "reference.png"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return
        try:
            body = json.loads(raw)
        except Exception as exc:                      # noqa: BLE001 — recorded, then answered
            posts.append((self.path, ctype, length, f"{type(exc).__name__}@{getattr(exc, 'start', '?')}"))
            self._stream([
                {"id": "fixture", "object": "chat.completion.chunk", "model": "fixture", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Fixture reply"}, "finish_reason": None}]},
                {"id": "fixture", "object": "chat.completion.chunk", "model": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            ])
            return
        posts.append((self.path, ctype, length, "ok"))
        requests.append(body)
        last = body["messages"][-1]
        if any(m.get("role") == "user" and "slow fixture" in json.dumps(m.get("content")) for m in body["messages"]):
            slow_started.set()
            slow_release.wait(10)
        blocks = [
            {"id": "fixture", "object": "chat.completion.chunk", "model": body.get("model"), "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Fixture reply"}, "finish_reason": None}]},
            {"id": "fixture", "object": "chat.completion.chunk", "model": body.get("model"), "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
        ]
        if any(m.get("role") == "user" and "cache fixture" in json.dumps(m.get("content")) for m in body["messages"]):
            # DeepSeek's own wire shape for a warm prompt: the hits ride `prompt_cache_hit_tokens`.
            blocks[-1]["usage"] = {"prompt_tokens": 1000, "completion_tokens": 2, "total_tokens": 1002,
                                   "prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": 100}
        if any(m.get("role") == "user" and "tool fixture" in json.dumps(m.get("content")) for m in body["messages"]) and not any(m.get("role") == "tool" for m in body["messages"]):
            blocks = [
                {"id": "fixture", "object": "chat.completion.chunk", "model": body.get("model"), "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{"index": 0, "id": "fixture-write", "type": "function", "function": {"name": "write", "arguments": json.dumps({"file_path": str(Path(_temp.name) / "workspace" / "native-tool.txt"), "content": "Native DeepSeek tool executed"})}}]}, "finish_reason": None}]},
                {"id": "fixture", "object": "chat.completion.chunk", "model": body.get("model"), "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            ]
        self._stream(blocks)


class DeepSeekIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        os.environ["DEEPSEEK_BASE_URL"] = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        dsh.shutdown()
        cls.server.shutdown()

    def test_01_native_runtime_resume_feed_and_fresh_conversation(self):
        pid = "d--fixture-workspace"
        workspace = Path(_temp.name) / "workspace"
        workspace.mkdir(exist_ok=True)
        for n in range(2):
            result = dsh.send(pid, f"prompt {n}", path=str(workspace), permission_mode="full")
            self.assertTrue(result["ok"], result)
            deadline = time.time() + 35
            while dsh.is_sending(pid) and time.time() < deadline:
                time.sleep(.1)
            self.assertFalse(dsh.is_sending(pid), "runtime did not settle")
            feed = mission.project_feed(dsh.PREFIX + pid)
            self.assertTrue(any(x.get("text") == "Fixture reply" for x in feed["lines"]), feed)
            if n == 0:
                sid = result["session"]
            else:
                self.assertEqual(sid, result["session"])
        self.assertEqual(2, len(requests), mission.project_feed(dsh.PREFIX + pid))
        self.assertTrue(any("prompt 0" in json.dumps(m) for m in requests[1]["messages"]), "history lost across runtime restart")
        self.assertEqual(1, len(mission.list_sessions(dsh.PREFIX + pid)))
        result = dsh.send(pid, "fresh", path=str(workspace), permission_mode="full", new_session=True)
        self.assertNotEqual(sid, result["session"])
        deadline = time.time() + 35
        while dsh.is_sending(pid) and time.time() < deadline:
            time.sleep(.1)
        self.assertEqual(2, len(mission.list_sessions(dsh.PREFIX + pid)))
        self.assertFalse(any("prompt 0" in json.dumps(m) for m in requests[-1]["messages"]))
        # A restarted SDK process uses a new internal id and receives explicit
        # restored history while the Studio conversation id remains stable.
        dsh.shutdown()
        result = dsh.send(pid, "after restart", path=str(workspace), permission_mode="full", session=sid)
        self.assertEqual(sid, result["session"])
        deadline = time.time() + 35
        while dsh.is_sending(pid) and time.time() < deadline:
            time.sleep(.1)
        self.assertTrue(any("prompt 0" in json.dumps(m) for m in requests[-1]["messages"]))

    def test_02_validation_does_not_launch_runtime(self):
        # `images` here points at a file that does not exist, so this still covers "a bad send never
        # starts a runtime" — the missing-file refusal happens before anything spawns. The real
        # attachment behaviour (which rows can see, and what reaches the wire) is test_06.
        for args in ({"permission_mode": "plan"}, {"model": "invented", "permission_mode": "full"},
                     {"session": "../bad", "permission_mode": "full"},
                     {"images": [str(Path(_temp.name) / "missing.png")], "permission_mode": "full"}):
            result = dsh.send("d--fixture-workspace", "test", path=_temp.name, **args)
            self.assertFalse(result["ok"], result)
        self.assertFalse(dsh.send("../bad", "test", permission_mode="full")["ok"])

    def test_03_cancel_and_busy_status_are_engine_specific(self):
        from unittest.mock import patch
        import importlib.util
        if importlib.util.find_spec("asset_studio.codex_app"):
            with patch("asset_studio.codex_app.is_sending", return_value=True), patch("asset_studio.cc_session._claude_is_sending", return_value=False):
                self.assertFalse(sending("d--fixture-workspace")["sending"])
                self.assertTrue(sending("codex--d--fixture-workspace")["sending"])
        with patch("asset_studio.cc_session.cancel") as claude_cancel, patch.object(dsh, "cancel", return_value={"ok": True}) as dsh_cancel:
            cancel_send("d--fixture-workspace", agent=dsh.AGENT)
            dsh_cancel.assert_called_once()
            claude_cancel.assert_not_called()

    def test_04_native_tool_execution_and_pro_model(self):
        workspace = Path(_temp.name) / "workspace"
        workspace.mkdir(exist_ok=True)
        result = dsh.send("d--tool-fixture", "tool fixture", path=str(workspace), permission_mode="full", model="deepseek-v4-pro")
        self.assertTrue(result["ok"], result)
        deadline = time.time() + 35
        while dsh.is_sending("d--tool-fixture") and time.time() < deadline:
            time.sleep(.1)
        self.assertTrue((workspace / "native-tool.txt").exists(), mission.project_feed(dsh.PREFIX + "d--tool-fixture"))
        self.assertEqual("Native DeepSeek tool executed", (workspace / "native-tool.txt").read_text())
        feed = mission.project_feed(dsh.PREFIX + "d--tool-fixture")
        self.assertFalse(feed["working"])
        self.assertTrue(any(x.get("tool") == "Write" for x in feed["lines"]), feed)

    def test_05_cancel_actual_runtime_and_missing_key(self):
        from unittest.mock import patch
        with patch.object(dsh, "api_key", return_value=""):
            result = dsh.send("d--cancel-fixture", "test", path=_temp.name, permission_mode="full")
            self.assertFalse(result["ok"])
            self.assertIn("API key", result["error"])
        result = dsh.send("d--cancel-fixture", "slow fixture", path=_temp.name, permission_mode="full")
        self.assertTrue(result["ok"], result)
        self.assertTrue(slow_started.wait(10))
        self.assertTrue(dsh.is_sending("d--cancel-fixture"))
        busy = dsh.send("d--cancel-fixture", "duplicate", path=_temp.name, permission_mode="full")
        self.assertFalse(busy["ok"])
        self.assertTrue(dsh.cancel("d--cancel-fixture")["ok"])
        slow_release.set()
        deadline = time.time() + 10
        while dsh.is_sending("d--cancel-fixture") and time.time() < deadline:
            time.sleep(.1)
        self.assertFalse(dsh.is_sending("d--cancel-fixture"))


    def test_05b_the_picker_names_the_model_that_answers(self):
        """The label must say V4.1 Flash, because that IS what every one of these ids serves.

        A wrong label here is not cosmetic — it IS the bug this test was written for. `deepseek-flash`
        has been V4.1-Flash since 2026-09-10, but the Studio called that row "DeepSeek Flash", so the
        option read as an older, unversioned model and the report was "V4.1 Flash is missing from the
        model bar" while the row sat there the whole time. Nothing was broken except the name.

        So the NAME is pinned against the runtime's own catalogue rather than against a string
        somebody remembered: the row's `name` is read out of the installed runtime binary, which is
        what actually decides which model answers. If a later runtime renames or retires the row,
        this fails and says to go re-read the catalogue — the same "read out of the binary, not
        recalled" rule the Claude model list already follows.
        """
        import re
        # Every row the picker can send is labelled, and every label names the model that answers.
        self.assertEqual(set(dsh.MODEL_LABELS), set(dsh.MODELS),
                         "a row the picker can send has no label")
        for m in dsh.MODELS:
            if m == "deepseek-v4-pro":
                # Its own model (DeepSeek-V4-Pro-0813) at its own price — not a Flash alias.
                self.assertEqual(dsh.MODEL_LABELS[m], "DeepSeek V4 Pro")
                continue
            self.assertIn("V4.1 Flash", dsh.MODEL_LABELS[m],
                          f"{m} is served by V4.1 Flash; its label must say so, not just 'Flash'")
        # The default row is what is sent when nobody opens the menu, so it is the one that has to be
        # both correctly named AND able to see.
        self.assertEqual(dsh.MODEL_LABELS[dsh.MODELS[0]], "DeepSeek V4.1 Flash")
        self.assertIn(dsh.MODELS[0], dsh.VISION_MODELS)

        exe = dsh.installed()
        if not exe:
            self.skipTest("DeepSeek runtime is not installed, so its catalogue cannot be read")
        with open(exe, "rb") as fh:
            blob = fh.read()
        # The catalogue as the runtime declares it, not as any one row's entry: find the table, then
        # read every (id, name) pair out of it, so a renamed row is caught wherever it moved to.
        at = blob.find(b"const DEFAULT_MODELS = [")
        self.assertGreater(at, 0, "the runtime's model catalogue moved; re-read it before trusting "
                                  "any label in this module")
        declared = {i.decode(): n.decode() for i, n in
                    re.findall(rb'id: "([^"]+)",\s*name: "([^"]+)"', blob[at:at + 3000])}
        self.assertIn("deepseek-flash", declared, "the runtime no longer declares this row")
        self.assertEqual(declared["deepseek-flash"], "DeepSeek-V41-Flash",
                         "the runtime now calls `deepseek-flash` %r — every label in this module is "
                         "stale" % declared["deepseek-flash"])

    def test_06_image_attachment_reaches_the_model(self):
        """An attached picture must reach the WIRE, and the transcript must not carry its bytes.

        The fixture records every request body, so this is the one place the whole image path can be
        proved without a vendor call: our block is turned by the runtime into the provider's own
        image part, and that part is what the fixture sees. Which rows CAN see is the runtime's
        `inputModalities`; a text-only row must refuse before it spawns anything, because otherwise
        the runtime silently swaps the picture for a placeholder and the agent spends the turn
        guessing at pixels instead of looking at them.
        """
        import base64
        # A real 160x90 PNG — the same bytes the runtime's own fixture uses, so the runtime's
        # attachment normaliser (which reads dimensions) accepts it.
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAKAAAABaCAYAAAA/xl1SAAAAvklEQVR42u3SMQ0AAAjAMIyhELM4AAe8PD1q"
            "YFlk9cCXEAEDYkAwIAYEA2JAMCAGBANiQDAgBgQDYkAwIAYEA2JAMCAGBANiQDAgBgQDYkAwIAYEA2JAMCAG"
            "xIBCYEAMCAbEgGBADAgGxIBgQAwIBsSAYEAMCAbEgGBADAgGxIBgQAwIBsSAYEAMCAbEgGBADAgGxIAYEAyI"
            "AcGAGBAMiAHBgBgQDIgBwYAYEAyIAcGAGBAMiAHBgBgQDIgB4bYWLb6pnOb1xAAAAABJRU5ErkJggg==")
        workspace = Path(_temp.name) / "workspace"
        workspace.mkdir(exist_ok=True)
        shot = workspace / "reference.png"
        shot.write_bytes(png)

        # The rows that can see are the runtime's declaration, not a guess: `deepseek-flash` and its
        # legacy vision name. Both V4 rows are text only, and the picker now says so.
        self.assertIn("deepseek-flash", dsh.VISION_MODELS)
        self.assertIn("deepseek-v4-flash-vision-exp", dsh.VISION_MODELS)
        self.assertNotIn("deepseek-v4-flash", dsh.VISION_MODELS)
        self.assertNotIn("deepseek-v4-pro", dsh.VISION_MODELS)
        # `default` is what the picker sends, and it has to be a row that can see — otherwise the
        # paperclip works for nobody who never opened the model menu.
        self.assertIn(dsh.MODELS[0], dsh.VISION_MODELS)

        # A text-only row: refused BEFORE anything launches, and the message names the fix.
        before = len(requests)
        refused = dsh.send("d--image-fixture", "look at this", path=str(workspace),
                           permission_mode="full", model="deepseek-v4-pro", images=[str(shot)])
        self.assertFalse(refused["ok"], refused)
        self.assertIn("DeepSeek V4.1 Flash", refused["error"])
        self.assertEqual(before, len(requests), "a refused image must not reach the provider")

        # An attached image with no text at all is still a message — "look at this" is implied.
        self.assertFalse(dsh.send("d--image-fixture", "", path=str(workspace),
                                  permission_mode="full", images=[],
                                  model="deepseek-v4-pro")["ok"],
                         "an empty message with no image is still an empty message")

        # The real send, on the default row.
        result = dsh.send("d--image-fixture", "what is in this picture?",
                          path=str(workspace), permission_mode="full",
                          model="default", images=[str(shot)], new_session=True)
        self.assertTrue(result["ok"], result)
        deadline = time.time() + 35
        while dsh.is_sending("d--image-fixture") and time.time() < deadline:
            time.sleep(.1)
        self.assertFalse(dsh.is_sending("d--image-fixture"), "runtime did not settle")

        # IT REACHED THE WIRE: the provider's own image part, carrying the bytes as a data URL.
        # The payload is NOT compared with our own base64: the runtime normalises an attachment
        # (it re-encodes and records a sha256), so the bytes on the wire may be a re-encode. What
        # must hold is that an image part with real data got there at all.
        wire = json.dumps(requests[-1])
        self.assertIn("image_url", wire, "the image part never reached the provider")
        self.assertIn("data:image/png;base64,", wire)
        payload = wire.split("data:image/png;base64,", 1)[1].split('"', 1)[0]
        self.assertGreater(len(payload), len(base64.b64encode(png).decode()) // 2,
                           "the image part carried a placeholder, not a picture")

        # AND THE TRANSCRIPT KEEPS A PATH, NOT A PAYLOAD. A base64 image in the .jsonl would be
        # re-read by every feed poll and every context read, for a picture the pane can draw from
        # its own file.
        transcript = dsh.HOME / "projects" / "d--image-fixture" / (result["session"] + ".jsonl")
        raw = transcript.read_text(encoding="utf-8")
        self.assertIn("reference.png", raw, "the transcript lost which file was attached")
        self.assertNotIn("iVBORw0KGgo", raw, "the transcript stored the image bytes")

        # The upload half of the same trip, and the reason this double no longer falls over on a
        # multipart body: the runtime also POSTs the image to the Files API to mint a `file_id`.
        self.assertTrue(any(note == "files-api" for _p, _c, _n, note in posts),
                        "the image was never uploaded for a durable reference")


class RuntimeLifecycle(unittest.TestCase):
    """The runtime is a process of 100-185 MB. These are the ways one used to be left behind, or
    kept when it had to go."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        os.environ["DEEPSEEK_BASE_URL"] = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.ws = Path(_temp.name) / "lifecycle"
        cls.ws.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        dsh.shutdown()
        cls.server.shutdown()

    def _turn(self, pid, text, **kw):
        result = dsh.send(pid, text, path=str(self.ws), permission_mode="full", **kw)
        self.assertTrue(result["ok"], result)
        deadline = time.time() + 35
        while dsh.is_sending(pid) and time.time() < deadline:
            time.sleep(.1)
        self.assertFalse(dsh.is_sending(pid), "runtime did not settle")
        return result

    def _keys(self, pid):
        with dsh._lock:
            return {k for k in dsh._runtimes if k[0] == pid}

    def test_a_new_conversation_closes_the_old_runtime(self):
        pid = "d--lifecycle-new"
        first = self._turn(pid, "one")
        old = dsh._runtimes[(pid, first["session"])]["harness"]
        second = self._turn(pid, "two", new_session=True)
        self.assertEqual({(pid, second["session"])}, self._keys(pid),
                         "the previous conversation's runtime is still held")
        proc = getattr(old.client, "_proc", None)
        if proc is not None:
            deadline = time.time() + 10
            while proc.poll() is None and time.time() < deadline:
                time.sleep(.1)
            self.assertIsNotNone(proc.poll(), "the old runtime process is still running")

    def test_reset_closes_an_idle_runtime_so_an_edit_is_forgotten(self):
        pid = "d--lifecycle-reset"
        r = self._turn(pid, "remember the word pelican")
        self.assertTrue(self._keys(pid))
        # cancel() alone leaves an idle runtime — and the model's memory — in place
        dsh.cancel(pid)
        self.assertTrue(self._keys(pid), "precondition: cancel is a no-op while idle")
        self.assertEqual(1, dsh.reset(pid)["closed"])
        self.assertFalse(self._keys(pid))
        n = len(requests)
        self._turn(pid, "after the edit", session=r["session"])
        first_user = next(m for m in requests[n]["messages"] if m.get("role") == "user")
        self.assertIn("Previous conversation restored", json.dumps(first_user.get("content")),
                      "the next send must rebuild from the transcript, not reuse the old memory")

    def test_the_idle_reaper_closes_quiet_runtimes_only(self):
        pid = "d--lifecycle-idle"
        self._turn(pid, "idle one")
        self.assertEqual(0, dsh.reap_idle(now=time.time() + 60), "closed a runtime that just worked")
        with dsh._lock:
            dsh._live[pid]["working"] = True          # pretend a turn is running
        try:
            dsh.reap_idle(now=time.time() + 10 * 3600)   # other tests' idle runtimes may go
            self.assertTrue(self._keys(pid), "closed a runtime whose project has a turn running")
        finally:
            with dsh._lock:
                dsh._live[pid]["working"] = False
        self.assertGreaterEqual(dsh.reap_idle(now=time.time() + 10 * 3600), 1)
        self.assertFalse(self._keys(pid))

    def test_cache_hits_are_read_and_the_turn_is_priced(self):
        from asset_studio import turns
        pid = "d--lifecycle-cache"
        self._turn(pid, "cache fixture")
        transcript = next((dsh.HOME / "projects" / pid).glob("*.jsonl"))
        usages = [json.loads(line)["message"].get("usage") for line in
                  transcript.read_text(encoding="utf-8").splitlines()
                  if '"usage"' in line]
        self.assertTrue(any(u and u.get("cache_read_input_tokens") == 900 for u in usages), usages)
        self.assertTrue(any(u and u.get("input_tokens") == 100 for u in usages), usages)
        deadline = time.time() + 5                    # banked at turn/end, on the runtime's thread
        banked = []
        while not banked and time.time() < deadline:
            banked = turns.for_project(dsh.PREFIX + pid)
            time.sleep(.1)
        self.assertTrue(banked, "the turn was not banked")
        self.assertGreater(banked[-1]["cost"], 0, "a DeepSeek turn must not be banked as free")
        self.assertEqual("list", banked[-1].get("basis"))

    def test_a_new_runtime_is_told_how_to_reach_the_code_graph(self):
        from asset_studio.config import settings
        settings.update({"cc_graphify": True})
        pid = "d--lifecycle-graph"
        n = len(requests)
        self._turn(pid, "graph please")
        first_user = next(m for m in requests[n]["messages"] if m.get("role") == "user")
        self.assertIn("/api/graphify/query", json.dumps(first_user.get("content")))
        n = len(requests)
        self._turn(pid, "second turn")
        last_user = [m for m in requests[n]["messages"] if m.get("role") == "user"][-1]
        self.assertNotIn("/api/graphify/query", json.dumps(last_user.get("content")),
                         "the note is paid for once per runtime, not once per turn")


class _Note:
    def __init__(self, method, payload):
        self.method, self.payload = method, payload


class StallWatchTest(unittest.TestCase):
    """A turn that answers NOTHING must say so instead of spinning forever.

    The failure this covers, measured on this PC on 2026-10-06: the send returned 200, the user's
    message sat in the transcript, and then the runtime produced no assistant message, no tool call
    and no error for FIFTEEN MINUTES — nothing to read, and not even a row in data/turns.jsonl. The
    pane simply kept spinning, so the only thing left to try was a backend restart, which is what
    lost the live conversation. The watch does not kill the turn (a slow call and a stuck one look
    alike from here); it replaces the activity line with what is actually true.
    """

    def _state(self, seen=0, **extra):
        return {"working": True, "harness": object(), "seen": seen, "sent": time.time(),
                "activity": "Thinking", "model": "deepseek-flash", "session": "s1",
                "sdk_session": "s1",
                "cwd": str(dsh.HOME), "transcript": dsh.HOME / "stall.jsonl", **extra}

    def test_a_silent_turn_is_labelled_and_never_killed(self):
        old = (dsh._STALL_WARN_S, dsh._STALL_POLL_S)
        dsh._STALL_WARN_S, dsh._STALL_POLL_S = 0.0, 0.02
        try:
            state = self._state()
            dsh._start_stall_watch(state)
            deadline = time.time() + 3
            while state["activity"] == "Thinking" and time.time() < deadline:
                time.sleep(0.02)
            self.assertIn("No output from the harness", state["activity"])
            self.assertIn("Stop", state["activity"], "it must say what to do about it")
            self.assertTrue(state["working"], "the watch must never kill a turn by itself")
        finally:
            dsh._STALL_WARN_S, dsh._STALL_POLL_S = old

    def test_a_turn_that_has_spoken_is_never_relabelled(self):
        old = (dsh._STALL_WARN_S, dsh._STALL_POLL_S)
        dsh._STALL_WARN_S, dsh._STALL_POLL_S = 0.0, 0.02
        try:
            state = self._state(seen=1)          # it answered something at least once
            dsh._start_stall_watch(state)
            time.sleep(0.2)
            self.assertEqual(state["activity"], "Thinking")
        finally:
            dsh._STALL_WARN_S, dsh._STALL_POLL_S = old

    def test_any_notification_counts_as_alive(self):
        state = self._state()
        dsh._notification(state, _Note("session.event", {
            "sessionId": "s1", "event": {"type": "tool/call", "data": {"name": "Read", "callId": "c1",
                                                                      "arguments": {"file": "a.py"}}}}))
        self.assertGreaterEqual(state["seen"], 1, "a tool call must count as output")
        self.assertGreater(state["last_note"], 0, "the watch needs the time of the last word")
        self.assertEqual(state["activity"], "Running Read")
        dsh.HOME.mkdir(parents=True, exist_ok=True)
        (dsh.HOME / "stall.jsonl").unlink(missing_ok=True)

    def test_a_notification_for_another_session_is_ignored(self):
        state = self._state()
        dsh._notification(state, _Note("session.event", {
            "sessionId": "someone-else", "event": {"type": "tool/call", "data": {"name": "Read"}}}))
        self.assertEqual(state["seen"], 0, "another conversation's event is not this turn's output")


if __name__ == "__main__":
    unittest.main(verbosity=2)
