"""The edit plan: everything the renderer needs to know, in one object.

Both planners (simple keyword mode and Claude mode) produce an EditPlan.
The renderer only ever reads an EditPlan, so it never has to understand
the user's free-text description.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

FORMATS = {
    "reel": (1080, 1920),      # 9:16 Instagram Reels / TikTok / Shorts
    "square": (1080, 1080),    # 1:1 feed post
    "portrait": (1080, 1350),  # 4:5 feed post
    "youtube": (1920, 1080),   # 16:9
}

# Color filters (CapCut "Filters").
LOOKS = ("natural", "warm", "cool", "moody", "vibrant", "bw", "clean",
         "vintage", "film", "cinematic", "golden", "pastel", "noir", "faded", "vivid")

# Transitions. Names CapCut users know map onto ffmpeg's xfade transitions or
# onto custom ones drawn frame by frame (see transitions.py).
NATIVE_TRANSITIONS = {
    "fade": "fade", "dissolve": "dissolve", "fade_black": "fadeblack", "flash": "fadewhite",
    "slide_left": "slideleft", "slide_right": "slideright", "slide_up": "slideup", "slide_down": "slidedown",
    "push_left": "smoothleft", "push_right": "smoothright", "push_up": "smoothup", "push_down": "smoothdown",
    "wipe_left": "wipeleft", "wipe_right": "wiperight", "wipe_up": "wipeup", "wipe_down": "wipedown",
    "cover_left": "coverleft", "cover_right": "coverright", "reveal_left": "revealleft", "reveal_right": "revealright",
    "zoom": "zoomin", "circle_open": "circleopen", "circle_close": "circleclose", "radial": "radial",
    "pixelize": "pixelize", "blur": "hblur", "squeeze": "squeezeh", "slice": "hlslice", "wind": "hlwind",
    "door_open": "vertopen", "door_close": "vertclose", "diagonal": "diagtl", "grayscale_fade": "fadegrays",
}
CUSTOM_TRANSITIONS = ("spin", "spin_left", "flip", "flip_up", "zoom_in", "zoom_out", "whip_left",
                      "whip_right", "whip_up", "glitch", "shake", "cube_left", "cube_right")
TRANSITIONS = ("cut", "mixed") + tuple(NATIVE_TRANSITIONS) + CUSTOM_TRANSITIONS
ALIASES = {"slide": "slide_left", "wipe": "wipe_left", "whip": "whip_left", "cube": "cube_left",
           "turn": "flip", "rotate": "spin", "white_flash": "flash", "dip_to_black": "fade_black",
           "crossfade": "fade", "push": "push_left", "zoom_blur": "zoom_in", "none": "cut", "hard_cut": "cut"}

# Effects (CapCut "Effects"). *_on_beat ones need music.
EFFECTS = ("vignette", "grain", "letterbox", "shake", "retro", "glow", "sharpen",
           "pulse_on_beat", "flash_on_beat")

PACES = ("slow", "medium", "fast")
TEXT_STYLES = ("shadow", "outline", "box", "neon")
TEXT_ANIMATIONS = ("fade", "slide", "words", "typewriter")
POSITIONS = ("top", "center", "bottom")


def norm_transition(name: str) -> str:
    n = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    n = ALIASES.get(n, n)
    return n if n in TRANSITIONS else "fade"


@dataclass
class EditPlan:
    format: str = "reel"
    duration: float = 15.0          # target length of the montage, seconds (end card is extra)
    look: str = "natural"
    transition: str = "fade"        # default for every cut; "mixed" varies them
    transition_duration: float = 0.5
    cut_transitions: list[dict] = field(default_factory=list)  # [{"cut": 3, "transition": "spin"}]
    pace: str = "medium"
    speed: float = 1.0              # <1 = slow motion for every video clip
    beat_sync: bool = False         # land cuts on the music's beats

    # Adjustments, -1..1 (0 = unchanged)
    brightness: float = 0.0
    contrast: float = 0.0
    saturation: float = 0.0
    warmth: float = 0.0
    effects: list[str] = field(default_factory=list)
    fit: str = "crop"               # "crop" fills the frame; "blur" shows the whole shot on a blurred copy

    # Per-clip edits, keyed by file name
    clip_speeds: dict[str, float] = field(default_factory=dict)
    reverse_files: list[str] = field(default_factory=list)
    mirror_files: list[str] = field(default_factory=list)

    # Audio
    keep_audio: bool = False        # keep the clips' own sound
    music_volume: float = 0.8

    # Text
    title: str = ""                 # big text over the opening shot
    product: str = ""               # product name (end card + price tag)
    price: str = ""                 # e.g. "$29" or "₹1,499"
    cta: str = "Shop now"           # end-card call to action
    handle: str = ""                # e.g. "@lorven.interio"
    captions: list[str] = field(default_factory=list)  # short lines spread over the middle shots
    texts: list[dict] = field(default_factory=list)    # [{"text", "at", "duration", "position"}]
    text_style: str = "shadow"
    text_animation: str = "fade"
    caption_position: str = "bottom"

    brand_color: str = "#111111"    # end card + price tag background
    text_color: str = "#FFFFFF"
    end_card: bool = True
    drop_blurry: bool = True

    def __post_init__(self) -> None:
        self.format = self.format if self.format in FORMATS else "reel"
        self.look = self.look if self.look in LOOKS else "natural"
        self.transition = norm_transition(self.transition)
        self.pace = self.pace if self.pace in PACES else "medium"
        self.duration = float(min(max(_num(self.duration, 15), 5.0), 90.0))
        self.transition_duration = float(min(max(_num(self.transition_duration, 0.5), 0.2), 1.2))
        self.speed = float(min(max(_num(self.speed, 1), 0.25), 3.0))
        for k in ("brightness", "contrast", "saturation", "warmth"):
            setattr(self, k, float(min(max(_num(getattr(self, k), 0), -1.0), 1.0)))
        self.music_volume = float(min(max(_num(self.music_volume, 0.8), 0.0), 1.0))
        self.fit = self.fit if self.fit in ("crop", "blur") else "crop"
        self.effects = list(dict.fromkeys(e for e in (self.effects or []) if e in EFFECTS))
        self.text_style = self.text_style if self.text_style in TEXT_STYLES else "shadow"
        self.text_animation = self.text_animation if self.text_animation in TEXT_ANIMATIONS else "fade"
        self.caption_position = self.caption_position if self.caption_position in POSITIONS else "bottom"
        self.brand_color = _hex(self.brand_color, "#111111")
        self.text_color = _hex(self.text_color, "#FFFFFF")
        self.captions = [str(c)[:60] for c in (self.captions or [])][:8]
        for name in ("title", "product", "price", "cta", "handle"):
            setattr(self, name, str(getattr(self, name) or "").strip()[:60])

        cuts = []
        for c in self.cut_transitions or []:
            if not isinstance(c, dict) or not c.get("transition"):
                continue
            item = {"transition": norm_transition(c["transition"])}
            if c.get("cut") is not None:
                item["cut"] = int(_num(c["cut"], 1))
            elif c.get("at") is not None:
                item["at"] = float(_num(c["at"], 0))
            else:
                continue
            cuts.append(item)
        self.cut_transitions = cuts

        self.clip_speeds = {str(k): float(min(max(_num(v, 1), 0.25), 3.0))
                            for k, v in (self.clip_speeds or {}).items()}
        self.reverse_files = [str(f) for f in (self.reverse_files or [])]
        self.mirror_files = [str(f) for f in (self.mirror_files or [])]

        texts = []
        for t in self.texts or []:
            if isinstance(t, dict) and str(t.get("text", "")).strip():
                texts.append({
                    "text": str(t["text"]).strip()[:80],
                    "at": max(0.0, float(_num(t.get("at"), 0))),
                    "duration": float(min(max(_num(t.get("duration"), 2.5), 0.5), 30)),
                    "position": t.get("position") if t.get("position") in POSITIONS else "center",
                })
        self.texts = texts[:10]

    @property
    def size(self) -> tuple[int, int]:
        return FORMATS[self.format]

    @property
    def shot_length(self) -> float:
        return {"slow": 3.2, "medium": 2.3, "fast": 1.5}[self.pace]

    def speed_for(self, name: str) -> float:
        return self.clip_speeds.get(name, self.speed)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EditPlan":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known and v is not None})


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _hex(value: str, default: str) -> str:
    v = str(value or "").strip()
    if not v.startswith("#"):
        v = "#" + v
    if len(v) == 4:
        v = "#" + "".join(c * 2 for c in v[1:])
    try:
        int(v[1:], 16)
    except ValueError:
        return default
    return v.upper() if len(v) == 7 else default
