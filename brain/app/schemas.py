from typing import Literal

from pydantic import BaseModel, Field


class Quoted(BaseModel):
    id: str
    participant: str | None = None
    from_bot: bool = False
    text: str | None = None


class Media(BaseModel):
    mimetype: str = ""
    b64: str | None = None
    seconds: int | None = None
    filename: str | None = None


class Reaction(BaseModel):
    target_id: str
    emoji: str = ""


class Event(BaseModel):
    type: Literal["message", "bot_added", "bot_removed", "group_update"]
    group_jid: str
    group_name: str | None = None
    id: str | None = None
    sender_jid: str | None = None
    sender_alt_jid: str | None = None
    sender_name: str | None = None
    ts: float = 0
    kind: str = "text"
    text: str = ""
    mentions: list[str] = Field(default_factory=list)
    mentioned_bot: bool = False
    from_owner: bool = False
    quoted: Quoted | None = None
    media: Media | None = None
    reaction: Reaction | None = None


class Sent(BaseModel):
    ref: str | None = None
    jid: str
    wa_id: str | None = None
    kind: str = "text"
    ok: bool = True
    error: str | None = None
