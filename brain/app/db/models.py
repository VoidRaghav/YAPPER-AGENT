from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings
from app.db.types import Embedding
from app.utils import utcnow

DIM = get_settings().embed_dim


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON, list[int]: JSON, datetime: DateTime()}


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    wa_jid: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    language_mix: Mapped[str] = mapped_column(String(64), default="hinglish")
    chattiness: Mapped[str] = mapped_column(String(8), default="med")
    quiet_until: Mapped[datetime | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    offlimits: Mapped[list[str]] = mapped_column(default=list)
    mood: Mapped[str] = mapped_column(String(32), default="neutral")
    mood_at: Mapped[datetime | None] = mapped_column(default=None)
    msgs_since_mood: Mapped[int] = mapped_column(Integer, default=0)
    msgs_since_memory: Mapped[int] = mapped_column(Integer, default=0)
    memory_cursor: Mapped[int] = mapped_column(Integer, default=0)
    memory_at: Mapped[datetime | None] = mapped_column(default=None)
    think_cursor: Mapped[int] = mapped_column(Integer, default=0)
    intro_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    pack: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True)
    spice: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("group_id", "wa_jid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    wa_jid: Mapped[str] = mapped_column(String(128))
    alt_jid: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    nicknames: Mapped[list[str]] = mapped_column(default=list)
    profile_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    offlimits: Mapped[list[str]] = mapped_column(default=list)
    roastable: Mapped[bool] = mapped_column(Boolean, default=True)
    protected_until: Mapped[datetime | None] = mapped_column(default=None)
    first_seen: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(default=utcnow)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_group_ts", "group_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"), default=None, index=True)
    wa_id: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    type: Mapped[str] = mapped_column(String(16), default="text")
    text: Mapped[str] = mapped_column(Text, default="")
    transcript: Mapped[str | None] = mapped_column(Text, default=None)
    reply_to_wa_id: Mapped[str | None] = mapped_column(String(128), default=None)
    mentioned_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    from_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    media_path: Mapped[str | None] = mapped_column(String(512), default=None)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    embedding: Mapped[Any] = mapped_column(Embedding(DIM), default=None, nullable=True)

    @property
    def content(self) -> str:
        parts = [p for p in (self.text, self.transcript) if p]
        return " ".join(parts)


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), default=None, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="fact")
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Embedding(DIM), default=None, nullable=True)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    source_msg_ids: Mapped[list[int]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)


class Lore(Base):
    __tablename__ = "lore"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[Any] = mapped_column(Embedding(DIM), default=None, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(default=utcnow)
    times_called_back: Mapped[int] = mapped_column(Integer, default=0)
    last_called_back: Mapped[datetime | None] = mapped_column(default=None)


class BotLine(Base):
    __tablename__ = "bot_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), default="text")
    mode: Mapped[str] = mapped_column(String(16), default="")
    trend_ref: Mapped[str] = mapped_column(String(200), default="")
    target_wa_id: Mapped[str | None] = mapped_column(String(128), default=None)
    wa_id: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    ref: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    reaction_score: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[int | None] = mapped_column(Integer, default=None)


class BlockedLine(Base):
    __tablename__ = "blocked_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    text: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(200))


class WeeklyStat(Base):
    __tablename__ = "weekly_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    week: Mapped[str] = mapped_column(String(16), index=True)
    stat_key: Mapped[str] = mapped_column(String(64))
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"), default=None)
    value: Mapped[float] = mapped_column(Float, default=0)
    example_msg_id: Mapped[int | None] = mapped_column(Integer, default=None)
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)


class MoodLog(Base):
    __tablename__ = "mood_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    mood: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32), default="llm")


class Trend(Base):
    __tablename__ = "trends"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    phrase: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[str] = mapped_column(Text, default="")
    usage: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(default=list)
    category: Mapped[str] = mapped_column(String(32), default="meme")
    source: Mapped[str] = mapped_column(String(32), default="seed")
    url: Mapped[str] = mapped_column(String(1024), default="")
    evergreen: Mapped[bool] = mapped_column(Boolean, default=False)
    safe: Mapped[bool] = mapped_column(Boolean, default=True)
    score: Mapped[float] = mapped_column(Float, default=0)
    embedding: Mapped[Any] = mapped_column(Embedding(DIM), default=None, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    times_used: Mapped[int] = mapped_column(Integer, default=0)
