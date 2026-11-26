from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, hide_typed_keys

# Keep a key typed into the "HashBidX API Key" node out of saved videos, images and ComfyUI's run history.
try:
    from server import PromptServer

    PromptServer.instance.add_on_prompt_handler(hide_typed_keys)
except Exception as e:
    print("[HashBidX] typed keys are not hidden from saved files on this ComfyUI: %s" % e)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
