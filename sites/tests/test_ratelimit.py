"""Tests for the Lua token bucket and the middleware.

The bucket tests need Redis (set RATE_LIMIT_TEST_REDIS_URL, default db 15).
"""
import json
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

import redis
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from sites.ratelimit import engine
from sites.ratelimit.engine import Result
from sites.ratelimit.middleware import RateLimitMiddleware

REDIS_URL = os.environ.get("RATE_LIMIT_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
try:
    CLIENT = redis.Redis.from_url(REDIS_URL, socket_timeout=1)
    CLIENT.ping()
except redis.RedisError:
    CLIENT = None

TEST_CONF = {
    "REDIS_URL": REDIS_URL,
    "POLICIES": {
        "read": {"capacity": 5, "rate": 5 / 60},
        "write": {"capacity": 30, "rate": 0.5},
        "upload": {"capacity": 250, "rate": 2},
    },
}


@unittest.skipIf(CLIENT is None, "Redis is not reachable")
@override_settings(RATE_LIMIT=TEST_CONF)
class BucketTests(SimpleTestCase):
    """Verify the Lua token bucket."""

    def setUp(self):
        engine._script = None
        self.id = uuid.uuid4().hex
        self.key = f"rl:read:{self.id}"
        self.addCleanup(CLIENT.delete, self.key)

    def spend(self, cost=1000):
        return engine.consume("read", self.id, cost)

    def ago(self, tokens, ms):
        seconds, micros = CLIENT.time()
        CLIENT.hset(self.key, mapping={
            "tokens": tokens, "last_refill": seconds * 1000 + micros // 1000 - ms})

    def test_burst_then_denied_with_exact_retry(self):
        for _ in range(5):
            self.assertTrue(self.spend().allowed)
        result = self.spend()
        self.assertFalse(result.allowed)
        self.assertTrue(11_900 <= result.retry_after_ms <= 12_000)

    def test_denied_request_changes_nothing(self):
        self.ago(500, 0)
        before = CLIENT.hgetall(self.key)
        self.assertFalse(self.spend().allowed)
        self.assertEqual(CLIENT.hgetall(self.key), before)

    def test_refill_over_time(self):
        self.ago(0, 12_000)
        self.assertTrue(self.spend().allowed)
        self.ago(0, 11_000)
        self.assertFalse(self.spend().allowed)

    def test_never_exceeds_capacity(self):
        self.ago(4000, 10_000_000)
        self.assertEqual(self.spend(0).remaining, 5)

    def test_fractional_cost(self):
        self.spend(1100)
        self.assertEqual(int(CLIENT.hget(self.key, "tokens")), 3900)

    def test_key_expires_after_full_refill(self):
        self.spend()
        self.assertTrue(55_000 < CLIENT.pttl(self.key) <= 61_000)

    def test_concurrent_requests_never_overspend(self):
        with ThreadPoolExecutor(16) as pool:
            results = list(pool.map(lambda _: self.spend().allowed, range(50)))
        self.assertEqual(sum(results), 5)
    def test_rate_conversion_is_exact(self):
        self.assertEqual(engine.rate_per_minute(5 / 60), 5000)
        self.assertEqual(engine.rate_per_minute(2), 120_000)
        self.assertEqual(engine.rate_per_minute(0.5), 30_000)

class MiddlewareTests(SimpleTestCase):
    """Verify routing, responses and fail-open behaviour."""

    def setUp(self):
        self.factory = RequestFactory()
        self.mw = RateLimitMiddleware(lambda request: HttpResponse("ok"))

    def call(self, method="get", path="/api/x/", user=True, **extra):
        request = getattr(self.factory, method)(path, **extra)
        request.user = SimpleNamespace(is_authenticated=user, pk=7)
        return self.mw(request)

    def allowed(self, remaining=9):
        return Result(True, 10, remaining, 0, 3500)

    @mock.patch("sites.ratelimit.middleware.consume")
    def test_allowed_adds_headers(self, consume):
        consume.return_value = self.allowed()
        response = self.call()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["RateLimit-Limit"], "10")
        self.assertEqual(response["RateLimit-Remaining"], "9")
        self.assertEqual(response["RateLimit-Reset"], "4")
        consume.assert_called_once_with("read", "user_7", 1000)

    @mock.patch("sites.ratelimit.middleware.consume")
    def test_denied_returns_standard_envelope(self, consume):
        consume.return_value = Result(False, 10, 0, 1500, 9000)
        response = self.call()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "2")
        self.assertEqual(json.loads(response.content), {
            "success": False,
            "message": "Too many requests. Please try again later.",
            "data": None,
            "errors": {"retry_after": 2},
        })

    @mock.patch("sites.ratelimit.middleware.consume", return_value=None)
    def test_redis_down_fails_open(self, consume):
        self.assertEqual(self.call().status_code, 200)

    @mock.patch("sites.ratelimit.middleware.consume")
    def test_write_scope(self, consume):
        consume.return_value = self.allowed()
        self.call("post", data="{}", content_type="application/json")
        consume.assert_called_once_with("write", "user_7", 1000)

    @mock.patch("sites.ratelimit.middleware.consume")
    def test_upload_cost_is_one_plus_mib(self, consume):
        consume.return_value = self.allowed()
        self.call("post", CONTENT_TYPE="multipart/form-data; boundary=x",
                  CONTENT_LENGTH=str(1024 * 1024))
        consume.assert_called_once_with("upload", "user_7", 2000)

    @mock.patch("sites.ratelimit.middleware.consume")
    def test_upload_without_valid_length_is_rejected(self, consume):
        for length in ("", "abc", "-5"):
            response = self.call("post", CONTENT_TYPE="multipart/form-data; boundary=x",
                                 CONTENT_LENGTH=length)
            self.assertEqual(response.status_code, 411)
        consume.assert_not_called()

    @mock.patch("sites.ratelimit.middleware.consume")
    def test_oversized_upload_is_rejected(self, consume):
        response = self.call("post", CONTENT_TYPE="multipart/form-data; boundary=x",
                             CONTENT_LENGTH=str(10**12))
        self.assertEqual(response.status_code, 413)
        consume.assert_not_called()

    @mock.patch("sites.ratelimit.middleware.consume")
    @override_settings(RATE_LIMIT={"EXEMPT_PATHS": ["/health/"]})
    def test_anonymous_options_and_exempt_paths_are_skipped(self, consume):
        self.call(user=False)
        self.call("options")
        self.call(path="/health/")
        consume.assert_not_called()