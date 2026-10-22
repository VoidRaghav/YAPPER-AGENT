from datetime import datetime

from app.export_parser import parse_export

IOS = """‎[19/10/26, 9:41:03 PM] Aman: bro i'll reach in 5 mins
[19/10/26, 9:42:10 PM] Priya: haha sure
second line of priya
‎[19/10/26, 9:43:00 PM] Rahul: ‎audio omitted
[19/10/26, 9:44:00 PM] Messages and calls are end-to-end encrypted."""

ANDROID = """19/10/2026, 21:41 - Aman: bro i'll reach in 5 mins
19/10/2026, 21:42 - Priya: <Media omitted>
19/10/2026, 21:43 - Dev added Rahul
10/19/26, 9:44 PM - Aman: us format"""


def test_ios_export():
    msgs = parse_export(IOS)
    assert [m.sender for m in msgs] == ["Aman", "Priya", "Rahul"]
    assert msgs[0].ts == datetime(2026, 10, 19, 21, 41, 3)
    assert msgs[1].text == "haha sure\nsecond line of priya"
    assert msgs[2].kind == "voice" and msgs[2].text == ""


def test_android_export_and_fallback_date_order():
    msgs = parse_export(ANDROID)
    assert [m.sender for m in msgs] == ["Aman", "Priya", "Aman"]
    assert msgs[1].kind == "image"
    assert msgs[2].ts == datetime(2026, 10, 19, 21, 44)
