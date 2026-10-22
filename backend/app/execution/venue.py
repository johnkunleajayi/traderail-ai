"""Future adapter contract only. No concrete venue or callable submission implementation."""
from datetime import datetime
from typing import Protocol, runtime_checkable
from .request import ExecutionRequest
from .orders import Order
from .vocabulary import ExecutionEnvironment


@runtime_checkable
class ExecutionVenue(Protocol):
    @property
    def venue_identity(self) -> str: ...
    @property
    def environment(self) -> ExecutionEnvironment: ...

    def submit(self, request: ExecutionRequest, *, as_of: datetime) -> Order:
        """Future implementation must revalidate, atomically deduplicate and act at most once.

        Ambiguous delivery requires lookup/reconciliation with the SAME client order identity,
        never an unchecked second submission. 11A supplies no implementation.
        """
        ...

    def lookup(self, client_order_identity: str, *, as_of: datetime) -> Order | None:
        """Future authoritative reconciliation; no current network/database lookup exists."""
        ...
