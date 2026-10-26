import logging

from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_call
from app.schemas import BatchIn, BatchOut, Prediction, TicketIn
from classify import BACKOFF, classify_all, run_classification

logger = logging.getLogger(__name__)
router = APIRouter()

FAILED_REASON = "classification failed"


def prediction_from(ticket_id, outcome):
    if outcome.error is not None:
        logger.error("classification failed for ticket %s: %s", ticket_id, outcome.error)
        return Prediction(ticket_id=ticket_id, category=None, confidence=None, needs_human=True, reason=FAILED_REASON)
    return Prediction(ticket_id=ticket_id, **outcome.prediction)


@router.post("/classify")
def classify_ticket(ticket: TicketIn, call=Depends(get_call)) -> Prediction:
    prediction = prediction_from(ticket.ticket_id, run_classification(ticket.text, call=call))
    if prediction.category is None:
        raise HTTPException(status_code=502, detail=FAILED_REASON)
    return prediction


@router.post("/classify/batch")
def classify_batch(batch: BatchIn, call=Depends(get_call)) -> BatchOut:
    results, _ = classify_all([ticket.model_dump() for ticket in batch.tickets], call, BACKOFF)
    return BatchOut(predictions=[prediction_from(row["ticket_id"], outcome) for row, outcome in results])
