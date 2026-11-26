import json
import os
import secrets
import time

from .presets import CAMERA_MOVES, DEPTHS, LENS_LOOKS, LIGHTING, SHOT_SIZES, apply_camera
from .hashbidx_api import Client, HashBidXError, image_file_to_data_uri, image_tensor_to_data_uri, resolve_credentials, user_config_path

# Model IDs as listed by GET /v1/models on hashbidx.ai (checked 2026-11-26).
# Typical limits, for the README only; the API itself validates each model and returns a clear 400.
MODELS = {
    "dreamina-seedance-2-5":      {"label": "Seedance 2.5",            "durations": (4, 30), "resolutions": ["720p", "480p", "1080p"]},
    "dreamina-seedance-2-0":      {"label": "Seedance 2.0",            "durations": (4, 15), "resolutions": ["720p", "480p", "1080p"]},
    "dreamina-seedance-2-0-fast": {"label": "Seedance 2.0 Fast",       "durations": (4, 15), "resolutions": ["720p", "480p"]},
    "dreamina-seedance-2-0-mini": {"label": "Seedance 2.0 Mini",       "durations": (4, 15), "resolutions": ["480p", "720p"]},
    "MiniMax-H3":                 {"label": "MiniMax H3",              "durations": (4, 15), "resolutions": ["768p", "2k"]},
    "wan3.0-video":               {"label": "Wan 3.0",                 "durations": (2, 30), "resolutions": ["720p", "480p", "1080p"]},
    "wan3.0-video-prime":         {"label": "Wan 3.0 Prime",           "durations": (2, 30), "resolutions": ["720p", "480p", "1080p"]},
}
MODEL_IDS = list(MODELS.keys())
ALL_RESOLUTIONS = ["default", "480p", "720p", "768p", "1080p", "2k"]
RATIOS = ["auto", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"]


def _image_item(uri, role):
    return {"type": "image_url", "image_url": {"url": uri}, "role": role}


def _media_item(kind, url, role):
    return {"type": kind, kind: {"url": url}, "role": role}


AUDIO_MODES = ["model default", "on", "off"]


def build_body(model, prompt, duration, resolution, ratio, first_frame_uri=None, last_frame_uri=None,
               reference_uris=None, extra=None, audio="model default", reference_video_urls=None,
               reference_audio_urls=None):
    """Build the POST /v1/videos body (HashBidX docs, read and tested 2026-11-26):
    - duration: `seconds` as a string; 0 = model default (omitted)
    - resolution / ratio / generate_audio: inside `metadata` (never send `size`, it returns 400)
    - first frame only: top-level `input_reference` (data URL)
    - first + last frame: metadata.content items with role first_frame / last_frame
    - references: metadata.content items with role reference_image / reference_video / reference_audio
      (Seedance rejects image items without a role); references can't be mixed with first/last frames
    The API validates model-specific limits and returns a clear 400; `extra` JSON is merged last."""
    reference_uris = list(reference_uris or [])
    reference_video_urls = list(reference_video_urls or [])
    reference_audio_urls = list(reference_audio_urls or [])
    has_refs = bool(reference_uris or reference_video_urls or reference_audio_urls)
    if has_refs and (first_frame_uri or last_frame_uri):
        raise HashBidXError("References can't be mixed with first/last frames. Use one or the other.")
    if last_frame_uri and not first_frame_uri:
        raise HashBidXError("last_frame needs a first_frame too.")
    has_frames = bool(first_frame_uri or last_frame_uri)
    # "auto": with a first/last frame the shape follows the image (Seedance rejects a fixed ratio there);
    # text-to-video and references default to 16:9.
    body = {"model": model, "prompt": prompt, "metadata": {}}
    if ratio == "auto":
        if not has_frames:
            body["metadata"]["ratio"] = "16:9"
    else:
        body["metadata"]["ratio"] = ratio
    if resolution != "default":
        body["metadata"]["resolution"] = resolution
    if duration and duration > 0:
        body["seconds"] = str(duration)
    if audio == "on":
        body["metadata"]["generate_audio"] = True
    elif audio == "off":
        body["metadata"]["generate_audio"] = False
    if first_frame_uri and last_frame_uri:
        body["metadata"]["content"] = [_image_item(first_frame_uri, "first_frame"), _image_item(last_frame_uri, "last_frame")]
    elif first_frame_uri:
        body["input_reference"] = first_frame_uri
    elif has_refs:
        body["metadata"]["content"] = (
            [_image_item(u, "reference_image") for u in reference_uris]
            + [_media_item("video_url", u, "reference_video") for u in reference_video_urls]
            + [_media_item("audio_url", u, "reference_audio") for u in reference_audio_urls]
        )
    if extra:
        meta = extra.pop("metadata", None)
        body.update(extra)
        if isinstance(meta, dict):
            body["metadata"].update(meta)
    return body


def _urls(text):
    out = []
    for line in (text or "").splitlines():
        u = line.strip()
        if not u:
            continue
        if not (u.startswith("http://") or u.startswith("https://")):
            raise HashBidXError("Reference video/audio must be public http(s) links, one per line: %s" % u[:80])
        out.append(u)
    return out


# ComfyUI writes the whole canvas into every video and image it saves and keeps it in its run history.
# A key typed into the "HashBidX API Key" node is therefore swapped for a meaningless marker before the
# run starts; the key itself stays in this process's memory only.
_HIDDEN_PREFIX = "(key hidden) "
_TYPED_KEYS = {}  # marker -> key
_MARKERS = {}     # key -> marker (same key, same marker: an unchanged canvas stays cached)


def _hide(key):
    marker = _MARKERS.get(key)
    if marker is None:
        marker = _HIDDEN_PREFIX + secrets.token_hex(5)
        _MARKERS[key] = marker
        _TYPED_KEYS[marker] = key
    return marker


def _blank_key_widgets(obj):
    """Empty the api_key widget of every HashBidX API Key node in a saved-canvas structure (subgraphs included)."""
    if isinstance(obj, dict):
        if obj.get("type") == "HashBidXAPIKey":
            vals = obj.get("widgets_values")
            if isinstance(vals, list) and vals:
                vals[0] = ""  # api_key is the first widget
            elif isinstance(vals, dict) and "api_key" in vals:
                vals["api_key"] = ""
        for v in obj.values():
            _blank_key_widgets(v)
    elif isinstance(obj, list):
        for v in obj:
            _blank_key_widgets(v)


def hide_typed_keys(json_data):
    """Runs when a canvas is queued, before ComfyUI stores it anywhere."""
    try:
        graph = json_data.get("prompt") or {}
        for node in graph.values():
            if isinstance(node, dict) and node.get("class_type") == "HashBidXAPIKey":
                inputs = node.get("inputs") or {}
                key = inputs.get("api_key")
                if isinstance(key, str) and key.strip() and not key.startswith(_HIDDEN_PREFIX):
                    inputs["api_key"] = _hide(key.strip())
        extra = (json_data.get("extra_data") or {}).get("extra_pnginfo") or {}
        _blank_key_widgets(extra.get("workflow"))
    except Exception as e:  # never block a run because of this
        print("[HashBidX] could not hide the typed key: %s" % e)
    return json_data


class HashBidXAPIKey:
    """Holds the customer's own HashBidX key (BYOK). Leave empty to use the key saved on this computer."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "api_key": ("STRING", {"default": "", "multiline": False, "placeholder": "sk-hb-... (or leave empty to use config.json)"}),
            },
            "optional": {
                "base_url": ("STRING", {"default": "https://hashbidx.ai/v1"}),
                "save_key": ("BOOLEAN", {"default": False, "tooltip": "Save the key on this computer (ComfyUI/user/hashbidx/config.json). Then clear the field: the key stays saved and won't travel inside shared workflows."}),
            },
        }

    RETURN_TYPES = ("HASHBIDX_AUTH", "STRING")
    RETURN_NAMES = ("hashbidx", "status")
    FUNCTION = "make"
    CATEGORY = "HashBidX"
    OUTPUT_NODE = True  # lets "Run" save the key with this node alone: nothing is generated or charged

    def make(self, api_key, base_url="https://hashbidx.ai/v1", save_key=False):
        if (api_key or "").startswith(_HIDDEN_PREFIX):
            if api_key not in _TYPED_KEYS:
                raise HashBidXError("Please paste your HashBidX key into the api_key field again, or leave it empty to use the saved key.")
            api_key = _TYPED_KEYS[api_key]
        key, base = resolve_credentials(api_key, base_url)
        typed = bool((api_key or "").strip())
        status = "Using the key typed in this node." if typed else "Using the key saved on this computer."
        if save_key and typed:
            path = user_config_path()
            if path:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                # created readable by this user only
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"api_key": key, "base_url": base}, f)
                print("[HashBidX] key saved to %s" % path)
                status = ("Key saved on this computer. Now clear the api_key field: the saved key is used from here on "
                          "and never travels inside workflows you share. Nothing was generated or charged.")
            else:
                status = "Could not find the ComfyUI user folder, so the key was not saved. Keep the key in this node instead."
        elif save_key:
            status = "Using the key saved on this computer. (The api_key field is empty, so there is nothing new to save.)"
        return ({"api_key": key, "base_url": base}, status)


class HashBidXVideo:
    """Text-to-video, image-to-video and reference-to-video through the HashBidX API (Seedance 2.x, MiniMax H3, Wan 3.0)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (MODEL_IDS, {"default": "dreamina-seedance-2-5"}),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "duration": ("INT", {"default": 5, "min": 0, "max": 30, "step": 1, "tooltip": "Seconds. 0 = model default."}),
                "resolution": (ALL_RESOLUTIONS, {"default": "720p", "tooltip": "MiniMax H3 uses 768p / 2k. 'default' lets the model choose."}),
                "ratio": (RATIOS, {"default": "auto", "tooltip": "auto = follow the first-frame image, or 16:9 otherwise."}),
                "audio": (AUDIO_MODES, {"default": "model default", "tooltip": "Generate sound with the video (dialogue, effects, ambience)."}),
            },
            "optional": {
                "hashbidx": ("HASHBIDX_AUTH",),
                "camera": ("HASHBIDX_CAMERA", {"tooltip": "From the 'HashBidX Camera & Lens' node."}),
                "first_frame": ("IMAGE",),
                "last_frame": ("IMAGE",),
                "reference_images": ("IMAGE", {"tooltip": "Batch of reference images (not with first/last frame)."}),
                "references": ("HASHBIDX_REFS", {"tooltip": "From the 'HashBidX Reference Pack' node: character/product images, reference videos and audio."}),
                "extra_params_json": ("STRING", {"multiline": True, "default": "", "placeholder": "{\"metadata\": {\"watermark\": true}}  (advanced, optional)"}),
                "max_wait_seconds": ("INT", {"default": 1200, "min": 60, "max": 7200}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF, "tooltip": "Changing it forces a new run; not sent to the API."}),
            },
        }

    RETURN_TYPES = ("VIDEO", "IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("video", "last_frame", "file_path", "task_id")
    FUNCTION = "generate"
    CATEGORY = "HashBidX"
    OUTPUT_NODE = True

    def generate(self, model, prompt, duration, resolution, ratio, audio="model default", hashbidx=None, camera=None,
                 first_frame=None, last_frame=None, reference_images=None, references=None, extra_params_json="",
                 max_wait_seconds=1200, seed=0):
        if not prompt.strip():
            raise HashBidXError("prompt is required.")
        if hashbidx:
            key, base = hashbidx["api_key"], hashbidx["base_url"]
        else:
            key, base = resolve_credentials()
        extra = None
        if extra_params_json.strip():
            try:
                extra = json.loads(extra_params_json)
            except ValueError as e:
                raise HashBidXError("extra_params_json is not valid JSON: %s" % e)
        if camera:
            prompt = apply_camera(prompt, **camera)
        ref_uris, ref_videos, ref_audios = [], [], []
        if reference_images is not None:
            ref_uris += [image_tensor_to_data_uri(reference_images[i:i + 1]) for i in range(len(reference_images))]
        if references:
            ref_uris += references.get("images", [])
            ref_videos += references.get("videos", [])
            ref_audios += references.get("audios", [])
        body = build_body(
            model, prompt, duration, resolution, ratio,
            image_tensor_to_data_uri(first_frame) if first_frame is not None else None,
            image_tensor_to_data_uri(last_frame) if last_frame is not None else None,
            ref_uris, extra, audio, ref_videos, ref_audios,
        )
        client = Client(key, base)
        created = client.create_video(body)
        task_id = created.get("id")
        if not task_id:
            raise HashBidXError("API did not return a task id: %s" % json.dumps(created)[:500])
        print("[HashBidX] %s task %s %s" % (model, task_id, created.get("status")))

        pbar = _progress_bar()

        def on_progress(info):
            p = info.get("progress")
            if pbar is not None and isinstance(p, (int, float)):
                pbar.update_absolute(int(p), 100)
            print("[HashBidX] %s %s %s%%" % (task_id, info.get("status"), p))

        # The create call can wait ~60 s and may already return a completed task.
        if str(created.get("status", "")).lower() != "completed":
            client.wait(task_id, max_wait_seconds=max_wait_seconds, on_progress=on_progress)
        data = client.download_video(task_id)
        path = _save(data, model, task_id)
        return {"ui": {"text": [path]}, "result": (_to_video(path), _last_frame(path), path, task_id)}


class HashBidXCamera:
    """Camera, lens and lighting presets. The video APIs have no camera parameters, so these are added to the
    prompt as cinematography directions, which Seedance, MiniMax H3 and Wan follow."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "shot_size": (list(SHOT_SIZES.keys()), {"default": "none"}),
                "camera_move": (list(CAMERA_MOVES.keys()), {"default": "none"}),
                "lens_look": (list(LENS_LOOKS.keys()), {"default": "none"}),
                "depth_of_field": (list(DEPTHS.keys()), {"default": "none"}),
                "lighting": (list(LIGHTING.keys()), {"default": "none"}),
            }
        }

    RETURN_TYPES = ("HASHBIDX_CAMERA", "STRING")
    RETURN_NAMES = ("camera", "camera_text")
    FUNCTION = "make"
    CATEGORY = "HashBidX"

    def make(self, shot_size, camera_move, lens_look, depth_of_field, lighting):
        cam = {"shot_size": shot_size, "camera_move": camera_move, "lens_look": lens_look,
               "depth_of_field": depth_of_field, "lighting": lighting}
        from .presets import camera_text
        return (cam, camera_text(**cam))


class HashBidXMultiShot:
    """Writes a multi-shot prompt (shot 1, shot 2, ...) with time ranges, the format Seedance uses for
    cuts inside one clip. Connect `prompt` to the Video node's prompt input and set its duration to `total_seconds`."""

    @classmethod
    def INPUT_TYPES(cls):
        req = {"style": ("STRING", {"multiline": True, "default": "", "placeholder": "Overall look, characters and setting (optional)"})}
        for i in range(1, 5):
            req["shot_%d" % i] = ("STRING", {"multiline": True, "default": ""})
            req["seconds_%d" % i] = ("INT", {"default": 4 if i <= 2 else 0, "min": 0, "max": 30})
        return {"required": req}

    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("prompt", "total_seconds")
    FUNCTION = "make"
    CATEGORY = "HashBidX"

    def make(self, style, **kw):
        lines, t = [], 0
        if style.strip():
            lines.append(style.strip())
        n = 0
        for i in range(1, 5):
            text = kw.get("shot_%d" % i, "").strip()
            secs = int(kw.get("seconds_%d" % i, 0) or 0)
            if not text or secs <= 0:
                continue
            n += 1
            lines.append("Shot %d (%d-%d seconds): %s" % (n, t, t + secs, text))
            t += secs
        if not n:
            raise HashBidXError("Add at least one shot with seconds > 0.")
        return ("\n".join(lines), t)


class HashBidXReferencePack:
    """Collects references for consistent characters, products or style: every image in a folder (e.g. a
    character sheet) and/or an image batch, plus public links to reference videos and audio."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_folder": ("STRING", {"default": "", "placeholder": "Folder with reference images (optional), e.g. /Users/me/characters/mia"}),
                "max_images": ("INT", {"default": 9, "min": 1, "max": 30, "tooltip": "Seedance 2.0 / H3 take up to 9; Seedance 2.5 more."}),
                "video_urls": ("STRING", {"multiline": True, "default": "", "placeholder": "Reference video links (https), one per line"}),
                "audio_urls": ("STRING", {"multiline": True, "default": "", "placeholder": "Reference audio links (https), one per line"}),
            },
            "optional": {
                "images": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("HASHBIDX_REFS", "INT")
    RETURN_NAMES = ("references", "image_count")
    FUNCTION = "make"
    CATEGORY = "HashBidX"

    def make(self, image_folder, max_images, video_urls, audio_urls, images=None):
        uris = []
        if images is not None:
            uris += [image_tensor_to_data_uri(images[i:i + 1]) for i in range(len(images))]
        folder = image_folder.strip().strip('"')
        if folder:
            folder = os.path.expanduser(folder)
            if not os.path.isdir(folder):
                raise HashBidXError("image_folder not found: %s" % folder)
            for name in sorted(os.listdir(folder)):
                if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    uris.append(image_file_to_data_uri(os.path.join(folder, name)))
        uris = uris[:max_images]
        refs = {"images": uris, "videos": _urls(video_urls), "audios": _urls(audio_urls)}
        if not (refs["images"] or refs["videos"] or refs["audios"]):
            raise HashBidXError("Reference Pack is empty: add an image folder, images, or links.")
        return (refs, len(uris))


def _last_frame(path):
    """Last frame of the clip as a ComfyUI IMAGE, for chaining into the next shot's first_frame."""
    import numpy as np
    import torch
    try:
        import av
        last = None
        with av.open(path) as c:
            for frame in c.decode(video=0):
                last = frame
        if last is not None:
            arr = last.to_ndarray(format="rgb24").astype("float32") / 255.0
            return torch.from_numpy(arr)[None, ...]
    except Exception as e:
        print("[HashBidX] could not read last frame: %s" % e)
    return torch.zeros((1, 64, 64, 3))


def _progress_bar():
    try:
        import comfy.utils
        return comfy.utils.ProgressBar(100)
    except Exception:
        return None


def _save(data, model, task_id):
    try:
        import folder_paths
        out_dir = os.path.join(folder_paths.get_output_directory(), "hashbidx")
    except Exception:
        out_dir = os.path.join(os.getcwd(), "output", "hashbidx")
    os.makedirs(out_dir, exist_ok=True)
    safe_id = "".join(c for c in task_id if c.isalnum() or c in "-_")[-24:]
    path = os.path.join(out_dir, "%s_%s_%s.mp4" % (time.strftime("%Y%m%d-%H%M%S"), model, safe_id))
    with open(path, "wb") as f:
        f.write(data)
    return path


def _to_video(path):
    try:
        from comfy_api.input_impl import VideoFromFile
        return VideoFromFile(path)
    except Exception:
        return path


NODE_CLASS_MAPPINGS = {
    "HashBidXAPIKey": HashBidXAPIKey,
    "HashBidXVideo": HashBidXVideo,
    "HashBidXCamera": HashBidXCamera,
    "HashBidXMultiShot": HashBidXMultiShot,
    "HashBidXReferencePack": HashBidXReferencePack,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "HashBidXAPIKey": "HashBidX API Key",
    "HashBidXVideo": "HashBidX Video (Seedance / H3 / Wan)",
    "HashBidXCamera": "HashBidX Camera & Lens",
    "HashBidXMultiShot": "HashBidX Multi-Shot Prompt",
    "HashBidXReferencePack": "HashBidX Reference Pack",
}
