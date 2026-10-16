import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx

from app.market_data.provider import ProviderUnavailable
from app.market_data.schemas import ProviderTicker

TICKER_URL = "https://openapi.quidax.io/exchange-open-api/api/v1/markets/tickers/"


def reject_json_constant(value: str):
    raise ValueError("Non-finite JSON number")


def normalize_ticker(payload: object, venue_symbol: str, retrieved_at: datetime) -> ProviderTicker:
    """Only observed last/at fields; at is source snapshot time, not last trade time."""
    try:
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise ValueError("Unsuccessful source envelope")
        data = payload["data"]
        if not isinstance(data, dict) or set(data) != {venue_symbol}:
            raise ValueError("Unexpected market identity")
        item = data[venue_symbol]
        last = item["ticker"]["last"]
        # JSON decimals must already be parsed with Decimal, never binary float.
        if isinstance(last, bool) or not isinstance(last, (str, int, Decimal)):
            raise ValueError("Invalid last price type")
        if len(str(last)) > 128:
            raise ValueError("Price too large")
        price = Decimal(last)
        if not price.is_finite() or price <= 0 or price > Decimal("1e100") or price.adjusted() < -100:
            raise ValueError("Invalid last price")
        source_timestamp = None
        if "at" in item:
            at = item["at"]
            if isinstance(at, bool) or not isinstance(at, int) or at <= 0:
                raise ValueError("Invalid timestamp")
            source_timestamp = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=at)
            if source_timestamp > retrieved_at + timedelta(minutes=5):
                raise ValueError("Future timestamp")
        return ProviderTicker(last_price=price, source_timestamp=source_timestamp, retrieved_at=retrieved_at)
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        raise ProviderUnavailable() from None


class QuidaxPublicProvider:
    """Public read-only ticker endpoint; symbols are supplied by stored mappings."""
    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch_ticker(self, venue_symbol: str) -> ProviderTicker:
        try:
            # Encode the identifier as one path segment. Never accept a user-supplied URL.
            if not venue_symbol or len(venue_symbol) > 128 or venue_symbol in (".", ".."):
                raise ValueError("Invalid identifier")
            with self.client.stream("GET", TICKER_URL + quote(venue_symbol, safe=""),
                                    headers={"Accept": "application/json"}) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise ValueError("Oversized response")
            payload = json.loads(body, parse_float=Decimal,
                                 parse_constant=reject_json_constant)
            return normalize_ticker(payload, venue_symbol, datetime.now(timezone.utc))
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise ProviderUnavailable() from None


def get_public_provider():
    # No authorization headers, keys, cookies, redirects or environment proxy credentials.
    with httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0), trust_env=False,
                      follow_redirects=False) as client:
        yield QuidaxPublicProvider(client)
