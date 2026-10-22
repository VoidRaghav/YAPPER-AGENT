import pytest

from app.safety import check_line, detect_serious, sensitive_categories, trend_is_safe


@pytest.mark.parametrize(
    ("line", "category"),
    [
        ("aman's mom would be ashamed of this excuse", "family"),
        ("tera baap bhi late aata tha kya", "family"),
        ("bro is so fat the chair filed a complaint", "looks"),
        ("motu ne phir se pizza order kiya", "looks"),
        ("hairline retreating faster than his plans", "looks"),
        ("typical sardar move", "religion"),
        ("mandir jaake maafi maang", "religion"),
        ("ye toh bihari jaisa kaam hai", "caste_region"),
        ("brahmin energy", "caste_region"),
        ("gareeb aadmi ka iphone", "money_hardship"),
        ("bro is so broke he", "money_hardship"),
        ("depression era playlist", "mental_health"),
        ("psycho behaviour", "mental_health"),
        ("gay jokes incoming", "sexuality"),
        ("he'll be dead before he reaches", "grief"),
        ("hospital food is still better than your cooking", "health"),
    ],
)
def test_roasts_on_sensitive_topics_are_blocked(line, category):
    verdict = check_line(line, mode="roast")
    assert not verdict.ok
    assert verdict.reason == f"sensitive:{category}"


@pytest.mark.parametrize(
    "line",
    [
        "5 mins in aman time or actual time",
        "this is the 4th monday of this diet. it's a lifestyle at this point",
        "rahul has dropped a podcast episode. no one asked. everyone's listening",
        "says the guy whose last joke needed a 2 min explanation",
        "goa plan part 7 announced. same ending expected",
        "rasode mein kaun tha. everyone's a suspect",
        "25 din mein paisa double energy detected",
        "nobody asked but okay",
        "looks like aman is late again",
    ],
)
def test_spec_examples_pass(line):
    assert check_line(line, mode="roast").ok


@pytest.mark.parametrize("line", ["As an AI, I think you're late", "Great question! aman is late", "I'd be happy to roast him", "Certainly! here goes"])
def test_assistant_voice_is_blocked(line):
    assert check_line(line, mode="banter").reason == "persona"


@pytest.mark.parametrize("line", ["- point one\n- point two", "**bold** move", "# heading", "1. first"])
def test_markdown_is_blocked(line):
    assert check_line(line, mode="banter").reason == "markdown"


def test_length_and_lines():
    assert check_line("a " * 200, mode="banter").reason == "length"
    assert check_line("a\nb\nc\nd", mode="banter").reason == "length"


@pytest.mark.parametrize("line", ["lol that's rough", "rip bro 💀", "haha get well soon", "skill issue honestly"])
def test_no_jokes_in_support_mode(line):
    assert check_line(line, mode="support").reason == "joke_in_support"


@pytest.mark.parametrize("line", ["hospital ka khana > tera khana", "at least you got a day off", "look at the bright side", "kal phir try kar lena"])
def test_support_mode_rejects_roast_framing_and_non_supportive(line):
    assert not check_line(line, mode="support").ok


def test_support_mode_allows_caring_words():
    assert check_line("that sucks man. hope your dad recovers soon, we're here", mode="support").ok
    assert check_line("you prepped hard for this interview, one bad round doesn't erase that", mode="support").ok


def test_offlimits_and_protected_and_repeat():
    assert check_line("aman ka job hunt season 4", mode="roast", offlimits=["job hunt"]).reason.startswith("offlimits")
    assert check_line("bhai his exams again", mode="roast", offlimits=["exams"]).reason.startswith("offlimits")
    assert check_line("priya ka excuse level 100", mode="roast", protected=["Priya"]).reason == "protected:priya"
    assert check_line("5 min in aman time or real time", mode="roast", recent=["5 mins in aman time or actual time"]).reason == "repeat"


def test_profanity_toggle():
    assert check_line("chutiya plan hai", mode="banter").reason == "profanity"
    assert check_line("chutiya plan hai", mode="banter", allow_profanity=True).ok


@pytest.mark.parametrize(
    "text",
    [
        "my nani passed away last night",
        "yaar mood off hai, feeling low",
        "interview went badly guys",
        "papa hospital mein admit hain",
        "we broke up yesterday",
        "got laid off today",
        "bahut bura lag raha hai",
    ],
)
def test_serious_detector_fires(text):
    assert detect_serious(text)


@pytest.mark.parametrize("text", ["chalo pizza order karte hai", "aman is late again", "who's coming for the match", "this movie was dead boring lol"])
def test_serious_detector_ignores_banter(text):
    assert detect_serious(text) is None


def test_sensitive_categories_for_memory_flagging():
    assert "health" in sensitive_categories("aman's dad is in hospital since monday")
    assert sensitive_categories("aman supports rcb and is always late") == []


@pytest.mark.parametrize(
    ("text", "ok", "reason"),
    [
        ("moye moye reel trend takes over", True, ""),
        ("bridge collapse kills 5", False, "tragedy"),
        ("BJP rally memes flood X", False, "politics"),
        ("temple run nostalgia", False, "religion"),
        ("shark tank india judge says bhai kya kar raha hai tu", True, ""),
    ],
)
def test_trend_safety(text, ok, reason):
    verdict = trend_is_safe(text)
    assert verdict.ok is ok
    assert verdict.reason == reason
