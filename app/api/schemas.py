from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PathName = Literal["naive", "stream", "compare"]


class SnapshotRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    path: Literal["stream", "compare"]
    revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=20_000)


class CommitRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    path: PathName
    revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=20_000)
    query_time: str = Field(default="", max_length=128)


class CommitAccepted(BaseModel):
    run_id: str
    turn_id: str
    path: PathName
    events_url: str


class SnapshotAccepted(BaseModel):
    turn_id: str
    revision: int
    events_url: str
