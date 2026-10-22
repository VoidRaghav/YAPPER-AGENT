from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.db import Group, Message, Person, WeeklyStat, db
from app.nodes import awards


async def _seed(settings):
    week_start = datetime(2026, 10, 12, 0, 0) - timedelta(hours=5, minutes=30)
    async with db.session() as ses:
        g = Group(wa_jid="g1@g.us", name="the boys", created_at=week_start)
        ses.add(g)
        await ses.flush()
        people = {n: Person(group_id=g.id, wa_jid=f"{n}@s", display_name=n.title()) for n in ("aman", "priya", "rahul", "dev")}
        ses.add_all(people.values())
        await ses.flush()

        def add(who: str, at: datetime, text: str) -> None:
            ses.add(Message(group_id=g.id, person_id=people[who].id, ts=at, text=text, type="text"))

        t = week_start + timedelta(days=1, hours=10)
        for i in range(12):
            add("aman", t + timedelta(minutes=i), f"yap {i}")
            add("rahul", t + timedelta(minutes=i, seconds=20), "haan")
        add("aman", t + timedelta(minutes=13), "aur ek baat")
        t2 = t + timedelta(hours=2)
        add("priya", t2, "koi movie chalega kya aaj raat?")
        add("aman", t2 + timedelta(hours=5), "sorry dekha nahi")
        t3 = t2 + timedelta(hours=6)
        add("dev", t3, "chalo goa plan karte hai")
        add("dev", t3 + timedelta(minutes=1), "party at my place saturday")
        add("rahul", t3 + timedelta(minutes=2), "sorry guys can't make it")
        add("aman", t3 + timedelta(minutes=3), "next time pakka")
        add("rahul", t3 + timedelta(minutes=4), "cancel kar do yaar")
        night = week_start + timedelta(days=2, minutes=30)
        for i in range(4):
            add("dev", night + timedelta(minutes=i * 10), "neend nahi aa rahi")
        await ses.flush()
        return g.id, people, week_start


async def test_weekly_stats(database, settings):
    gid, people, start = await _seed(settings)
    async with db.session() as ses:
        stats = await awards.compute(ses, settings, gid, start, start + timedelta(days=7))
    assert stats["biggest_yapper"]["person"] == "Aman"
    assert stats["biggest_yapper"]["value"] == 15
    assert stats["most_ignored_message"]["person"] == "Priya"
    assert "movie" in stats["most_ignored_message"]["detail"]["text"]
    assert stats["plans_maker"]["person"] == "Dev"
    assert stats["plans_breaker"]["person"] == "Rahul"
    assert stats["night_owl"]["person"] == "Dev"
    assert stats["chronically_online"]["person"] in ("Rahul", "Aman")
    assert stats["most_ghosted"]["value"] >= 1
    text = awards.format_stats(stats)
    assert "biggest yapper: Aman (15 msgs)" in text
    assert "- " not in text.split("\n", 1)[1]


async def test_stats_are_saved(database, settings):
    gid, _, start = await _seed(settings)
    async with db.session() as ses:
        stats = await awards.compute(ses, settings, gid, start, start + timedelta(days=7))
        await awards.save(ses, gid, "2026-W42", stats)
        await awards.save(ses, gid, "2026-W42", stats)
    async with db.session() as ses:
        assert await ses.scalar(select(func.count(WeeklyStat.id))) == len(stats)
