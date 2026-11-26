"""Minimal HashBidX video API client (stdlib only, no extra dependencies).

API shape (OpenAI videos style):
  POST {base}/videos                 -> {"id", "status": "queued", ...}
  GET  {base}/videos/{id}            -> {"status": queued|in_progress|completed|failed, "progress", ...}
  GET  {base}/videos/{id}/content    -> mp4 bytes
"""
import base64
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://hashbidx.ai/v1"
USER_AGENT = "ComfyUI-HashBidX/0.4.2"
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
    """Node input wins, then env vars, then config.json next to this file."""
    cfg = _load_config()
    key = (api_key or "").strip() or os.environ.get("HASHBIDX_API_KEY", "").strip() or str(cfg.get("api_key", "")).strip()
    base = (base_url or "").strip() or os.environ.get("HASHBIDX_BASE_URL", "").strip() or str(cfg.get("base_url", "")).strip() or DEFAULT_BASE_URL
    if not key:
        raise HashBidXError(
            "No HashBidX API key. Paste it into the 'HashBidX API Key' node, or set HASHBIDX_API_KEY, "
            "or save it once with the 'HashBidX API Key' node's save_key option (stored in ComfyUI/user/hashbidx/config.json)."
        )
    return key, base.rstrip("/")


class Client:
    def __init__(self, api_key, base_url=DEFAULT_BASE_URL, timeout=120):
        self.key = api_key
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method, path, body=None, raw=False):
        url = self.base + "/" + path.lstrip("/")
        data = None
        headers = {"Authorization": "Bearer " + self.key, "User-Agent": USER_AGENT}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
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
            raise HashBidXError("HashBidX API %s %s -> HTTP %s %s" % (method, path, e.code, msg)) from None
        except urllib.error.URLError as e:
            raise HashBidXError("Cannot reach HashBidX API at %s: %s" % (url, e.reason)) from None

    def create_video(self, body):
        return self._request("POST", "videos", body)

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
                raise HashBidXError("Video task %s failed: %s" % (task_id, err or status))
            if time.monotonic() > deadline:
                raise HashBidXError(
                    "Video task %s still '%s' after %ss. It may still finish; the task id is %s."
                    % (task_id, status, max_wait_seconds, task_id)
                )
            time.sleep(poll_seconds)


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
