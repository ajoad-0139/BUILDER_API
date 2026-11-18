"""Apply per-user token bucket rate limiting to every API request."""
import math

from rest_framework.renderers import JSONRenderer

from sites.ratelimit.engine import UNITS, consume, get_conf, upload_cost, validate_conf
from sites.utils.responses import error_response

SAFE_METHODS = {"GET", "HEAD"}


def _render(response):
    """Render a DRF Response so it can be returned from middleware.

    Args:
        response: Unrendered DRF Response.

    Returns:
        Response: The same response, rendered as JSON.
    """
    response.accepted_renderer = JSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    return response.render()


def _content_length(request):
    """Parse Content-Length safely.

    Args:
        request: Django request.

    Returns:
        int | None: Size in bytes, or None if missing or invalid.
    """
    value = request.META.get("CONTENT_LENGTH", "").strip()
    return int(value) if value.isascii() and value.isdigit() else None


class RateLimitMiddleware:
    """Limit authenticated requests before views and response caching run.
    
    """

    def __init__(self, get_response):
        """Validate settings and store the next handler.

        Args:
            get_response: Next middleware or view.
        """
        self.get_response = get_response
        validate_conf()

    def __call__(self, request):
        """Check the bucket, then continue or return HTTP 429.

        Args:
            request: Django request.

        Returns:
            HttpResponse: 429/411/413 when rejected, otherwise the view's
            response with RateLimit headers.
        """
        conf = get_conf()
        if (
            not conf["ENABLED"]
            or request.method == "OPTIONS"
            or request.path.startswith(tuple(conf["EXEMPT_PATHS"]))
            or not request.user.is_authenticated
        ):
            return self.get_response(request)

        if request.method in SAFE_METHODS:
            scope, cost = "read", UNITS
        elif request.META.get("CONTENT_TYPE", "").startswith("multipart/"):
            size = _content_length(request)
            if size is None:
                return _render(error_response(
                    "Content-Length header is required for uploads.", status_code=411))
            if size > conf["MAX_UPLOAD_BYTES"]:
                return _render(error_response("Upload is too large.", status_code=413))
            scope, cost = "upload", upload_cost(size)
        else:
            scope, cost = "write", UNITS

        result = consume(scope, f"user_{request.user.pk}", cost)
        if result is None:  # Redis unavailable: fail open
            return self.get_response(request)

        if not result.allowed:
            retry_after = max(1, math.ceil(result.retry_after_ms / 1000))
            response = _render(error_response(
                "Too many requests. Please try again later.",
                errors={"retry_after": retry_after},
                status_code=429,
            ))
            response["Retry-After"] = str(retry_after)
        else:
            response = self.get_response(request)

        response["RateLimit-Limit"] = str(result.limit)
        response["RateLimit-Remaining"] = str(result.remaining)
        response["RateLimit-Reset"] = str(math.ceil(result.reset_ms / 1000))
        return response