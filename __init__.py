from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, add_key_routes, hide_typed_keys

# Keep a key typed into the "HashBidX API Key" node out of saved videos, images and ComfyUI's run history.
try:
    from server import PromptServer

    PromptServer.instance.add_on_prompt_handler(hide_typed_keys)
except Exception as e:
    print("[HashBidX] typed keys are not hidden from saved files on this ComfyUI: %s" % e)

# The "HashBidX API Key" box shows whether a key is saved and has a "Verify and save key" button.
try:
    from server import PromptServer

    add_key_routes(PromptServer.instance)
except Exception as e:
    print("[HashBidX] the key box works on Run only, without its button and status line: %s" % e)

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

# Updating through the ComfyUI Manager can leave the template files of an older version in place, which shows
# old and new cards side by side. Remove the retired template files (exact names only) from this plug-in's own folder.
try:
    import os

    _retired = (
        "1 Save your HashBidX key",
        "2 Text to video",
        "3 Image to video",
        "4 Two-shot chain with camera presets",
        "5 First and last frame",
        "6 Keep a character or product",
        "7 Extend a clip",
        "8 Edit a clip",
        "9 Idea to shots to video",
        "10 Picture first, then video",
        "hashbidx_image_to_video",
        "hashbidx_text_to_video",
        "hashbidx_two_shot_chain",
    )
    _tpl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "example_workflows")
    for _base in _retired:
        for _ext in (".json", ".jpg"):
            _old = os.path.join(_tpl_dir, _base + _ext)
            if os.path.isfile(_old):
                try:
                    os.remove(_old)
                except OSError as e:
                    print("[HashBidX] could not remove the retired template %s: %s" % (_base + _ext, e))
except Exception as e:
    print("[HashBidX] could not tidy the template folder: %s" % e)

# Front-end helpers: model-linked options, the key shown as dots, the key box's button and status (web/hashbidx.js).
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
