"""Shared daily budget for mock LLM calls, measured in nanodollars."""

from datetime import datetime, timezone

from fastapi import HTTPException

from .config import settings
from .redis_client import r

NANODOLLARS_PER_USD = 1_000_000_000
# Equivalent to USD 0.00015 per 1K input tokens and USD 0.0006 per 1K output tokens.
INPUT_PRICE = 150
OUTPUT_PRICE = 600
BUDGET_TTL = 2 * 24 * 60 * 60

SETTLE = r.register_script("""
if redis.call('EXISTS', KEYS[1]) == 1 then
    redis.call('INCRBY', KEYS[1], ARGV[1])
end
""")

RESERVE = r.register_script("""
local total = tonumber(redis.call('GET', KEYS[1]) or '0')
local estimate = tonumber(ARGV[1])
if total + estimate > tonumber(ARGV[2]) then
    return 0
end
redis.call('INCRBY', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
""")


def estimate_tokens(text: str) -> int:
    """Approximate a token as four UTF-8 bytes for the mock model."""
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def check_budget(input_tokens: int, max_output_tokens: int) -> tuple[str, int]:
    day = datetime.now(timezone.utc).date().isoformat()
    key = f"day14:cost:{day}"
    reserved = input_tokens * INPUT_PRICE + max_output_tokens * OUTPUT_PRICE
    limit = int(settings.daily_budget_usd * NANODOLLARS_PER_USD)
    if not RESERVE(keys=[key], args=[reserved, limit, BUDGET_TTL]):
        raise HTTPException(status_code=402, detail="Daily LLM budget exceeded")
    return key, reserved


def record_cost(
    key: str, reserved: int, input_tokens: int, output_tokens: int
) -> float:
    """Replace the reservation with the actual charge in its original UTC day."""
    actual = input_tokens * INPUT_PRICE + output_tokens * OUTPUT_PRICE
    adjustment = actual - reserved
    if adjustment:
        SETTLE(keys=[key], args=[adjustment])
    return actual / NANODOLLARS_PER_USD
