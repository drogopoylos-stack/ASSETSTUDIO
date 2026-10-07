"""Video workflow and queue contract tests, without paid inference."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from asset_studio.models import Job, StageType
from asset_studio.providers.base import JobContext
from asset_studio.providers.comfy_common import comfy_memory, free_comfy
from asset_studio.providers.image.comfy_workflow import ComfyWorkflowImageProvider
from asset_studio.providers.image.comfyui import ComfyUIImageProvider
from asset_studio.providers.texture.trellis_texture import TrellisTextureProvider
from asset_studio.providers.threed.comfy_workflow import ComfyWorkflow3DProvider
from asset_studio.providers.video.common import RATIOS, dimensions, video_options, video_refs
from asset_studio.providers.video.minimax_h3 import MiniMaxH3Provider, build_graph, frame_count, wait_video
from asset_studio.providers.video.minimax_h3_max import MiniMaxH3MaxProvider, queue_url


def context(folder, params=None, canceled=lambda: False):
    return JobContext(Job(stage=StageType.video, provider_id="minimax-h3-max", params=params or {"prompt": "A waving tree", "seed": 7}),
                      [], [], Path(folder), {}, lambda event: None, canceled)


class VideoTests(unittest.TestCase):
    def test_canvases_stay_within_native_area_cap(self):
        self.assertEqual(dimensions("16:9", "768P"), (1344, 768))
        self.assertEqual(dimensions("9:16", "768P"), (768, 1344))
        for ratio in RATIOS:
            for resolution in ("480P", "768P"):
                width, height = dimensions(ratio, resolution)
                self.assertEqual(width % 32, 0)
                self.assertEqual(height % 32, 0)
                self.assertLessEqual(width * height, 1344 * 768)

    def test_native_audio_graph_and_frame_grid(self):
        for duration in (5, 8, 15):
            graph = build_graph("Scene", duration, "16:9", "768P", 7)
            self.assertEqual(frame_count(duration) % 17, 5)
            self.assertGreaterEqual(frame_count(duration), duration * 24)
            self.assertEqual(graph["13"]["inputs"]["audio"], ["12", 0])
            self.assertEqual(graph["11"]["inputs"]["samples"], graph["12"]["inputs"]["samples"])
            self.assertEqual(graph["14"]["inputs"]["format.codec"], "h264")
        image = build_graph("Scene", 5, "9:16", "480P", 7, turbo=True, image="input.png")
        self.assertEqual(image["5"]["inputs"]["first_frame"], ["15", 0])
        self.assertEqual(image["8"]["inputs"]["steps"], 8)
        self.assertEqual(image["9"]["inputs"]["model"], ["16", 0])
        self.assertEqual(image["5"]["inputs"]["width"], 480)

    def test_invalid_prompt_and_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            for params in ({"prompt": " "}, {"prompt": "x", "duration": float("nan")},
                           {"prompt": "x", "duration": 16}, {"prompt": "x", "aspect_ratio": "invalid"}):
                with self.assertRaises(ValueError):
                    video_options(context(folder, params))

    def test_outputs_and_comfy_failure(self):
        entry = {"outputs": {"14": {"images": [{"filename": "clip.mp4", "subfolder": "video"}, {"filename": "preview.png"}]}}}
        self.assertEqual(video_refs(entry), [{"filename": "clip.mp4", "subfolder": "video", "type": "output"}])
        failure = {"p": {"status": {"status_str": "error", "messages": [["execution_error", {"exception_message": "out of memory"}]]}}}
        with tempfile.TemporaryDirectory() as folder, httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=failure))) as http:
            with self.assertRaisesRegex(RuntimeError, "out of memory"):
                wait_video(http, "http://localhost", "p", context(folder))

    def test_cancel_does_not_interrupt_another_prompt(self):
        calls = []
        def handle(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={"queue_running": [[0, "someone-else"]]})
        with tempfile.TemporaryDirectory() as folder, httpx.Client(transport=httpx.MockTransport(handle)) as http:
            with self.assertRaisesRegex(RuntimeError, "canceled"):
                wait_video(http, "http://localhost", "p", context(folder, canceled=lambda: True))
        self.assertEqual(calls, ["/queue", "/queue"])

    def test_fal_key_only_sent_to_queue_and_video_saved(self):
        requests = []
        def handle(request):
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(200, json={"request_id": "r", "status_url": "https://queue.fal.run/status",
                    "response_url": "https://queue.fal.run/result", "cancel_url": "https://queue.fal.run/cancel"})
            if request.url.path == "/status":
                return httpx.Response(200, json={"status": "COMPLETED"})
            if request.url.path == "/result":
                return httpx.Response(200, json={"video": {"url": "https://cdn.example.test/video.mp4"}})
            return httpx.Response(200, content=b"video-bytes")
        client = httpx.Client(transport=httpx.MockTransport(handle))
        with tempfile.TemporaryDirectory() as folder, patch("asset_studio.providers.video.minimax_h3_max.keychain.get_key", return_value="test-key"), patch("asset_studio.providers.video.minimax_h3_max.httpx.Client", return_value=client):
            assets = MiniMaxH3MaxProvider().run(context(folder))
            self.assertEqual(assets[0].type.value, "video")
            self.assertEqual(Path(assets[0].path).read_bytes(), b"video-bytes")
            self.assertNotIn("test-key", str(assets[0]))
        self.assertEqual(str(requests[0].url), "https://queue.fal.run/minimax/h3-max/text-to-video")
        for request in requests:
            self.assertEqual(request.headers.get("Authorization"), "Key test-key" if request.url.host == "queue.fal.run" else None)

    def test_fal_rejects_foreign_queue_url(self):
        for url in ("http://queue.fal.run/status", "https://attacker.test/status", "https://queue.fal.run.attacker.test"):
            with self.assertRaises(RuntimeError):
                queue_url(url)


# --- the memory a finished video leaves behind ------------------------------------------------
#
# THE REPORT: "after a video generation RAM and VRAM stayed high". ComfyUI holds the video model
# and its 32B text encoder in ITS OWN process, so `gpu_memory.free_now()`'s `torch.cuda.empty_cache()`
# cannot free one byte of it, and until this change NO ComfyUI-backed provider implemented
# `unload()` — so the idle reaper walked past every one of them. Measured 2026-10-05 with ComfyUI's
# queue empty, right after one MiniMax H3 clip: 13.8 GB of 16 GB VRAM in use and 30.8 GB of private
# memory; one POST to its own /free returned the card to 0.9 GB and the process to 4.2 GB.
#
# These pin both halves offline: what ComfyUI is asked to do, and WHICH providers ask it.

V16 = 16_000_000_000
_REAL_CLIENT = httpx.Client


def _stats(vram_free, ram_free=1_000_000_000, total=V16):
    return {"devices": [{"vram_total": total, "vram_free": vram_free}],
            "system": {"ram_free": ram_free}}


def _offline(handler):
    """Patch the two entry points `comfy_common` actually calls onto one offline transport.

    `free_comfy` imports httpx INSIDE the function, so patching `comfy_common.httpx` would miss —
    what it reaches for is the module-level attribute on the httpx module itself. The originals are
    captured before patching, or the replacement would call itself.
    """
    transport = httpx.MockTransport(handler)
    return (patch("httpx.get", lambda url, **kw: _REAL_CLIENT(transport=transport).get(url, **kw)),
            patch("httpx.Client", lambda *a, **kw: _REAL_CLIENT(transport=transport)))


class ComfyReleaseTests(unittest.TestCase):
    def release(self, *, vram_free, running=(), force=False):
        """Run free_comfy against a fake ComfyUI; return (calls, posted bodies, result)."""
        calls: list[tuple[str, str]] = []
        bodies: list[dict] = []

        def handle(request):
            calls.append((request.method, request.url.path))
            if request.url.path == "/system_stats":
                # The card is full before the POST and empty after, as the real one measured.
                after = any(m == "POST" for m, _ in calls)
                return httpx.Response(200, json=_stats(15_500_000_000 if after else vram_free))
            if request.url.path == "/queue":
                return httpx.Response(200, json={"queue_running": list(running), "queue_pending": []})
            if request.url.path == "/free":
                bodies.append(json.loads(request.content or "{}"))
                return httpx.Response(200, json={})
            return httpx.Response(404)

        get, client = _offline(handle)
        with get, client:
            result = free_comfy("http://comfy.test", force=force)
        return calls, bodies, result

    def test_a_loaded_comfyui_is_asked_to_unload_and_reports_the_before_after(self):
        calls, bodies, result = self.release(vram_free=1_600_000_000)
        self.assertEqual([p for m, p in calls if m == "POST"], ["/free"])
        self.assertTrue(result["ok"] and result["freed"], result)
        self.assertEqual(result["before"]["vram_free"], 1_600_000_000)
        self.assertEqual(result["after"]["vram_free"], 15_500_000_000)

    def test_the_request_is_comfyuis_own_unload_models_and_free_memory(self):
        _calls, bodies, _result = self.release(vram_free=1_600_000_000)
        self.assertEqual(bodies, [{"unload_models": True, "free_memory": True}])

    def test_a_running_prompt_is_left_alone_even_when_forced(self):
        # force is for "the card LOOKS empty"; it must never yank a model out of a live sampler.
        for force in (False, True):
            calls, bodies, result = self.release(vram_free=1_600_000_000, running=[[0, "someone-else"]], force=force)
            self.assertEqual(bodies, [], f"force={force} posted /free over a running prompt")
            self.assertEqual([p for m, p in calls if m == "POST"], [])
            self.assertEqual(result["skipped"], "ComfyUI is running a prompt")

    def test_an_already_empty_comfyui_is_not_posted_to_unless_forced(self):
        _calls, bodies, result = self.release(vram_free=15_500_000_000)
        self.assertEqual(bodies, [])
        self.assertEqual(result["skipped"], "ComfyUI holds nothing")
        _calls, bodies, result = self.release(vram_free=15_500_000_000, force=True)
        self.assertEqual(bodies, [{"unload_models": True, "free_memory": True}])

    def test_an_unreachable_comfyui_is_not_an_error(self):
        def handle(request):
            return httpx.Response(500)

        get, client = _offline(handle)
        with get, client:
            result = free_comfy("http://comfy.test")
        self.assertFalse(result["ok"])
        self.assertIn("not reachable", result["error"])

    def test_comfy_memory_reads_the_device_and_the_host(self):
        def handle(request):
            return httpx.Response(200, json=_stats(4_000_000_000, ram_free=9_000_000_000))

        get, client = _offline(handle)
        with get, client:
            seen = comfy_memory("http://comfy.test")
        self.assertEqual(seen, {"vram_total": V16, "vram_free": 4_000_000_000, "ram_free": 9_000_000_000})

    def test_every_comfyui_backed_provider_hands_the_memory_back(self):
        """The bug was silence: these classes inherited a no-op `unload()`, so the idle reaper and
        the Free-now button did nothing for the family that holds the most memory."""
        for provider in (MiniMaxH3Provider(), ComfyUIImageProvider(), ComfyWorkflowImageProvider(),
                         ComfyWorkflow3DProvider(), TrellisTextureProvider()):
            calls, _bodies, _result = [], [], None

            def handle(request, calls=calls):
                calls.append((request.method, request.url.path))
                if request.url.path == "/system_stats":
                    return httpx.Response(200, json=_stats(1_600_000_000))
                if request.url.path == "/queue":
                    return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
                return httpx.Response(200, json={})

            get, client = _offline(handle)
            with get, client:
                provider.unload()
            self.assertIn(("POST", "/free"), calls, f"{type(provider).__name__}.unload() freed nothing")

    def test_the_reaper_asks_comfyui_even_when_nothing_in_this_process_ran(self):
        """A ComfyUI left loaded by a PREVIOUS Studio session is invisible to `_dirty`."""
        from asset_studio import gpu_memory

        calls: list[str] = []
        self.addCleanup(self._reset_reaper)
        gpu_memory._dirty, gpu_memory._busy, gpu_memory._last_activity = False, 0, 0.0
        gpu_memory._comfy_probe_at, gpu_memory._comfy_probed_at_startup = 0.0, False
        with patch("asset_studio.providers.comfy_common.free_comfy", lambda *a, **k: calls.append("comfy")), \
             patch.object(gpu_memory, "free_now", lambda *a, **k: calls.append("torch")), \
             patch.object(gpu_memory, "_idle_seconds", lambda: 90.0):
            gpu_memory._idle_pass(1000.0)          # 1000s in, nothing has run here
            gpu_memory._idle_pass(1100.0)          # and a later pass must not ask again
        self.assertEqual(calls, ["comfy"])

    def test_the_reaper_frees_both_halves_after_a_job(self):
        from asset_studio import gpu_memory

        calls: list[str] = []
        self.addCleanup(self._reset_reaper)
        gpu_memory._dirty, gpu_memory._busy, gpu_memory._last_activity = False, 0, 0.0
        gpu_memory._comfy_probe_at, gpu_memory._comfy_probed_at_startup = 0.0, True
        gpu_memory.note_busy_start()
        gpu_memory.note_busy_end()
        idle_from = gpu_memory._last_activity
        with patch("asset_studio.providers.comfy_common.free_comfy", lambda *a, **k: calls.append("comfy")), \
             patch.object(gpu_memory, "free_now", lambda *a, **k: calls.append("torch")), \
             patch.object(gpu_memory, "_idle_seconds", lambda: 90.0):
            gpu_memory._idle_pass(idle_from + 30)   # still inside the idle window
            self.assertEqual(calls, [])
            gpu_memory._idle_pass(idle_from + 91)
        self.assertEqual(calls, ["comfy", "torch"])

    @staticmethod
    def _reset_reaper():
        # Leave the module exactly as the app expects it between tests: nothing dirty, and the
        # one-per-session startup sweep already spent, so no test's state leaks into another.
        from asset_studio import gpu_memory

        gpu_memory._dirty, gpu_memory._busy, gpu_memory._last_activity = False, 0, 0.0
        gpu_memory._comfy_probe_at, gpu_memory._comfy_probed_at_startup = 0.0, True


if __name__ == "__main__":
    unittest.main()
