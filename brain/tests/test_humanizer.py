import random
import statistics

from app.config import Settings
from app.nodes.humanize import clean, count_emoji, reply_delay

NAMES = ["yapper"]


def test_strips_prefix_quotes_period_and_lowercases():
    assert clean('Yapper: "Haha Bro, You Are LATE."', NAMES) == "haha bro, you are LATE"


def test_keeps_acronyms_and_urls():
    assert clean("RCB fans in shambles https://X.com/Abc", NAMES) == "RCB fans in shambles https://X.com/Abc"


def test_single_emoji_max():
    out = clean("lol 😂😂🔥💀", NAMES)
    assert count_emoji(out) == 1
    assert out.startswith("lol")


def test_markdown_and_dashes_removed():
    assert clean("**bhai** — kya scene hai", NAMES) == "bhai, kya scene hai"
    assert clean("- point\n- second", NAMES) == "point\nsecond"


def test_ellipsis_kept():
    assert clean("aman time...", NAMES) == "aman time..."


def test_delay_bounds_and_scaling():
    s = Settings(_env_file=None)
    rng = random.Random(1)
    short = [reply_delay("ok", s, rng) for _ in range(300)]
    long = [reply_delay("x" * 160, s, rng) for _ in range(300)]
    for d in short + long:
        assert s.reply_delay_min <= d <= s.reply_delay_max
    assert statistics.mean(long) > statistics.mean(short) + 5
    assert all(2 <= reply_delay("", s, rng, "reaction") <= 9 for _ in range(100))
