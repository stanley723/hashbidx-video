"""Camera / shot presets. The video APIs (Seedance 2.x, MiniMax H3, Wan 3.0) have no camera parameters,
so presets are written into the prompt as plain cinematography language, which these models follow."""

SHOT_SIZES = {
    "none": "",
    "extreme wide shot": "Extreme wide shot",
    "wide shot": "Wide shot",
    "full shot": "Full shot",
    "medium shot": "Medium shot",
    "medium close-up": "Medium close-up",
    "close-up": "Close-up",
    "extreme close-up": "Extreme close-up",
    "over-the-shoulder": "Over-the-shoulder shot",
    "aerial / drone": "Aerial drone shot",
}

CAMERA_MOVES = {
    "none": "",
    "static (locked off)": "the camera stays completely still, locked off on a tripod",
    "slow push in": "the camera slowly pushes in toward the subject",
    "pull out": "the camera slowly pulls back away from the subject",
    "pan left": "the camera pans smoothly to the left",
    "pan right": "the camera pans smoothly to the right",
    "tilt up": "the camera tilts up slowly",
    "tilt down": "the camera tilts down slowly",
    "tracking (follow subject)": "the camera tracks alongside the subject, keeping it in frame",
    "orbit around subject": "the camera orbits around the subject in a smooth arc",
    "crane up": "the camera cranes up and rises above the scene",
    "crane down": "the camera cranes down from above",
    "dolly zoom (vertigo)": "dolly zoom: the camera moves back while zooming in, warping the background",
    "handheld": "handheld camera with natural, subtle shake",
    "whip pan": "a fast whip pan with motion blur",
    "fpv drone fly-through": "an FPV drone flies fast through the scene",
}

LENS_LOOKS = {
    "none": "",
    "clean digital cinema": "shot on a modern digital cinema camera, clean and sharp",
    "35mm film": "shot on 35mm film, natural grain, filmic color",
    "anamorphic": "anamorphic lens, wide aspect feel, oval bokeh and horizontal lens flares",
    "vintage lens": "vintage lens with soft edges and gentle halation",
    "16mm documentary": "16mm documentary look, visible grain, handheld realism",
    "8mm home movie": "Super 8 home-movie look, heavy grain and warm faded color",
    "wide angle 24mm": "24mm wide-angle lens",
    "portrait 85mm": "85mm portrait lens",
    "telephoto 200mm": "200mm telephoto lens with compressed background",
    "macro": "macro lens, extreme detail",
}

DEPTHS = {
    "none": "",
    "shallow (f/1.4)": "very shallow depth of field, f/1.4, background softly blurred",
    "medium (f/4)": "moderate depth of field, f/4",
    "deep focus (f/11)": "deep focus, f/11, everything sharp",
}

LIGHTING = {
    "none": "",
    "golden hour": "warm golden-hour sunlight",
    "blue hour": "cool blue-hour light",
    "soft overcast": "soft, even overcast light",
    "high-key studio": "bright high-key studio lighting",
    "low-key / noir": "low-key, high-contrast noir lighting",
    "neon night": "neon-lit night with colorful reflections",
    "candlelight": "warm flickering candlelight",
}


def camera_text(shot_size="none", camera_move="none", lens_look="none", depth_of_field="none", lighting="none"):
    parts = []
    s = SHOT_SIZES.get(shot_size, "")
    m = CAMERA_MOVES.get(camera_move, "")
    if s and m:
        parts.append("%s, %s." % (s, m))
    elif s:
        parts.append("%s." % s)
    elif m:
        parts.append(m[0].upper() + m[1:] + ".")
    for table, key in ((LENS_LOOKS, lens_look), (DEPTHS, depth_of_field), (LIGHTING, lighting)):
        t = table.get(key, "")
        if t:
            parts.append(t[0].upper() + t[1:] + ".")
    return " ".join(parts)


def apply_camera(prompt, **kw):
    extra = camera_text(**kw)
    if not extra:
        return prompt
    return "%s\n\nCamera: %s" % (prompt.rstrip(), extra)
