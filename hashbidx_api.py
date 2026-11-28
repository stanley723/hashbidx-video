"""Minimal HashBidX video API client (stdlib only, no extra dependencies).

API shape (OpenAI videos style):
  POST {base}/videos                 -> {"id", "status": "queued", ...}
  GET  {base}/videos/{id}            -> {"status": queued|in_progress|completed|failed, "progress", ...}
  GET  {base}/videos/{id}/content    -> mp4 bytes
"""
import base64
import io
import json
import re
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://hashbidx.ai/v1"
USER_AGENT = "ComfyUI-HashBidX/0.5.3"
PLUGIN_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def user_config_path():
    """ComfyUI/user/hashbidx/config.json survives plug-in updates; falls back to the plug-in folder."""
    try:
        import folder_paths
        return os.path.join(folder_paths.get_user_directory(), "hashbidx", "config.json")
    except Exception:
        return None


def _load_config():
    for path in (user_config_path(), PLUGIN_CONFIG):
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


class HashBidXError(RuntimeError):
    pass


def resolve_credentials(api_key="", base_url=""):
    """Node input wins, then the key saved on this computer (ComfyUI/user/hashbidx/config.json)."""
    cfg = _load_config()
    key = (api_key or "").strip() or str(cfg.get("api_key", "")).strip()
    base = (base_url or "").strip() or str(cfg.get("base_url", "")).strip() or DEFAULT_BASE_URL
    if not key:
        raise HashBidXError(
            "No HashBidX API key yet. Open Templates > hashbidx-video > '1 Save your HashBidX key', paste your key and press Run once. "
            "Or paste the key into a 'HashBidX API Key' node connected to this node."
        )
    return key, base.rstrip("/")


def _check_hint(msg):
    """The model checks every finished clip and its sound, and words every refusal as a copyright matter."""
    low = msg.lower()
    if "copyright" not in low and "sensitive" not in low and "content_policy" not in low:
        return ""
    if "audio" in low:
        return ("\nWhat this means: the model's own check refused the sound it generated for this clip. Nothing was "
                "charged. Run it again, or set audio to off.")
    return ("\nWhat this means: the model's own content check refused this clip. Nothing was charged. Run it again or "
            "change the prompt.")


def _input_check_hint(msg):
    """The model also checks the prompt text and the pictures before it starts. Say that in plain words."""
    low = msg.lower()
    if "identified your task as video" in low:
        return None
    if "content_policy" not in low and "sensitive" not in low:
        return None
    if "input image" in low or "input video" in low:
        what, fix = "a picture or clip you gave it", "Use another picture or clip"
    elif "input text" in low or "prompt" in low:
        what, fix = "the wording of the prompt", "Change the wording"
    else:
        what, fix = "what you sent", "Change the prompt or the pictures"
    return ("The model's own content check refused %s before anything was generated. Nothing was charged. %s or pick "
            "another model, then run again.\nDetails from the model: %s" % (what, fix, msg))


def _spec_hint(msg):
    """The API names what a model offers when a setting does not fit it. Say that in plain words."""
    if "unsupported_video_spec" not in msg:
        return None
    m = re.search(r"model '([^']+)' supports (.*?); got (.*)$", msg, re.S)
    if not m:
        return None
    got = m.group(3).strip()
    for raw, plain in (("metadata.generate_audio=false", "audio off"), ("metadata.generate_audio=true", "audio on"),
                       ("metadata.resolution=", "resolution "), ("metadata.ratio=", "ratio "), ("metadata.", "")):
        got = got.replace(raw, plain)
    got = re.sub(r"^seconds=(\d+)$", r"\1 seconds", got)
    return ("%s does not offer this setting: %s. It offers %s. Change the setting or pick another model. "
            "Nothing was charged." % (m.group(1), got, m.group(2).strip()))


class Client:
    def __init__(self, api_key, base_url=DEFAULT_BASE_URL, timeout=120):
        self.key = api_key
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method, path, body=None, raw=False, timeout=None):
        url = self.base + "/" + path.lstrip("/")
        data = None
        headers = {"Authorization": "Bearer " + self.key, "User-Agent": USER_AGENT}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                payload = resp.read()
                if raw:
                    return payload, resp.headers.get("Content-Type", "")
                return json.loads(payload.decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            msg = text
            try:
                err = json.loads(text).get("error", {})
                if isinstance(err, dict):
                    msg = "%s: %s" % (err.get("code") or err.get("type") or e.code, err.get("message") or text)
            except Exception:
                pass
            spec = _spec_hint(msg)
            if spec:
                raise HashBidXError(spec) from None
            refused = _input_check_hint(msg)
            if refused:
                raise HashBidXError(refused) from None
            if method == "POST" and (e.code in (502, 503, 504) or "upstream_unavailable" in msg):
                raise HashBidXError("This model is temporarily unavailable. It is not about your prompt or your settings, and "
                                    "nothing was charged. Wait a few minutes and run again, or pick another model for now."
                                    "\nDetails: HTTP %s %s" % (e.code, msg)) from None
            raise HashBidXError("HashBidX API %s %s -> HTTP %s %s" % (method, path, e.code, msg)) from None
        except urllib.error.URLError as e:
            raise HashBidXError("Cannot reach HashBidX API at %s: %s" % (url, e.reason)) from None

    def create_video(self, body):
        return self._request("POST", "videos", body)

    def create_image(self, body):
        """POST /images/generations (OpenAI images style). Can take a minute or more."""
        return self._request("POST", "images/generations", body, timeout=600)

    def chat(self, body):
        """POST /chat/completions (OpenAI chat style), not streamed."""
        return self._request("POST", "chat/completions", body, timeout=300)

    def video_link(self, task_id):
        """Temporary web link of a finished clip, as reported by the task (used to extend or edit that clip)."""
        info = self.get_video(task_id)
        status = str(info.get("status", "")).lower()
        if status not in ("completed", "succeeded", "success"):
            raise HashBidXError("Clip %s is not finished yet (status: %s)." % (task_id, status or "unknown"))
        content = info.get("content") if isinstance(info.get("content"), dict) else {}
        url = info.get("video_url") or content.get("video_url") or info.get("url")
        if not url:
            raise HashBidXError(
                "The API did not return a web link for clip %s, so it cannot be used as a source. "
                "Put a public https link to the clip into source_video_url instead." % task_id)
        return url, info

    def get_video(self, task_id):
        return self._request("GET", "videos/" + urllib.parse.quote(task_id, safe=""))

    def download_video(self, task_id):
        data, ctype = self._request("GET", "videos/" + urllib.parse.quote(task_id, safe="") + "/content", raw=True)
        if len(data) < 1000 or b"ftyp" not in data[:64]:
            raise HashBidXError("Downloaded file is not a valid MP4 (%d bytes, %s)" % (len(data), ctype))
        return data

    def wait(self, task_id, poll_seconds=5, max_wait_seconds=1200, on_progress=None):
        deadline = time.monotonic() + max_wait_seconds
        while True:
            info = self.get_video(task_id)
            status = str(info.get("status", "")).lower()
            if on_progress:
                on_progress(info)
            if status in ("completed", "succeeded", "success"):
                return info
            if status in ("failed", "error", "cancelled", "canceled", "expired") or info.get("error"):
                err = info.get("error")
                if isinstance(err, dict):
                    err = err.get("message") or json.dumps(err)
                raise HashBidXError("Video task %s failed: %s%s" % (task_id, err or status, _check_hint(str(err or ""))))
            if time.monotonic() > deadline:
                raise HashBidXError(
                    "Video task %s still '%s' after %ss. It may still finish; the task id is %s."
                    % (task_id, status, max_wait_seconds, task_id)
                )
            time.sleep(poll_seconds)


def download_url(url, timeout=300):
    """Fetch a result file (image) from the link the API returned."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        raise HashBidXError("Could not download the result: %s" % e) from None


def image_tensor_to_data_uri(image):
    """ComfyUI IMAGE tensor [B,H,W,C] float 0..1 -> PNG data URI (first frame of batch)."""
    import numpy as np
    from PIL import Image

    arr = image[0].cpu().numpy() if hasattr(image[0], "cpu") else np.asarray(image[0])
    arr = (np.clip(arr, 0, 1) * 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def image_file_to_data_uri(path, max_side=2048):
    """Image file -> JPEG/PNG data URI, downscaled so the longest side is at most max_side."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(path))
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    if img.mode in ("RGBA", "LA", "P"):
        img.convert("RGBA").save(buf, format="PNG")
        mime = "image/png"
    else:
        img.convert("RGB").save(buf, format="JPEG", quality=92)
        mime = "image/jpeg"
    return "data:%s;base64,%s" % (mime, base64.b64encode(buf.getvalue()).decode("ascii"))
