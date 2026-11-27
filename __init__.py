from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, hide_typed_keys

# Keep a key typed into the "HashBidX API Key" node out of saved videos, images and ComfyUI's run history.
try:
    from server import PromptServer

    PromptServer.instance.add_on_prompt_handler(hide_typed_keys)
except Exception as e:
    print("[HashBidX] typed keys are not hidden from saved files on this ComfyUI: %s" % e)

# The templates open with sample stills, so they run before you pick your own pictures.
try:
    import os
    import shutil

    import folder_paths

    _src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_media")
    _dst_dir = folder_paths.get_input_directory()
    if not os.path.isdir(_src_dir):
        print("[HashBidX] the sample stills are missing from the plug-in folder: %s" % _src_dir)
    else:
        os.makedirs(_dst_dir, exist_ok=True)
        for _name in os.listdir(_src_dir):
            _dst = os.path.join(_dst_dir, _name)
            if _name.startswith("hashbidx_example_") and not os.path.exists(_dst):
                shutil.copyfile(os.path.join(_src_dir, _name), _dst)
except Exception as e:
    print("[HashBidX] could not place the sample stills in ComfyUI's input folder: %s" % e)

# Front-end helpers: model-linked options and the key shown as dots (web/hashbidx.js).
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
