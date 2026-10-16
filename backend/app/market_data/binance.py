import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx

from app.market_data.candle_schemas import ProviderCandle, Timeframe
from app.market_data.provider import ProviderUnavailable

KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
# Provider-specific intervals/durations stay inside this adapter.
INTERVALS = {"15m": ("15m", 900000), "1h": ("1h", 3600000),
             "4h": ("4h", 14400000), "1d": ("1d", 86400000)}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def decimal_value(value: object) -> Decimal:
    # Binance documents financial fields as strings. Reject schema drift and floats.
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError("Invalid financial value")
    number = Decimal(value)
    if not number.is_finite() or number.copy_abs() > Decimal("1e100") or (number and number.adjusted() < -100):
        raise ValueError("Invalid financial magnitude")
    return number


def normalize_klines(payload: object, timeframe: Timeframe, limit: int,
                     retrieved_at: datetime) -> list[ProviderCandle]:
    try:
        duration = INTERVALS[timeframe][1]
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("Invalid limit")
        if not isinstance(payload, list) or not payload or len(payload) > limit:
            raise ValueError("Invalid batch")
        candles, seen = [], set()
        for row in payload:
            if not isinstance(row, list) or len(row) != 12:
                raise ValueError("Invalid kline row")
            start, end = row[0], row[6]
            if any(isinstance(t, bool) or not isinstance(t, int) or t <= 0 for t in (start, end)):
                raise ValueError("Invalid timestamp")
            if start in seen or start % duration or end != start + duration - 1:
                raise ValueError("Duplicate or invalid candle interval")
            seen.add(start)
            candles.append(ProviderCandle(
                open_time=EPOCH + timedelta(milliseconds=start),
                close_time=EPOCH + timedelta(milliseconds=end),
                open=decimal_value(row[1]), high=decimal_value(row[2]),
                low=decimal_value(row[3]), close=decimal_value(row[4]), volume=decimal_value(row[5]),
                retrieved_at=retrieved_at,
            ))
        # Sorting changes only order: never values, duplicates or missing intervals.
        return sorted(candles, key=lambda candle: candle.open_time)
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        raise ProviderUnavailable() from None


def reject_constant(value: str):
    raise ValueError("Non-finite JSON number")


class BinanceHistoricalProvider:
    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch_candles(self, provider_symbol: str, timeframe: Timeframe, limit: int) -> list[ProviderCandle]:
        try:
            interval, duration = INTERVALS[timeframe]
            if (not provider_symbol or len(provider_symbol) > 128 or isinstance(limit, bool)
                    or not isinstance(limit, int) or not 1 <= limit <= 1000):
                raise ValueError("Invalid request")
            requested_at = datetime.now(timezone.utc)
            delta = requested_at - EPOCH
            now_ms = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
            # Inclusive last millisecond of the previous UTC bucket: completed bars only.
            end_time = (now_ms // duration) * duration - 1
            params = {"symbol": provider_symbol, "interval": interval, "limit": limit,
                      "timeZone": "0", "endTime": end_time}
            with self.client.stream("GET", KLINES_URL, params=params,
                                    headers={"Accept": "application/json"}) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 1048576:
                        raise ValueError("Oversized batch")
            payload = json.loads(body, parse_float=Decimal, parse_constant=reject_constant)
            candles = normalize_klines(payload, timeframe, limit, datetime.now(timezone.utc))
            if any(c.close_time > EPOCH + timedelta(milliseconds=end_time) for c in candles):
                raise ValueError("Provider exceeded completed-candle boundary")
            return candles
        except (httpx.HTTPError, KeyError, TypeError, ValueError, UnicodeError):
            raise ProviderUnavailable() from None


def get_historical_provider():
    # Official unauthenticated, market-data-only host. No account/order surface.
    with httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0), trust_env=False,
                      follow_redirects=False) as client:
        yield BinanceHistoricalProvider(client)
