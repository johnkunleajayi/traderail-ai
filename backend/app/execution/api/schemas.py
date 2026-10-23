from datetime import timezone
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, PlainSerializer

Identity = Annotated[str, Field(strict=True, pattern=r'^sha256:[0-9a-f]{64}$', max_length=71)]
Financial = Annotated[Decimal, PlainSerializer(lambda x: format(x, 'f'), return_type=str)]
UTCDate = Annotated[AwareDatetime, PlainSerializer(
    lambda x: x.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'), return_type=str)]


class PaperExecutionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent_identity: Identity
    decision_identity: Identity
    request_identity: Identity


class PaperExecutionResponse(BaseModel):
    status: Literal['COMPLETED'] = 'COMPLETED'
    environment: Literal['PAPER'] = 'PAPER'
    result_identity: str
    intent_identity: str
    decision_identity: str
    request_identity: str
    order_identity: str
    fill_identity: str
    position_id: UUID
    position_effect: Literal['OPENED', 'CLOSED']
    action: Literal['ENTER_LONG', 'EXIT_LONG']
    quantity: Financial
    execution_price: Financial
    gross_notional: Financial
    fee: Financial
    currency: str
    cash_after: Financial
    gross_realized_pnl: Financial | None
    net_realized_pnl: Financial | None
    executed_at: UTCDate
