import json
import os
import secrets
import time

from .presets import (CAMERA_MOVES, COLOR_GRADES, DEPTHS, ERAS, GENRES, LENS_LOOKS, LIGHTING, PACING, SHOT_SIZES,
                      apply_camera)
from .hashbidx_api import (Client, HashBidXError, download_url, image_file_to_data_uri, image_tensor_to_data_uri,
                           key_tail, resolve_credentials, saved_key, user_config_path, verify_key)
from .hashbidx_api import BAD_KEY, NO_KEY
from .hashbidx_api import save_key as store_key

# Model IDs as listed by GET /v1/models on hashbidx.ai (checked 2026-11-26).
# Typical limits, for the README only; the API itself validates each model and returns a clear 400.
MODELS = {
    "dreamina-seedance-2-5":      {"label": "Seedance 2.5",            "durations": (4, 30), "resolutions": ["720p", "480p", "1080p"], "extend": "video"},
    "dreamina-seedance-2-0":      {"label": "Seedance 2.0",            "durations": (4, 15), "resolutions": ["720p", "480p", "1080p", "4k"], "extend": "video"},
    "dreamina-seedance-2-0-fast": {"label": "Seedance 2.0 Fast",       "durations": (4, 15), "resolutions": ["720p", "480p"], "extend": "video"},
    "dreamina-seedance-2-0-mini": {"label": "Seedance 2.0 Mini",       "durations": (4, 15), "resolutions": ["480p", "720p"], "extend": "video"},
    "MiniMax-H3":                 {"label": "MiniMax H3",              "durations": (4, 15), "resolutions": ["768p", "2k"], "extend": "video"},
    "wan3.0-video":               {"label": "Wan 3.0",                 "durations": (2, 30), "resolutions": ["720p", "480p", "1080p"], "extend": "frame"},
    "wan3.0-video-prime":         {"label": "Wan 3.0 Prime",           "durations": (2, 30), "resolutions": ["720p", "480p", "1080p"], "extend": "frame"},
}
# "extend": how a clip is continued on that model (tested 2026-11-27). "video" = the source clip goes in as a
# reference video and the model films what comes next. "frame" = the model starts from the source clip's last
# frame (Wan re-films a reference video from its start instead of continuing it).
MODEL_IDS = list(MODELS.keys())
ALL_RESOLUTIONS = ["default", "480p", "720p", "768p", "1080p", "2k", "4k"]
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
    elif ratio:
        body["metadata"]["ratio"] = ratio  # None = not sent, the model keeps the shape of its input
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


def _strip(obj, keys):
    """Remove the typed key(s) from every string in a JSON-like structure, in place."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                for key in keys:
                    if key in v:
                        v = v.replace(key, "")
                obj[k] = v
            else:
                _strip(v, keys)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                for key in keys:
                    if key in v:
                        v = v.replace(key, "")
                obj[i] = v
            else:
                _strip(v, keys)


def hide_typed_keys(json_data):
    """Runs when a canvas is queued, before ComfyUI stores it anywhere."""
    try:
        graph = json_data.get("prompt") or {}
        keys = []
        for node in graph.values():
            if isinstance(node, dict) and node.get("class_type") == "HashBidXAPIKey":
                inputs = node.get("inputs") or {}
                key = inputs.get("api_key")
                if isinstance(key, str) and key.strip() and not key.startswith(_HIDDEN_PREFIX):
                    keys.append(key.strip())
                    inputs["api_key"] = _hide(key.strip())
        if keys:
            # the copy of the canvas that ComfyUI embeds in saved files (any layout, subgraphs included)
            _strip(json_data.get("extra_data"), keys)
    except Exception as e:  # never block a run because of this
        print("[HashBidX] could not hide the typed key: %s" % e)
    return json_data


class HashBidXAPIKey:
    """The customer's own HashBidX key (BYOK). Paste it once: it is checked with HashBidX, saved on this computer and
    the field is emptied again. Every HashBidX box connected to this one then uses the saved key."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "api_key": ("STRING", {"default": "", "multiline": False, "placeholder": "paste your key here (sk-hb-...)",
                                       "tooltip": "Paste your HashBidX key and click 'Verify and save key'. It is saved on this computer; the field is emptied again so the key never travels inside a workflow you share."}),
            },
        }

    RETURN_TYPES = ("HASHBIDX_AUTH", "STRING")
    RETURN_NAMES = ("hashbidx", "status")
    FUNCTION = "make"
    CATEGORY = "HashBidX"
    OUTPUT_NODE = True  # lets "Run" check and save the key with this box alone: nothing is generated or charged

    @classmethod
    def IS_CHANGED(cls, api_key="", **kwargs):
        # run again when the key saved on this computer changes (for example from another canvas)
        try:
            saved = os.path.getmtime(user_config_path())
        except Exception:
            saved = 0
        return "%s|%s" % (api_key, saved)

    def make(self, api_key="", base_url="", save_key=True):
        # base_url and save_key were fields of this box before 0.6.0; canvases saved then may still send them.
        if (api_key or "").startswith(_HIDDEN_PREFIX):
            if api_key not in _TYPED_KEYS:
                raise HashBidXError("Please paste your HashBidX key into the api_key field again, or leave it empty to use the saved key.")
            api_key = _TYPED_KEYS[api_key]
        typed = (api_key or "").strip()
        status = connect_key(typed)["message"]
        key, base = resolve_credentials(typed)
        return {"ui": {"text": [status]}, "result": ({"api_key": key, "base_url": base}, status)}


def connect_key(typed=""):
    """Check a key with HashBidX and, if it was typed, save it on this computer. Used by the 'HashBidX API Key' box
    when a canvas runs and by its 'Verify and save key' button. Raises HashBidXError with what to do when the key is
    missing or refused."""
    typed = (typed or "").strip()
    key, base = resolve_credentials(typed)  # raises the "no key yet" message
    verify_key(key, base)  # raises the "key not accepted" message
    tail = key_tail(key)
    ends = " (ends in %s)" % tail if tail else ""
    if typed and typed != saved_key():
        store_key(key)
        print("[HashBidX] key verified and saved on this computer")
        message = ("Key verified and saved on this computer%s. The field is emptied on purpose: every HashBidX box "
                   "now uses the saved key." % ends)
    else:
        message = "Key verified. Every HashBidX box uses the key saved on this computer%s." % ends
    return {"ok": True, "saved": True, "tail": tail, "message": message}


def key_state():
    """What the 'HashBidX API Key' box shows before anything runs. Never returns the key itself."""
    key = saved_key()
    if not key:
        return {"saved": False, "tail": "",
                "message": "No key yet. Paste your HashBidX key above and click 'Verify and save key'."}
    tail = key_tail(key)
    return {"saved": True, "tail": tail,
            "message": "Every HashBidX box uses the key saved on this computer%s. To change it, paste a new key above "
                       "and click 'Verify and save key'." % (" (ends in %s)" % tail if tail else "")}


def add_key_routes(server):
    """GET /hashbidx/key: is a key saved (last four characters only). POST /hashbidx/key: check and save a key."""
    import asyncio

    from aiohttp import web

    @server.routes.get("/hashbidx/key")
    async def _get_key(request):
        return web.json_response(key_state())

    @server.routes.post("/hashbidx/key")
    async def _post_key(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        typed = str((data or {}).get("api_key") or "")
        try:
            out = await asyncio.get_running_loop().run_in_executor(None, connect_key, typed)
        except HashBidXError as e:
            text = str(e).split("\nDetails:")[0]
            if text.startswith(NO_KEY):
                text = "No key yet. Paste your HashBidX key above and click 'Verify and save key'."
            elif text.startswith(BAD_KEY):
                text = ("HashBidX did not accept this key, so it was not saved. Copy the whole key again from "
                        "hashbidx.ai > API Keys and paste it above.")
            out = dict(key_state(), ok=False, message=text)
        except Exception as e:
            out = dict(key_state(), ok=False, message="Could not check the key: %s" % e)
        return web.json_response(out)


class HashBidXVideo:
    """Text-to-video, image-to-video and reference-to-video through the HashBidX API (Seedance 2.x, MiniMax H3, Wan 3.0)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (MODEL_IDS, {"default": "dreamina-seedance-2-5"}),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "duration": ("INT", {"default": 5, "min": 0, "max": 30, "step": 1, "tooltip": "Seconds. 0 = model default."}),
                "resolution": (ALL_RESOLUTIONS, {"default": "720p", "tooltip": "MiniMax H3 uses 768p / 2k. 4k is on Seedance 2.0. 'default' lets the model choose."}),
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
        key, base = _auth(hashbidx)
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
        path, task_id = _run_video(Client(key, base), model, body, max_wait_seconds)
        return {"ui": {"text": [path]}, "result": (_to_video(path), _last_frame(path), path, task_id)}


class HashBidXCamera:
    """Camera, lens, lighting, genre, era, colour and pacing presets. The video APIs have no camera parameters, so these are added to the
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
            },
            # added in 0.5.0 at the end, so canvases saved with older versions keep their values
            "optional": {
                "second_move": (list(CAMERA_MOVES.keys()), {"default": "none", "tooltip": "A second camera move that follows the first one."}),
                "genre": (list(GENRES.keys()), {"default": "none"}),
                "era_look": (list(ERAS.keys()), {"default": "none", "tooltip": "Film stock and look of a decade."}),
                "color_grade": (list(COLOR_GRADES.keys()), {"default": "none"}),
                "pacing": (list(PACING.keys()), {"default": "none"}),
            },
        }

    RETURN_TYPES = ("HASHBIDX_CAMERA", "STRING")
    RETURN_NAMES = ("camera", "camera_text")
    FUNCTION = "make"
    CATEGORY = "HashBidX"

    def make(self, shot_size, camera_move, lens_look, depth_of_field, lighting, second_move="none", genre="none",
             era_look="none", color_grade="none", pacing="none"):
        cam = {"shot_size": shot_size, "camera_move": camera_move, "lens_look": lens_look,
               "depth_of_field": depth_of_field, "lighting": lighting, "second_move": second_move, "genre": genre,
               "era_look": era_look, "color_grade": color_grade, "pacing": pacing}
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

    RETURN_TYPES = ("HASHBIDX_REFS", "INT", "STRING")
    RETURN_NAMES = ("references", "image_count", "legend")
    FUNCTION = "make"
    CATEGORY = "HashBidX"

    def make(self, image_folder, max_images, video_urls, audio_urls, images=None):
        uris, names = [], []
        if images is not None:
            uris += [image_tensor_to_data_uri(images[i:i + 1]) for i in range(len(images))]
            names += ["picture %d from the images input" % (i + 1) for i in range(len(images))]
        folder = image_folder.strip().strip('"')
        if folder:
            folder = os.path.expanduser(folder)
            if not os.path.isdir(folder):
                raise HashBidXError("image_folder not found: %s" % folder)
            for name in sorted(os.listdir(folder)):
                if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    uris.append(image_file_to_data_uri(os.path.join(folder, name)))
                    names.append(name)
        uris, names = uris[:max_images], names[:max_images]
        refs = {"images": uris, "videos": _urls(video_urls), "audios": _urls(audio_urls)}
        if not (refs["images"] or refs["videos"] or refs["audios"]):
            raise HashBidXError("Reference Pack is empty: add an image folder, images, or links.")
        # How to point at each reference from the prompt ("Image 1", "Video 1", "Audio 1": the order they are sent in).
        legend = ["Image %d = %s" % (i + 1, n) for i, n in enumerate(names)]
        legend += ["Video %d = %s" % (i + 1, u) for i, u in enumerate(refs["videos"])]
        legend += ["Audio %d = %s" % (i + 1, u) for i, u in enumerate(refs["audios"])]
        return (refs, len(uris), "\n".join(legend))


def _auth(hashbidx):
    if hashbidx and hashbidx.get("api_key"):
        return hashbidx["api_key"], hashbidx["base_url"]
    return resolve_credentials()


def _run_video(client, model, body, max_wait_seconds):
    """Create the task, wait for it, download the clip. Returns (file path, task id)."""
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
    return _save(data, model, task_id), task_id


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


SEEDANCE_IDS = [m for m in MODEL_IDS if "seedance" in m]
EXTEND, EDIT = "extend (continue the clip)", "edit (change something, keep the rest)"
_JOINED = {}  # task id -> the joined clip made in this session, so a second extension joins onto the whole clip


class HashBidXExtendEdit:
    """Continue a finished clip (extend) or change something in it and keep the rest (edit). The source is a
    clip made by a HashBidX Video node (connect its task_id) or any clip with a public https link."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": ([EXTEND, EDIT], {"default": EXTEND}),
                "prompt": ("STRING", {"multiline": True, "default": "", "placeholder": "Extend: what happens next.  Edit: what to change."}),
                "model": (MODEL_IDS, {"default": "dreamina-seedance-2-5"}),
                "duration": ("INT", {"default": 5, "min": 0, "max": 30, "step": 1, "tooltip": "Seconds of the new part. For an edit, use the length of the source clip. 0 = model default."}),
                "resolution": (["same as source"] + ALL_RESOLUTIONS[1:], {"default": "same as source"}),
                "ratio": (["same as source"] + [r for r in RATIOS if r not in ("auto", "adaptive")], {"default": "same as source"}),
                "audio": (AUDIO_MODES, {"default": "model default"}),
                "join_with_source": ("BOOLEAN", {"default": True, "tooltip": "Extend only: the video output is the source clip and the new part as one clip."}),
            },
            "optional": {
                "hashbidx": ("HASHBIDX_AUTH",),
                "source_task_id": ("STRING", {"default": "", "placeholder": "connect task_id from a HashBidX Video box", "tooltip": "Connect the task_id output of a HashBidX Video node (or of another Extend / Edit node), or paste a task id."}),
                "source_video_url": ("STRING", {"default": "", "placeholder": "or a public https link to the source clip"}),
                "references": ("HASHBIDX_REFS", {"tooltip": "Optional pictures the prompt refers to, e.g. the new label or product for an edit."}),
                "camera": ("HASHBIDX_CAMERA",),
                "max_wait_seconds": ("INT", {"default": 1200, "min": 60, "max": 7200}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF, "tooltip": "Changing it forces a new run; not sent to the API."}),
            },
        }

    RETURN_TYPES = ("VIDEO", "IMAGE", "STRING", "STRING", "VIDEO")
    RETURN_NAMES = ("video", "last_frame", "file_path", "task_id", "new_part")
    FUNCTION = "generate"
    CATEGORY = "HashBidX"
    OUTPUT_NODE = True

    def generate(self, mode, prompt, model, duration, resolution, ratio, audio="model default", join_with_source=True,
                 hashbidx=None, source_task_id=None, source_video_url="", references=None, camera=None,
                 max_wait_seconds=1200, seed=0):
        if not prompt.strip():
            raise HashBidXError("prompt is required: say what happens next (extend) or what to change (edit).")
        key, base = _auth(hashbidx)
        client = Client(key, base)
        source_task_id = (source_task_id or "").strip()
        url = (source_video_url or "").strip()
        info = {}
        if source_task_id:
            url, info = client.video_link(source_task_id)
        elif not url:
            raise HashBidXError("No source clip. Connect the task_id output of a HashBidX Video node to source_task_id, "
                                "or put a public https link into source_video_url.")
        elif not (url.startswith("http://") or url.startswith("https://")):
            raise HashBidXError("source_video_url must be a public http(s) link. A clip on your computer needs a link "
                                "first; a clip made by a HashBidX Video node can be connected through task_id instead.")
        meta = info.get("metadata") if isinstance(info.get("metadata"), dict) else {}
        offered = MODELS.get(model, {}).get("resolutions", [])
        if resolution == "same as source":
            src_res = str(meta.get("resolution") or "").lower()
            resolution = src_res if src_res in offered else "default"
        if ratio == "same as source":
            ratio = meta.get("ratio") if meta.get("ratio") in RATIOS else "16:9"
        refs = references or {}
        src = _JOINED.get(source_task_id) if source_task_id else None
        if src and not os.path.exists(src):
            src = None

        def source_file():
            data = client.download_video(source_task_id) if source_task_id else download_url(url)
            return _save(data, "source", source_task_id or "link")

        if mode == EXTEND and MODELS.get(model, {}).get("extend") == "frame":
            # Wan: start the new part on the last frame of the source clip
            if refs.get("images") or refs.get("videos") or refs.get("audios"):
                raise HashBidXError("On Wan models an extension starts from the last frame of the clip, so a Reference Pack "
                                    "cannot be added to it. Disconnect the Reference Pack, or pick a Seedance model or MiniMax H3.")
            src = src or source_file()
            text = ("Continue this shot from the given first frame, with the same subjects, setting and style. "
                    "What happens next: %s" % prompt.strip())
            if camera:
                text = apply_camera(text, **camera)
            frame = _last_frame(src)
            if frame.shape[1] <= 64 and float(frame.max()) == 0.0:
                raise HashBidXError("Could not read the last frame of the source clip, so it cannot be extended on this "
                                    "model. Pick a Seedance model or MiniMax H3 for this extension.")
            body = build_body(model, text, duration, resolution, "auto", image_tensor_to_data_uri(frame), None,
                              None, None, audio)
        else:
            strict = model == "dreamina-seedance-2-5"
            if mode == EXTEND:
                text = ("Extend the reference video (Video 1). Continue directly from its last frame with the same subjects, "
                        "setting and style. What happens next: %s" % prompt.strip())
                if strict:
                    ratio = None  # Seedance 2.5 keeps the shape of the source clip and refuses a ratio here (tested 2026-11-27)
            elif strict:
                # Seedance 2.5 refuses a length for what it reads as "video editing", and the API always sends one.
                # Worded as a new take of the same shot, it accepts the request and returns the edited clip.
                text = ("Video 1 is the reference. Film a new take of the same shot with the same setting, camera and "
                        "motion as Video 1. Director's note for the new take: %s." % prompt.strip().rstrip("."))
            else:
                text = ("Edit the reference video (Video 1). The change: %s. Keep everything else exactly as it is: "
                        "the other subjects, the setting, the camera and the motion." % prompt.strip().rstrip("."))
            if camera:
                text = apply_camera(text, **camera)
            body = build_body(model, text, duration, resolution, ratio, None, None, refs.get("images", []), None, audio,
                              [url] + list(refs.get("videos", [])), refs.get("audios", []))
        try:
            path, task_id = _run_video(client, model, body, max_wait_seconds)
        except HashBidXError as e:
            if "identified your task as video" in str(e):
                label = MODELS.get(model, {}).get("label", model)
                if mode == EXTEND:
                    raise HashBidXError("%s did not accept this extension. Describe what happens next in other words and "
                                        "run again, or pick another model for this clip (Seedance 2.0 and MiniMax H3 both "
                                        "do this). Nothing was charged." % label) from None
                raise HashBidXError("%s did not accept this edit. It takes changing, replacing and adding things; for "
                                    "taking something out of the clip pick Seedance 2.0 or MiniMax H3. You can also say "
                                    "what the new take shows (\"the fox is white\") and run again. Nothing was charged."
                                    % label) from None
            raise
        new_part = path
        if mode == EXTEND and join_with_source:
            try:
                src = src or source_file()
                joined = os.path.splitext(path)[0] + "_joined.mp4"
                _join([src, path], joined)
                _JOINED[task_id] = joined
                path = joined
            except Exception as e:
                # the new part is paid for and saved; never lose it because joining failed
                print("[HashBidX] could not join the source clip and the new part (%s). The new part is at %s" % (e, path))
        return {"ui": {"text": [path]}, "result": (_to_video(path), _last_frame(path), path, task_id, _to_video(new_part))}


IMAGE_MODELS = ["qwen-image-3.0", "qwen-image-3.0-pro"]
IMAGE_SIZES = {"16:9": "1280x720", "9:16": "720x1280", "1:1": "1024x1024", "4:3": "1152x864", "3:4": "864x1152"}


class HashBidXImage:
    """Makes pictures with the same HashBidX key: a first frame, storyboard frames, a character or product sheet.
    With a reference picture connected, it changes or restyles that picture as the prompt says."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (IMAGE_MODELS, {"default": "qwen-image-3.0"}),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "ratio": (list(IMAGE_SIZES.keys()), {"default": "16:9"}),
                "count": ("INT", {"default": 1, "min": 1, "max": 4, "tooltip": "How many pictures to make in this run (each one is charged)."}),
            },
            "optional": {
                "hashbidx": ("HASHBIDX_AUTH",),
                "reference_image": ("IMAGE", {"tooltip": "Optional: one picture to change or restyle as the prompt says."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF, "tooltip": "Changing it forces a new run; not sent to the API."}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "file_paths")
    FUNCTION = "generate"
    CATEGORY = "HashBidX"

    def generate(self, model, prompt, ratio, count=1, hashbidx=None, reference_image=None, seed=0):
        import base64
        import io

        import numpy as np
        import torch
        from PIL import Image

        if not prompt.strip():
            raise HashBidXError("prompt is required.")
        key, base = _auth(hashbidx)
        body = {"model": model, "prompt": prompt.strip(), "size": IMAGE_SIZES[ratio], "n": int(count), "response_format": "b64_json"}
        if reference_image is not None:
            body["image"] = image_tensor_to_data_uri(reference_image)
        out = Client(key, base).create_image(body)
        items = out.get("data") or []
        if not items:
            raise HashBidXError("The API returned no picture: %s" % json.dumps(out)[:400])
        try:
            import folder_paths
            out_dir = os.path.join(folder_paths.get_output_directory(), "hashbidx")
        except Exception:
            out_dir = os.path.join(os.getcwd(), "output", "hashbidx")
        os.makedirs(out_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        tensors, paths, size = [], [], None
        for i, item in enumerate(items):
            raw = base64.b64decode(item["b64_json"]) if item.get("b64_json") else download_url(item.get("url") or "")
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            path = os.path.join(out_dir, "%s_%s_%d.png" % (stamp, model, i + 1))
            img.save(path)
            paths.append(path)
            if size is None:
                size = img.size
            elif img.size != size:
                img = img.resize(size)
            tensors.append(torch.from_numpy(np.asarray(img).astype("float32") / 255.0))
        return (torch.stack(tensors), "\n".join(paths))


TEXT_MODELS = ["deepseek-v4.1-flash-1017-global", "qwen3.8-flash-global", "glm-5.3-global", "kimi-k3-global", "qwen3.8-max-global"]
SHOT_LIST, IMPROVE = "shot list (several shots in one clip)", "improve one prompt"
_SHOT_SYSTEM = (
    "You are a film director's assistant writing for an AI video model. Turn the user's idea into a shot list. "
    "Reply with JSON only, no other text: {\"style\": string, \"shots\": [{\"seconds\": integer, \"description\": string}]}. "
    "\"style\" is one sentence on the overall look, the characters and the setting, so every shot stays consistent. "
    "Use exactly the number of shots asked for, and the seconds must add up to exactly the total asked for. "
    "Each description is one or two sentences: who or what, the action, the camera (shot size and movement) and the light. "
    "Describe only what can be seen and heard. Write in the language of the user's idea."
)
_IMPROVE_SYSTEM = (
    "You are a film director's assistant writing for an AI video model. Rewrite the user's short idea as one clear, "
    "detailed prompt of 60 to 120 words for a clip of the stated length: the subject, the action in order, the setting, "
    "the camera (shot size and movement), the light and the mood. Describe only what can be seen and heard. "
    "Reply with the prompt only, no title and no quotes. Write in the language of the user's idea."
)


def _parse_shots(text, total, n_shots):
    t = text.strip()
    if "```" in t:
        t = t.split("```")[1]
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON in the reply")
    data = json.loads(t[a:b + 1])
    shots = [(max(1, int(round(float(x.get("seconds", 0) or 0)))), str(x.get("description", "")).strip())
             for x in data.get("shots", []) if str(x.get("description", "")).strip()]
    if not shots:
        raise ValueError("no shots in the reply")
    # make the seconds add up to the requested total
    diff = total - sum(s for s, _ in shots)
    i = 0
    while diff != 0 and i < 200:
        k = i % len(shots)
        if diff > 0:
            shots[k] = (shots[k][0] + 1, shots[k][1])
            diff -= 1
        elif shots[k][0] > 1:
            shots[k] = (shots[k][0] - 1, shots[k][1])
            diff += 1
        i += 1
    return str(data.get("style", "")).strip(), shots


class HashBidXShotWriter:
    """Turns a short idea into a ready prompt with a HashBidX text model: either a shot list with seconds
    (several shots in one clip) or one improved, detailed prompt. Connect `prompt` to a Video node's prompt
    and `total_seconds` to its duration."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "idea": ("STRING", {"multiline": True, "default": "", "placeholder": "Your idea in a sentence or two, e.g. a 15 second ad for a water bottle on a snowy mountain hike"}),
                "mode": ([SHOT_LIST, IMPROVE], {"default": SHOT_LIST}),
                "total_seconds": ("INT", {"default": 15, "min": 2, "max": 30}),
                "shots": ("INT", {"default": 3, "min": 1, "max": 6, "tooltip": "Number of shots (shot list mode)."}),
                "model": (TEXT_MODELS, {"default": TEXT_MODELS[0]}),
            },
            "optional": {
                "hashbidx": ("HASHBIDX_AUTH",),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF, "tooltip": "Changing it writes a new version; not sent to the API."}),
            },
        }

    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("prompt", "total_seconds")
    FUNCTION = "write"
    CATEGORY = "HashBidX"

    def write(self, idea, mode, total_seconds, shots, model, hashbidx=None, seed=0):
        if not idea.strip():
            raise HashBidXError("idea is required: describe the clip in a sentence or two.")
        key, base = _auth(hashbidx)
        client = Client(key, base)
        if mode == SHOT_LIST:
            n = max(1, min(int(shots), int(total_seconds)))
            user = "Idea: %s\nTotal length: %d seconds. Number of shots: %d." % (idea.strip(), total_seconds, n)
            system = _SHOT_SYSTEM
        else:
            user = "Idea: %s\nClip length: %d seconds." % (idea.strip(), total_seconds)
            system = _IMPROVE_SYSTEM
        last = None
        for _ in range(2):  # one retry if the reply cannot be read
            out = client.chat({"model": model, "stream": False, "temperature": 0.7, "max_tokens": 2000,
                               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            try:
                reply = (out["choices"][0]["message"].get("content") or "").strip()
            except Exception:
                reply = ""
            if not reply:
                last = "empty reply"
                continue
            if mode == IMPROVE:
                return {"ui": {"text": [reply]}, "result": (reply.strip('"'), int(total_seconds))}
            try:
                style, items = _parse_shots(reply, int(total_seconds), n)
            except Exception as e:
                last = str(e)
                continue
            lines, t = ([style] if style else []), 0
            for i, (secs, text) in enumerate(items):
                lines.append("Shot %d (%d-%d seconds): %s" % (i + 1, t, t + secs, text))
                t += secs
            prompt = "\n".join(lines)
            return {"ui": {"text": [prompt]}, "result": (prompt, t)}
        raise HashBidXError("The text model did not return a usable answer (%s). Press Run again or pick another model." % last)


def _join(paths, out_path):
    """Join clips end to end into one MP4 (H.264 + AAC), using the PyAV library that ships with ComfyUI."""
    from fractions import Fraction

    import av
    import numpy as np

    RATE = 44100
    with av.open(paths[0]) as c:
        v = c.streams.video[0]
        width, height = v.codec_context.width, v.codec_context.height
        fps = v.average_rate or Fraction(24, 1)
    has_audio = []
    for p in paths:
        with av.open(p) as c:
            has_audio.append(len(c.streams.audio) > 0)
    with av.open(out_path, "w") as out:
        ov = out.add_stream("libx264", rate=fps)
        ov.width, ov.height, ov.pix_fmt = width, height, "yuv420p"
        ov.options = {"crf": "16", "preset": "medium"}
        oa = None
        if any(has_audio):
            oa = out.add_stream("aac", rate=RATE)
            oa.layout = "stereo"
        n_frames, n_samples = 0, 0
        for p, audio in zip(paths, has_audio):
            count = 0
            with av.open(p) as c:
                for frame in c.decode(video=0):
                    frame = frame.reformat(width=width, height=height, format="yuv420p")
                    frame.pts = n_frames
                    frame.time_base = Fraction(fps.denominator, fps.numerator)
                    frame.pict_type = 0  # let the encoder choose
                    for packet in ov.encode(frame):
                        out.mux(packet)
                    n_frames += 1
                    count += 1
            if oa is None:
                continue
            want = int(round(count * RATE / float(fps)))  # samples that match this clip's picture length
            chunks = []
            if audio:
                resampler = av.AudioResampler(format="fltp", layout="stereo", rate=RATE)
                with av.open(p) as c:
                    for frame in c.decode(audio=0):
                        frame.pts = None
                        for r in resampler.resample(frame):
                            chunks.append(r.to_ndarray())
                    for r in resampler.resample(None):
                        chunks.append(r.to_ndarray())
            sound = np.concatenate(chunks, axis=1) if chunks else np.zeros((2, 0), dtype="float32")
            if sound.shape[1] < want:
                sound = np.concatenate([sound, np.zeros((2, want - sound.shape[1]), dtype="float32")], axis=1)
            sound = np.ascontiguousarray(sound[:, :want].astype("float32"))
            for i in range(0, want, 1024):
                af = av.AudioFrame.from_ndarray(np.ascontiguousarray(sound[:, i:i + 1024]), format="fltp", layout="stereo")
                af.sample_rate = RATE
                af.pts = n_samples + i
                af.time_base = Fraction(1, RATE)
                for packet in oa.encode(af):
                    out.mux(packet)
            n_samples += want
        for packet in ov.encode(None):
            out.mux(packet)
        if oa is not None:
            for packet in oa.encode(None):
                out.mux(packet)
    return out_path


NODE_CLASS_MAPPINGS = {
    "HashBidXAPIKey": HashBidXAPIKey,
    "HashBidXVideo": HashBidXVideo,
    "HashBidXCamera": HashBidXCamera,
    "HashBidXMultiShot": HashBidXMultiShot,
    "HashBidXReferencePack": HashBidXReferencePack,
    "HashBidXExtendEdit": HashBidXExtendEdit,
    "HashBidXImage": HashBidXImage,
    "HashBidXShotWriter": HashBidXShotWriter,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "HashBidXAPIKey": "HashBidX API Key",
    "HashBidXVideo": "HashBidX Video (Seedance / H3 / Wan)",
    "HashBidXCamera": "HashBidX Camera & Lens",
    "HashBidXMultiShot": "HashBidX Multi-Shot Prompt",
    "HashBidXReferencePack": "HashBidX Reference Pack",
    "HashBidXExtendEdit": "HashBidX Extend / Edit Clip",
    "HashBidXImage": "HashBidX Image",
    "HashBidXShotWriter": "HashBidX Shot Writer (idea to shots)",
}
