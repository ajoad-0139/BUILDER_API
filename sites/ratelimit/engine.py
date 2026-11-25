"""Token bucket rate limiting backed by one atomic Redis Lua script."""

import logging
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import redis
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

UNITS = 1000         
MIB = 1024 * 1024
_LUA = Path(__file__).with_name("token_bucket.lua")

DEFAULTS = {
    "ENABLED": True,
    "REDIS_URL": "redis://127.0.0.1:6379/2",
    "SOCKET_TIMEOUT": 0.08,                
    "EXEMPT_PATHS": [],
    "MAX_UPLOAD_BYTES": 100 * MIB,
    "POLICIES": {
        "read": {"capacity": 120, "rate": 2},
        "write": {"capacity": 30, "rate": 0.5},
        "upload": {"capacity": 250, "rate": 2},
    },
}

_lock = threading.Lock()
_script = None


@dataclass(frozen=True)
class Result:
    """Outcome of one rate limit check.

    Attributes:
        allowed: Whether the request may proceed.
        limit: Bucket capacity in whole tokens.
        remaining: Whole tokens left after this request.
        retry_after_ms: Milliseconds until the request becomes affordable.
        reset_ms: Milliseconds until the bucket is full again.
    """

    allowed: bool
    limit: int
    remaining: int
    retry_after_ms: int
    reset_ms: int


def get_conf():
    """Return the rate limit settings merged over the defaults.

    Returns:
        dict: Effective configuration.
    """
    return {**DEFAULTS, **getattr(settings, "RATE_LIMIT", {})}


def to_units(tokens):
    """Convert tokens to integer units without float error.

    Args:
        tokens: Token amount as int, float or str.

    Returns:
        int: Thousandths of a token.
    """
    return int(Decimal(str(tokens)) * UNITS)

def rate_per_minute(rate):
    """Convert a refill rate in tokens per second to units per minute.

    Rounds instead of truncating, so rates such as 5/60 stay exact.

    Args:
        rate: Tokens per second as int, float or str.

    Returns:
        int: Units added per minute.
    """
    return round(Decimal(str(rate)) * 60 * UNITS)


def upload_cost(size_bytes):
    """Return the units an upload of the given size costs.

    Cost is 1 token plus 1 token per MiB, rounded up.

    Args:
        size_bytes: Request body size in bytes.

    Returns:
        int: Cost in units.
    """
    return UNITS + -(-size_bytes * UNITS // MIB)


def validate_conf():
    """Check the configuration is usable.

    Raises:
        ImproperlyConfigured: If a policy is invalid or the upload bucket
            cannot afford the largest permitted upload.
    """
    conf = get_conf()
    for scope in ("read", "write", "upload"):
        policy = conf["POLICIES"].get(scope)
        if not policy or to_units(policy["capacity"]) < UNITS or rate_per_minute(policy["rate"]) < 1:
            raise ImproperlyConfigured(f"RATE_LIMIT policy '{scope}' is invalid.")
    capacity = to_units(conf["POLICIES"]["upload"]["capacity"])
    if upload_cost(conf["MAX_UPLOAD_BYTES"]) > capacity:
        raise ImproperlyConfigured(
            "RATE_LIMIT upload capacity is too small for MAX_UPLOAD_BYTES."
        )


def _get_script():
    """Return the registered Lua script, connecting lazily.

    Returns:
        redis.commands.core.Script: Callable script bound to a short-timeout client.
    """
    global _script
    if _script is None:
        with _lock:
            if _script is None:
                conf = get_conf()
                client = redis.Redis.from_url(
                    conf["REDIS_URL"],
                    socket_timeout=conf["SOCKET_TIMEOUT"],
                    socket_connect_timeout=conf["SOCKET_TIMEOUT"],
                )
                _script = client.register_script(_LUA.read_text())
    return _script


def consume(scope, identity, cost):
    """Try to spend tokens from a bucket.

    Args:
        scope: Policy name: "read", "write" or "upload".
        identity: Bucket owner, such as "user_101".
        cost: Cost in units.

    Returns:
        Result | None: The outcome, or None if Redis is unavailable (the
        caller should then let the request through).
    """
    policy = get_conf()["POLICIES"][scope]
    capacity, rate = to_units(policy["capacity"]), rate_per_minute(policy["rate"])
    try:
        allowed, retry, remaining, reset = _get_script()(
            keys=[f"rl:{scope}:{identity}"], args=[capacity, rate, cost]
        )
    except redis.RedisError as exc:
        logger.warning("Rate limiter unavailable, allowing request: %s", exc)
        return None
    return Result(
        allowed=bool(allowed),
        limit=capacity // UNITS,
        remaining=int(remaining) // UNITS,
        retry_after_ms=int(retry),
        reset_ms=int(reset),
    )