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

LOOKS = ("natural", "warm", "cool", "moody", "vibrant", "bw", "clean")
TRANSITIONS = ("fade", "slide", "zoom", "wipe", "cut")
PACES = ("slow", "medium", "fast")


@dataclass
class EditPlan:
    format: str = "reel"
    duration: float = 15.0          # target length of the montage, seconds (end card is extra)
    look: str = "natural"
    transition: str = "fade"
    pace: str = "medium"
    speed: float = 1.0              # <1 = slow motion for video clips

    title: str = ""                 # big text over the opening shot
    product: str = ""               # product name (end card + price tag)
    price: str = ""                 # e.g. "$29" or "₹1,499"
    cta: str = "Shop now"           # end-card call to action
    handle: str = ""                # e.g. "@lorven.interio"
    captions: list[str] = field(default_factory=list)  # short lines shown over the middle shots

    brand_color: str = "#111111"    # end card + price tag background
    text_color: str = "#FFFFFF"
    end_card: bool = True
    drop_blurry: bool = True

    def __post_init__(self) -> None:
        self.format = self.format if self.format in FORMATS else "reel"
        self.look = self.look if self.look in LOOKS else "natural"
        self.transition = self.transition if self.transition in TRANSITIONS else "fade"
        self.pace = self.pace if self.pace in PACES else "medium"
        self.duration = float(min(max(self.duration, 5.0), 90.0))
        self.speed = float(min(max(self.speed, 0.25), 2.0))
        self.brand_color = _hex(self.brand_color, "#111111")
        self.text_color = _hex(self.text_color, "#FFFFFF")
        self.captions = [str(c)[:60] for c in (self.captions or [])][:6]
        for name in ("title", "product", "price", "cta", "handle"):
            setattr(self, name, str(getattr(self, name) or "").strip()[:60])

    @property
    def size(self) -> tuple[int, int]:
        return FORMATS[self.format]

    @property
    def shot_length(self) -> float:
        return {"slow": 3.2, "medium": 2.3, "fast": 1.5}[self.pace]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EditPlan":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known and v is not None})


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
