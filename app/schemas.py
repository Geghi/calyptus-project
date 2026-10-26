from typing import Literal

from pydantic import BaseModel, Field

from classify import CATEGORIES

MAX_TEXT_LENGTH = 5000
MAX_BATCH_SIZE = 100


class TicketIn(BaseModel):
    ticket_id: str | None = None
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


class Prediction(BaseModel):
    ticket_id: str | None
    category: Literal[*CATEGORIES] | None
    confidence: float | None
    needs_human: bool
    reason: str


class BatchIn(BaseModel):
    tickets: list[TicketIn] = Field(min_length=1, max_length=MAX_BATCH_SIZE)


class BatchOut(BaseModel):
    predictions: list[Prediction]
