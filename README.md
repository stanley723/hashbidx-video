# ComfyUI-HashBidX

Use your own HashBidX API key inside ComfyUI to generate video with Seedance 2.5 / 2.0, MiniMax H3 and Wan 3.0.
No Comfy credits are used: generation is billed to your HashBidX account.

## Install
- **ComfyUI Manager:** open Manager, search **HashBidX**, click Install, restart ComfyUI. Or run `comfy node install hashbidx-video`.
- **Manual:** copy this folder into `ComfyUI/custom_nodes/` and restart ComfyUI.

No extra Python packages are needed.

## Add your key (once)
1. Get a key on the **API Keys** page at https://hashbidx.ai.
2. In ComfyUI open **Templates**, choose **hashbidx-video** at the bottom of the list, and open **1 Save your HashBidX key**.
3. Paste the key into **api_key**, leave **save_key** on, and press **Run**. Nothing is generated or charged.
4. Clear the key field. The key stays saved on this computer (`ComfyUI/user/hashbidx/config.json`), survives plug-in updates, and never travels inside workflows you share.

You can also set the environment variable `HASHBIDX_API_KEY`.

## Quick start
Open **Templates > hashbidx-video** and pick one:

| Template | What it does |
|---|---|
| **1 Save your HashBidX key** | Saves your key on this computer. Run it once. |
| **2 Text to video** | A prompt in, a clip out. |
| **3 Image to video** | Pick a still in **Load Image**, describe the motion, press **Run**. |
| **4 Two-shot chain with camera presets** | A timed two-shot prompt with camera presets, then a second clip that continues from the first clip's last frame. |

The finished clip plays in the **Save Video** node and is saved to `ComfyUI/output/hashbidx/`. The same files are in this plug-in's `example_workflows` folder if you prefer to drag them onto the canvas.

## Nodes
| Node | What it does |
|---|---|
| **HashBidX API Key** | Holds your key. Save it once, then leave the field empty. |
| **HashBidX Video (Seedance / H3 / Wan)** | Generates the clip: text-to-video, image-to-video, first + last frame, or references. Outputs the video and its **last frame**. |
| **HashBidX Camera & Lens** | Dropdowns for shot size, camera move, lens look, depth of field and lighting. Connect `camera` to the Video node. |
| **HashBidX Multi-Shot Prompt** | Write up to 4 shots with seconds each; it builds one timed prompt for cuts inside a single clip. Connect `prompt` to the Video node's prompt and use `total_seconds` as the duration. |
| **HashBidX Reference Pack** | A folder of character / product images (plus optional reference video and audio links) for consistent subjects. Connect `references` to the Video node. |

## Use
On **HashBidX Video**:
- `model`, `prompt`, `duration` (seconds, 0 = model default), `resolution` ("default" = model default), `ratio` ("auto" follows the first-frame image, otherwise 16:9)
- `audio`: on / off / model default
- optional `first_frame` (image-to-video), `first_frame` + `last_frame`, or references (`reference_images` batch and/or a **Reference Pack**). References can't be combined with first/last frames.
- `last_frame` output: connect it to the next Video node's `first_frame` to continue a scene shot by shot.
- `extra_params_json` for advanced fields, merged into the request (e.g. `{"metadata": {"watermark": true}}`)

The camera presets are added to your prompt as plain cinematography directions (these models have no separate camera settings), so you can also just write them yourself.

If a model doesn't support a duration or resolution, HashBidX returns a clear error, shown in ComfyUI.

The Video node submits the job, shows progress, downloads the MP4 to `ComfyUI/output/hashbidx/`, and outputs a standard `VIDEO` you can connect to **Save Video**, upscalers, colour (LUT) nodes or **Get Video Components**.

## What you pay
- You pay only for generated video, at the prices on https://hashbidx.ai. The plug-in is free and adds no fee.
- The Camera, Multi-Shot and Reference Pack nodes run on your computer and cost nothing.
- Longer prompts, camera presets, reference images and first/last frames do not change the price.
- Seedance is priced by video size (resolution x seconds); MiniMax H3 and Wan by seconds of output.
- A **reference video** is counted by Seedance as extra input, so that clip costs more (it is billed at the lower "with video input" rate).
- Failed generations are not charged. Your usage page on hashbidx.ai shows the exact charge of every clip.

Typical limits (the API is the source of truth):

| Model | ID | Seconds | Resolution |
|---|---|---|---|
| Seedance 2.5 | dreamina-seedance-2-5 | 4-30 | 480p / 720p / 1080p |
| Seedance 2.0 | dreamina-seedance-2-0 | 4-15 | 480p / 720p / 1080p |
| Seedance 2.0 Fast | dreamina-seedance-2-0-fast | 4-15 | 480p / 720p |
| Seedance 2.0 Mini | dreamina-seedance-2-0-mini | 4-15 | 480p / 720p |
| MiniMax H3 | MiniMax-H3 | 4-15 | 768p / 2k |
| Wan 3.0 | wan3.0-video | 2-30 | 480p / 720p / 1080p |
| Wan 3.0 Prime | wan3.0-video-prime | 2-30 | 480p / 720p / 1080p |

Get a key at https://hashbidx.ai (API Keys page).
