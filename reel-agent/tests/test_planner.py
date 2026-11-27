from types import SimpleNamespace

from reel_agent.plan import EditPlan
from reel_agent.planner import claude_plan, make_plan, simple_plan


def test_simple_plan_reads_the_basics():
    p = simple_plan('15 sec warm reel for our new soy candle, $24, shop now, @glowco, navy brand color')
    assert p.format == "reel" and p.size == (1080, 1920)
    assert p.duration == 15
    assert p.look == "warm"
    assert p.price == "$24"
    assert p.product == "Soy Candle"
    assert p.title == "New: Soy Candle"
    assert p.cta == "Shop Now"
    assert p.handle == "@glowco"
    assert p.brand_color == "#1B2A49"


def test_simple_plan_other_formats_and_moods():
    p = simple_plan("square post, moody, hard cuts, Rs 1499, link in bio, 20 seconds")
    assert (p.format, p.look, p.transition, p.price, p.cta, p.duration) == \
        ("square", "moody", "cut", "₹1499", "Link In Bio", 20)
    assert simple_plan("youtube video, upbeat").format == "youtube"
    assert simple_plan("upbeat and energetic").pace == "fast"
    assert simple_plan("black and white, slow motion").look == "bw"
    assert simple_plan("slow motion").speed == 0.5
    assert simple_plan('"Fresh Drop" vibrant').title == "Fresh Drop"


def test_plan_sanitises_bad_values():
    p = EditPlan.from_dict({"format": "imax", "duration": 999, "look": "neon",
                            "brand_color": "nope", "speed": 9, "unknown": 1})
    assert (p.format, p.duration, p.look, p.brand_color, p.speed) == ("reel", 90, "natural", "#111111", 2.0)
    assert EditPlan(brand_color="fff").brand_color == "#FFFFFF"


class FakeClient:
    def __init__(self, plan):
        self.plan, self.calls = plan, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=self.plan)])


def test_claude_plan_uses_tool_output():
    fake = FakeClient({"format": "reel", "duration": 12, "look": "warm", "transition": "slide",
                       "pace": "fast", "title": "Autumn glow", "cta": "Shop now", "price": "$24",
                       "brand_color": "#6D4C41", "text_color": "#FFFFFF", "captions": ["Hand-poured"]})
    p = claude_plan("cozy autumn vibe for the candle", ["a.jpg", "b.mp4"], client=fake)
    assert (p.look, p.transition, p.title, p.captions) == ("warm", "slide", "Autumn glow", ["Hand-poured"])
    call = fake.calls[0]
    assert call["tool_choice"]["name"] == "make_plan"
    assert "b.mp4" in call["messages"][0]["content"]


def test_make_plan_defaults_to_simple_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("REEL_AGENT_MODE", raising=False)
    _, mode = make_plan("warm reel")
    assert mode == "simple"
