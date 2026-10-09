# MiniMax H3 Director in VIDEO

VIDEO embeds the native Muse MiniMax Director V1.4 timeline in the local ComfyUI.
The upstream repository is named MiniMaxH3-Director-V1.2; its current nodes are
Director V1.4 and Refine V2. The Studio starts its configured ComfyUI service when
VIDEO opens. The timeline has a separate browser autosave and can also be saved
with ComfyUI's normal workflow controls. New timeline resets it to the starter.

The starter connects the Reference and First/Last Frame checkpoints, text encoder,
both VAEs, Director, and native CreateVideo/SaveVideo nodes. Candidate outputs
have their own video saves for Seed Hunt. Refine V2 is installed and available
from ComfyUI's node menu. The starter uses 0.2 megapixels, 3 seconds, 20 steps,
res_multistep/simple and single-stage sampling for this 16GB GPU. Two-stage sampling
can be enabled once the latent upscaler is installed.

Videos are saved below `ComfyUI/output/AssetStudio/`, and are previewed by the
Save Video nodes. These direct ComfyUI renders are separate from the Studio job
queue and Catalog. Save the workflow before loading a different one in ComfyUI.

## Reinstall the integration

Run `installer/install-minimax-director.ps1` in PowerShell, optionally passing
`-ComfyDir` for another ComfyUI installation. It installs Director, motion-context
continuity, the latent-upscaler nodes, Python dependencies, the Studio bridge and
the starter workflow. It preserves existing repository checkouts. Restart ComfyUI
after installation. Build the Studio frontend with `npm.cmd run build` from
`frontend`, then reload the Studio window.

Model files are separate from the node installation. The current setup uses:

| Folder under ComfyUI/models | File |
| --- | --- |
| diffusion_models | minimax_h3_ref2va_pruned_int8_convrot.safetensors |
| diffusion_models | minimax_h3_fl2va_pruned_int8_convrot.safetensors |
| text_encoders | qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors |
| vae | minimax_h3_video_vae_int8_convrot.safetensors |
| vae | minimax_h3_audio_vae_fp32.safetensors |
| latent_upscale_models | minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors |

The H3 files come from [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3).
The latent-upscaler checkpoint comes from
[LBH-123-AI/Minimax_h3_latent_Upscaler](https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler).
On this PC the First/Last Frame checkpoint is already verified and supplied by
the existing extra model path at `C:/Users/Drog/AppData/Local/AssetStudio/models`.

Run `node tests/director-electron.cjs` from `frontend` with both local services
running to verify the real iframe, native editor, defaults, connections, autosave
and reload in an isolated hidden Electron profile. The test exports the API graph
and a screenshot under `data/tmp`.
