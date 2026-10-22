import re
from dataclasses import dataclass
from difflib import SequenceMatcher


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(patterns) + r")(?![a-z0-9])", re.IGNORECASE)


SENSITIVE: dict[str, re.Pattern[str]] = {
    "family": _rx(
        r"mom+y?", r"moms?", r"mother", r"maa", r"mummy", r"mumm?i", r"mamm?i", r"papa", r"dad+y?", r"dads?",
        r"father", r"baap", r"sister", r"sis", r"behe?n", r"bahe?n", r"didi", r"wife", r"biwi", r"husband", r"pati",
        r"patni", r"chach[aie]", r"mama ?ji", r"mami", r"mausi", r"nani", r"dadi", r"bua", r"parents?", r"ghar ?wal[aei]",
        r"family", r"khandan", r"your (?:bro|brother)", r"tera bhai", r"papa ki pari", r"in-?laws?", r"sasural", r"saas",
    ),
    "health": _rx(
        r"cancer", r"tumou?r", r"diabet\w*", r"covid", r"corona", r"dengue", r"malaria", r"typhoid", r"hospital\w*",
        r"icu", r"surgery", r"chemo\w*", r"disease", r"bimari", r"beemar\w*", r"bimaar\w*", r"illness", r"fever",
        r"bukhar", r"injur\w*", r"fracture", r"disab\w*", r"handicap\w*", r"andh[aei]", r"langd[aei]", r"behr[aei]",
        r"autis\w*", r"retard\w*", r"heart attack", r"brain stroke", r"paralys\w*", r"pregnan\w*", r"miscarriage",
        r"period cramps", r"infertil\w*", r"asthma", r"medicine", r"dawai", r"stammer\w*", r"hakla",
    ),
    "mental_health": _rx(
        r"depress\w*", r"anxiety", r"anxious", r"panic attacks?", r"therap\w*", r"psychiatr\w*", r"bipolar", r"ocd",
        r"adhd", r"schizo\w*", r"psycho", r"mental(?:ly)? (?:ill|issue|issues|health|case)", r"suicid\w*",
        r"self[- ]harm", r"kill (?:my|him|her|your|them)sel(?:f|ves)", r"trauma\w*", r"insomnia", r"eating disorder",
        r"pagal ?khana", r"mental hospital",
    ),
    "looks": _rx(
        r"fat", r"fatty", r"fatso", r"obese", r"overweight", r"chubby", r"mot[aiu]", r"motu", r"skinny", r"patl[aiu]",
        r"ugly", r"kaal[aiu]", r"kalu", r"kallu", r"dark[- ]skinned", r"fair skin", r"fairness", r"gor[aie]",
        r"takl[aiu]", r"bald\w*", r"ganja", r"hairline", r"chhotu", r"tingu", r"naat[aei]", r"short(?:y| guy| height)",
        r"height", r"lambu", r"shakal", r"thobd[aei]", r"chehr[ae]", r"pimples?", r"acne", r"nose", r"naak", r"teeth",
        r"daant", r"belly", r"tond", r"weight", r"body", r"jawline", r"looks like a",
    ),
    "religion": _rx(
        r"hindu\w*", r"muslim\w*", r"musalm\w*", r"islam\w*", r"christian\w*", r"isai", r"sikh\w*", r"sardar(?:ji)?",
        r"jain (?:food|khana|hai)", r"buddhis\w*", r"parsi", r"jews?", r"jewish", r"religio\w*", r"dharm\w*",
        r"mazhab\w*", r"mandir", r"temple", r"masjid", r"mosque", r"church", r"gurudwara", r"namaz", r"allah",
        r"bhagwan", r"jesus", r"quran", r"bible", r"maulana", r"mullah?", r"katua", r"jihad\w*", r"kafir", r"burqa",
        r"hijab", r"pagdi", r"turban",
    ),
    "caste_region": _rx(
        r"caste\w*", r"dalit\w*", r"brahmin\w*", r"baniy[ae]", r"kshatriya", r"shudra", r"chamar\w*", r"bhangi",
        r"neech", r"lower caste", r"upper caste", r"sc ?/ ?st", r"obc", r"quota", r"reservation (?:wala|quota|category)",
        r"jati", r"gotra", r"marwari", r"bihari", r"madrasi", r"chinki", r"ghati", r"gujju", r"mallu", r"bong",
        r"south indian", r"north indian", r"northeast\w*", r"chinese looking",
    ),
    "sexuality": _rx(
        r"gay", r"lesbian", r"homo\w*", r"queer", r"trans", r"transgender", r"lgbt\w*", r"chakk[ae]", r"hijr[ae]",
        r"kinnar", r"gandu", r"meetha", r"bisexual", r"sexuality", r"virgin\w*", r"sex\w*", r"porn\w*", r"nudes?",
        r"horny",
    ),
    "money_hardship": _rx(
        r"gareeb\w*", r"garib\w*", r"poor (?:family|people|log)", r"broke", r"kangaal\w*", r"kangal", r"bankrupt\w*",
        r"debts?", r"karz\w*", r"karza", r"loans?", r"emis?", r"salary (?:nahi|nhi|not|cut)", r"paise nahi",
        r"paisa nahi", r"unemploy\w*", r"berozgar\w*", r"jobless", r"laid off", r"layoffs?", r"fired", r"bhik(?:h)?ari",
        r"beggar", r"slums?", r"homeless", r"afford",
    ),
    "grief": _rx(
        r"died", r"dead", r"death\w*", r"passed away", r"funeral", r"cremat\w*", r"condolences?", r"rest in peace",
        r"nahi rah[eai]", r"guzar ga\w*", r"maut", r"antim sanskar", r"shraddh", r"tervi", r"accident\w*", r"killed",
        r"murder\w*",
    ),
    "violence": _rx(r"rape\w*", r"molest\w*", r"assault\w*", r"abus(?:e|ed|ive)", r"harass\w*", r"stalk\w*", r"domestic violence"),
}

PROFANITY = _rx(
    r"madarchod\w*", r"m\.?c\.?", r"b\.?c\.?", r"behe?n ?chod\w*", r"bhen ?chod\w*", r"chutiy\w*", r"chut", r"bhosd\w*",
    r"lund", r"lawd\w*", r"loda", r"lauda", r"gaand\w*", r"randi\w*", r"harami\w*", r"fuck\w*", r"motherfuck\w*",
    r"bitch\w*", r"cunt", r"dick", r"pussy", r"asshole", r"bastard",
)

SERIOUS = _rx(
    r"passed away", r"nahi rah[eai]", r"guzar ga\w*", r"funeral", r"died", r"death", r"hospital(?:ised|ized)?",
    r"admit(?:ted)?", r"icu", r"accident", r"surgery", r"cancer", r"diagnos\w*", r"depress\w*", r"anxiety",
    r"panic attack", r"suicid\w*", r"kill myself", r"self[- ]harm", r"break ?up", r"broke up", r"divorce\w*",
    r"cheated on", r"laid off", r"lost my job", r"got fired", r"naukri (?:chali|gayi|gai)", r"job (?:chali|gayi|gai|chhut\w*)",
    r"feeling (?:low|down|lonely|empty|sad|lost)", r"not (?:okay|ok)", r"i'?m not fine", r"can'?t do this anymore",
    r"crying", r"ro (?:raha|rahi|rha|rhi)", r"rona aa", r"bahut bura lag", r"dil toot\w*", r"heartbroken",
    r"rest in peace", r"condolences?", r"stressed out", r"burn(?:ed|t)? ?out", r"tension mein", r"pareshan",
    r"fail ho gay\w*", r"failed (?:my|the) (?:exam|interview|test)", r"interview (?:went )?(?:bad|badly|kharab|achha nahi)",
    r"hope (?:he|she|they)'?s? (?:okay|ok|fine)", r"pray(?:ers|ing)? for",
)

LAUGHTER = re.compile(
    r"(?<![a-z])(?:lol\w*|lmao\w*|lmfao|rofl|haha\w*|hehe\w*|jk|skill issue|rip|moye|noob|bruh|cope|ratio|l+ma+o)(?![a-z])|[😂🤣💀😭🤡😆😹]",
    re.IGNORECASE,
)

SUPPORT_MARKERS = _rx(
    r"here(?: for you)?", r"sorry", r"hugs?", r"take care", r"sucks", r"rough", r"tough", r"hope", r"proud", r"love",
    r"got you", r"got your back", r"call (?:me|kar|karo|karna)", r"with you", r"saath", r"chinta mat", r"tension mat",
    r"sab theek", r"theek ho ja\w*", r"ho jayega", r"strong", r"rest up", r"hang in", r"anything you need",
    r"kuch bhi chahiye", r"main hoon", r"hum hain", r"don'?t worry", r"no worries", r"that'?s (?:hard|awful|terrible|so bad)",
    r"bura laga", r"dukh", r"prepped hard", r"doesn'?t (?:erase|define)", r"bounce back", r"deserve\w*", r"you'?ll",
    r"not your fault", r"proud of you", r"one bad", r"breathe", r"we'?re (?:all )?here", r"okay\?", r"ok\?",
)
SUPPORT_DENY = re.compile(r"bright side|at least|could be worse|better than|worse than|[<>]|kidding|roast", re.IGNORECASE)
SUPPORT_EMOJI_OK = re.compile("[❤🫂🙏💪]")

PERSONA_BANS = re.compile(
    r"as an ai|as a language model|i'?d be happy to|i would be happy to|happy to help|great question|^\s*certainly|"
    r"certainly!|hope this helps|feel free to|let me know if you need|i understand your|it'?s important to note",
    re.IGNORECASE,
)

MARKDOWN = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s|\*\*|__|`|^\s*#{1,6}\s|\[[^\]]+\]\([^)]+\)")

STOPWORDS = {"about", "with", "that", "this", "from", "their", "there", "they", "them", "your", "mera", "meri", "mere", "wala", "wali", "baat", "topic", "jokes", "joke"}


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""


def sensitive_categories(text: str) -> list[str]:
    return [cat for cat, rx in SENSITIVE.items() if rx.search(text or "")]


def detect_serious(text: str) -> str | None:
    hit = SERIOUS.search(text or "")
    return hit.group(0).lower() if hit else None


def topic_terms(topic: str) -> list[str]:
    topic = topic.strip().lower()
    terms = [w for w in re.findall(r"[a-zऀ-ॿ]{4,}", topic) if w not in STOPWORDS]
    return [topic, *terms] if topic else terms


def mentions_topic(text: str, topics: list[str]) -> str | None:
    low = (text or "").lower()
    for topic in topics:
        for term in topic_terms(topic):
            if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", low):
                return topic
    return None


def mentions_name(text: str, names: list[str]) -> str | None:
    low = (text or "").lower()
    for name in names:
        name = name.strip().lower()
        if len(name) >= 2 and re.search(r"(?<![a-z0-9])@?" + re.escape(name) + r"(?![a-z0-9])", low):
            return name
    return None


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def check_line(
    text: str,
    *,
    mode: str,
    offlimits: list[str] | None = None,
    protected: list[str] | None = None,
    recent: list[str] | None = None,
    max_chars: int = 240,
    max_lines: int = 3,
    repeat_threshold: float = 0.72,
    allow_profanity: bool = False,
) -> Verdict:
    t = (text or "").strip()
    if not t or t.upper() == "<SKIP>":
        return Verdict(False, "empty")
    if PERSONA_BANS.search(t):
        return Verdict(False, "persona")
    if MARKDOWN.search(t):
        return Verdict(False, "markdown")
    if len(t) > max_chars or t.count("\n") + 1 > max_lines:
        return Verdict(False, "length")
    if not allow_profanity and PROFANITY.search(t):
        return Verdict(False, "profanity")
    if mode == "support":
        if LAUGHTER.search(t) or SUPPORT_DENY.search(t):
            return Verdict(False, "joke_in_support")
        if not (SUPPORT_MARKERS.search(t) or SUPPORT_EMOJI_OK.search(t)):
            return Verdict(False, "not_supportive")
    else:
        cats = sensitive_categories(t)
        if cats:
            return Verdict(False, f"sensitive:{cats[0]}")
        topic = mentions_topic(t, offlimits or [])
        if topic:
            return Verdict(False, f"offlimits:{topic}")
        name = mentions_name(t, protected or [])
        if name:
            return Verdict(False, f"protected:{name}")
    for prev in recent or []:
        if prev and similarity(t, prev) >= repeat_threshold:
            return Verdict(False, "repeat")
    return Verdict(True)


POLITICS = _rx(
    r"bjp", r"congress", r"aam aadmi party", r"modi(?:ji)?", r"rahul gandhi", r"kejriwal", r"yogi adityanath", r"owaisi", r"mamata",
    r"election\w*", r"ministers?", r"ministry", r"parliament", r"lok sabha", r"rajya sabha", r"vidhan sabha", r"mla",
    r"chief minister", r"prime minister", r"govt", r"government", r"opposition", r"pakistan\w*", r"china", r"war",
    r"army", r"terror\w*", r"riots?", r"communal", r"hindutva", r"godi media", r"bhakts?", r"andh ?bhakt\w*", r"sanghi",
    r"urban naxal", r"protest\w*", r"rally", r"politic\w*",
)

TRAGEDY = _rx(
    r"died", r"dead", r"death\w*", r"killed", r"murder\w*", r"accident\w*", r"crash\w*", r"floods", r"flood[- ]hit", r"flash flood", r"earthquake",
    r"stampede", r"blast", r"bomb\w*", r"attack\w*", r"rape\w*", r"assault\w*", r"suicid\w*", r"missing", r"tragedy",
    r"tragic", r"victims?", r"injured", r"collapse\w*", r"fire kills", r"arrested", r"scam(?:s|med)?", r"fraud",
)


def trend_is_safe(text: str, allow_politics: bool = False) -> Verdict:
    if TRAGEDY.search(text):
        return Verdict(False, "tragedy")
    if not allow_politics and POLITICS.search(text):
        return Verdict(False, "politics")
    for cat in ("religion", "caste_region", "sexuality", "violence", "grief", "mental_health"):
        if SENSITIVE[cat].search(text):
            return Verdict(False, cat)
    return Verdict(True)
