# MiniMax video in Asset Studio

Open **Video** and select **Free / Local → MiniMax H3**. Write the scene,
motion, camera movement and desired sound in the prompt. An initial image is
optional. Choose duration, aspect ratio, resolution and seed, then Generate.
The result is saved in the Catalog with playback and download controls.

H3 uses native ComfyUI nodes and produces video plus audio. The local default
is 5 seconds, 480P and 20 steps. Turbo uses the installed 8-step LoRA, trading
some motion/audio quality for speed. Higher resolution and longer clips use
more GPU memory and take longer. Duration snaps to H3's frame grid at 24 fps.

Select **API → MiniMax H3 Max** to use fal's hosted model. Set a **fal** API
key in Settings → API keys. Requests incur fal charges; the Studio's cost
field does not represent the actual fal invoice. Keys stay in the backend's
keychain and are not attached to output-video downloads.

## Memory after a render — why it looked stuck, and what gives it back

ComfyUI keeps its model cache warm when a workflow finishes. That is deliberate — it is what makes
the NEXT render fast — but H3 is a big one (a convrot int8 UNET plus a 32B Qwen3-VL text encoder)
and ComfyUI runs in **its own process**. Until 2026-10-05 nothing in the Studio ever asked it to let
go: the idle reaper walked the provider list calling `unload()`, and not one ComfyUI-backed provider
implemented it, while `torch.cuda.empty_cache()` in this process cannot free a byte of another
process's memory. Measured here right after one 5-second clip, ComfyUI's queue already empty:

| | before | after one POST to ComfyUI's `/free` |
|---|---|---|
| VRAM in use | 13.8 GB of 16 | 0.9 GB |
| ComfyUI private memory | 30.8 GB | 4.2 GB |
| free host RAM | 5.8 GB | 21.0 GB |

The 32B encoder is why RAM stays high after a video, not just VRAM.

Three ways that memory now comes back:

- **By itself, 90 seconds after the last job** (`gpu_idle_release_seconds`). Every ComfyUI-backed
  provider — video, 2D, 3D, texture — hands the server's memory back through
  `providers/comfy_common.free_comfy`. The wait is deliberate: releasing immediately would reload
  the encoder for every step of a batch.
- **By clicking the VRAM or RAM reading** in the status bar. That calls `/api/system/free-gpu`,
  which frees this process's caches *and* ComfyUI's, even when the card already looks empty (a
  model can be offloaded to host RAM).
- **By starting the Studio.** A ComfyUI left loaded by a previous session is invisible to the new
  process, so the reaper runs one sweep shortly after startup and then leaves it alone.

A render that is still running is never disturbed: the release reads ComfyUI's own queue first and
waits, even when you click Free now.

## Installation on this PC

- ComfyUI: `D:\Asset Studio\data\vendor\ComfyUI`
- ComfyUI service: `http://127.0.0.1:8188`, started by the Studio
- Main diffusion weights: `C:\Users\Drog\AppData\Local\AssetStudio\models`
- Encoder, VAEs and optional Turbo LoRA: ComfyUI's `models` folder on D:
- `extra_model_paths.yaml` connects the model folder on C: to ComfyUI.

The main weights were downloaded again on C: after the previous D: I/O
failure. The invalid D: copy has a `.corrupt-20261003` suffix and is ignored.
The installer supports resumable downloads and verifies SHA-256 against
the official Hugging Face manifest before promoting a partial file.

Model terms are shown in the provider information. Local H3 uses the
MiniMax H3 Community License; H3 Max uses fal/MiniMax API terms.

## Verified on 2026-10-03

ComfyUI 0.38.0 with PyTorch 2.14.1+cu130 passed dependency checks. The
Studio-generated test job `3e2ebc511b2b` completed in 165.79 seconds on the
RTX 4070 Ti SUPER. Its saved MP4 contains 124 H.264 frames (864x480, 24 fps),
5.17 seconds of video and stereo AAC audio at 32 kHz. All frames and audio
were decoded successfully. The Studio serves byte ranges for playback and
the result appears on the Video page and in the Catalog.

The native 768P canvas correction is saved in the backend and takes effect
on its next restart. H3 Max still needs the user's fal key and was not
tested with paid inference.

Sources: [native ComfyUI workflows](https://docs.comfy.org/tutorials/video/minimax/minimax-h3-native),
[official weights](https://huggingface.co/Comfy-Org/MiniMax-H3),
[H3 Max API](https://fal.ai/models/minimax/h3-max/text-to-video/api).
