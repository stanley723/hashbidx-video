"""Camera, look and pacing presets. The video APIs (Seedance 2.x, MiniMax H3, Wan 3.0) have no camera parameters,
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


GENRES = {
    "none": "",
    "action": "Genre: action film, kinetic and intense",
    "epic": "Genre: epic, grand scale and sweeping scope",
    "drama": "Genre: drama, intimate and emotional",
    "comedy": "Genre: comedy, light and playful tone",
    "horror": "Genre: horror, tense and unsettling",
    "documentary": "Genre: documentary, observational and real",
    "commercial / ad": "Genre: premium TV commercial, polished and product-focused",
    "music video": "Genre: music video, stylish and rhythmic",
    "anime": "Genre: anime, hand-drawn Japanese animation style",
}

ERAS = {
    "none": "",
    "1950s Technicolor": "1950s Technicolor look, saturated primary colors",
    "1970s film": "1970s film stock, warm tones, soft halation and grain",
    "1980s VHS": "1980s VHS look, soft detail, scan lines and color bleed",
    "1990s 35mm": "1990s 35mm film look, natural contrast and fine grain",
    "2000s digital": "early 2000s digital video look",
    "modern": "modern high-end digital cinema look",
}

COLOR_GRADES = {
    "none": "",
    "teal and orange": "teal-and-orange color grade",
    "warm": "warm color grade with amber highlights",
    "cool": "cool color grade with blue shadows",
    "desaturated (bleach bypass)": "desaturated bleach-bypass color grade",
    "black and white": "black and white",
    "pastel": "soft pastel color palette",
    "vivid high contrast": "vivid, high-contrast color grade",
}

PACING = {
    "none": "",
    "calm": "Calm pacing with slow, unhurried movement",
    "dynamic": "Dynamic pacing with energetic movement",
    "chaotic": "Chaotic, frantic pacing",
    "single continuous take": "One continuous take with no cuts",
}


def camera_text(shot_size="none", camera_move="none", lens_look="none", depth_of_field="none", lighting="none",
                second_move="none", genre="none", era_look="none", color_grade="none", pacing="none"):
    parts = []
    s = SHOT_SIZES.get(shot_size, "")
    m = CAMERA_MOVES.get(camera_move, "")
    m2 = CAMERA_MOVES.get(second_move, "")
    if m and m2:
        m = "%s, then %s" % (m, m2)
    elif m2:
        m = m2
    if s and m:
        parts.append("%s, %s." % (s, m))
    elif s:
        parts.append("%s." % s)
    elif m:
        parts.append(m[0].upper() + m[1:] + ".")
    for table, key in ((LENS_LOOKS, lens_look), (DEPTHS, depth_of_field), (LIGHTING, lighting),
                       (ERAS, era_look), (COLOR_GRADES, color_grade), (GENRES, genre), (PACING, pacing)):
        t = table.get(key, "")
        if t:
            parts.append(t[0].upper() + t[1:] + ".")
    return " ".join(parts)


def apply_camera(prompt, **kw):
    extra = camera_text(**kw)
    if not extra:
        return prompt
    return "%s\n\nCamera: %s" % (prompt.rstrip(), extra)
