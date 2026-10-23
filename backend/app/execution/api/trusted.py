"""Server-owned command retrieval, never approval construction from HTTP claims."""
from datetime import datetime
from typing import Protocol
from uuid import UUID
from app.execution.paper import PaperCommand


class TrustedPaperCommandSource(Protocol):
    def resolve(self, *, user_id: UUID, intent_identity: str, decision_identity: str,
                request_identity: str, evaluated_at: datetime) -> PaperCommand:
        """Resolve exact approved provenance and trusted execution inputs.

        New commands require authoritative approval/market/cost sources. Successful
        retries must recover the original immutable command, not build a new one.
        The 11D service still revalidates/locks the actual portfolio in its transaction.
        """
        ...


class UnavailablePaperCommandSource:
    def resolve(self, **kwargs):
        raise LookupError('Authoritative Paper command source is not configured')


def get_trusted_paper_source() -> TrustedPaperCommandSource:
    # Like 10G, no persisted intent/approval registry or authoritative runtime
    # evidence builder exists yet. Do not fabricate safety state or accept it via HTTP.
    return UnavailablePaperCommandSource()
