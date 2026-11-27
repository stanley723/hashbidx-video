from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, hide_typed_keys

# Keep a key typed into the "HashBidX API Key" node out of saved videos, images and ComfyUI's run history.
try:
    from server import PromptServer

    PromptServer.instance.add_on_prompt_handler(hide_typed_keys)
except Exception as e:
    print("[HashBidX] typed keys are not hidden from saved files on this ComfyUI: %s" % e)

# Template "3 Image to video" opens with a sample still, so it runs before you pick your own picture.
try:
    import os
    import shutil

    import folder_paths

    _src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_media", "hashbidx_example_still.jpg")
    _dst = os.path.join(folder_paths.get_input_directory(), "hashbidx_example_still.jpg")
    if not os.path.exists(_src):
        print("[HashBidX] the sample still is missing from the plug-in folder: %s" % _src)
    elif not os.path.exists(_dst):
        os.makedirs(os.path.dirname(_dst), exist_ok=True)
        shutil.copyfile(_src, _dst)
except Exception as e:
    print("[HashBidX] could not place the sample still in ComfyUI's input folder: %s" % e)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
