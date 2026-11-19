"""
Server-side response cache.

Cached values are serialized response data, never model instances. Each namespace
has a version included in its cache keys. Invalidation changes the version, making
all previous entries unreachable.

If caching fails, requests fall back to the database.
"""

import hashlib
import logging
import time
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)

PREFIX = "api"

THEMES = "themes"
SITES = "sites"


def theme_images(theme_id):
    """Return the theme images cache namespace.

    Args:
        theme_id: Theme ID.

    Returns:
        str: Cache namespace.
    """
    return f"theme:{theme_id}:images"


def theme_videos(theme_id):
    """Return the theme videos cache namespace.

    Args:
        theme_id: Theme ID.

    Returns:
        str: Cache namespace.
    """
    return f"theme:{theme_id}:videos"


def user_sites(user_id):
    """Return the user's sites cache namespace.

    Args:
        user_id: User ID.

    Returns:
        str: Cache namespace.
    """
    return f"user:{user_id}:sites"


def site_detail(site_id):
    """Return the site detail cache namespace.

    Args:
        site_id: Site ID.

    Returns:
        str: Cache namespace.
    """
    return f"site:{site_id}:detail"


def site_pages(site_id):
    """Return the site pages cache namespace.

    Args:
        site_id: Site ID.

    Returns:
        str: Cache namespace.
    """
    return f"site:{site_id}:pages"


def site_images(site_id):
    """Return the site images cache namespace.

    Args:
        site_id: Site ID.

    Returns:
        str: Cache namespace.
    """
    return f"site:{site_id}:images"


def site_videos(site_id):
    """Return the site videos cache namespace.

    Args:
        site_id: Site ID.

    Returns:
        str: Cache namespace.
    """
    return f"site:{site_id}:videos"


def _enabled():
    """Check whether API caching is enabled.

    Returns:
        bool: True if caching is enabled.
    """
    return getattr(settings, "API_CACHE_ENABLED", True)


def _ttl():
    """Return the default cache expiration time.

    Returns:
        int: Cache TTL in seconds.
    """
    return getattr(settings, "API_CACHE_TTL", 300)


def _version_key(namespace):
    """Build the namespace version cache key.

    Args:
        namespace: Cache namespace.

    Returns:
        str: Version cache key.
    """
    return f"{PREFIX}:v:{namespace}"


def _version(namespace):
    """Get or initialize the namespace version.

    Uses a timestamp to prevent old entries from becoming reachable
    if the version key is lost.

    Args:
        namespace: Cache namespace.

    Returns:
        int: Current namespace version.
    """
    key = _version_key(namespace)
    version = cache.get(key)

    if version is None:
        version = time.time_ns()
        if not cache.add(key, version, None):
            version = cache.get(key, version)

    return version


def _digest(parts):
    """Generate a hash from cache key components.

    Args:
        parts: Cache key components.

    Returns:
        str: SHA-256 digest.
    """
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def signature(request, *extra):
    """Build request-specific cache key components.

    Args:
        request: HTTP request.
        *extra: Additional cache key values.

    Returns:
        tuple: Host, query string, and extra values.
    """
    query = urlencode(sorted(request.query_params.lists()), doseq=True)
    return (request.get_host(), query, *extra)


def cached(namespace, parts, build, ttl=None):
    """Get cached data or build and cache a response.

    Cache failures fall back to the database. Build errors are not cached.

    Args:
        namespace: Cache namespace.
        parts: Cache key components.
        build: Callable that generates response data.
        ttl: Optional cache expiration time.

    Returns:
        Any: Cached or newly generated response data.
    """
    if not _enabled():
        return build()

    try:
        key = f"{PREFIX}:{namespace}:{_version(namespace)}:{_digest(parts)}"
        hit = cache.get(key)
    except Exception:
        logger.warning("Cache read failed for %s; using the database", namespace, exc_info=True)
        return build()

    if hit is not None:
        return hit

    data = build()
    try:
        cache.set(key, data, ttl or _ttl())
    except Exception:
        logger.warning("Cache write failed for %s", namespace, exc_info=True)

    return data


def invalidate(*namespaces):
    """Invalidate cache entries for the given namespaces.

    Updates namespace versions instead of deleting individual entries.
    Runs after transaction commit by default.

    Args:
        *namespaces: Cache namespaces to invalidate.
    """
    def run():
        """Update the versions of the specified namespaces."""
        now = time.time_ns()

        for namespace in namespaces:
            try:
                cache.set(_version_key(namespace), now, None)
            except Exception:
                logger.error("Cache invalidation failed for %s", namespace, exc_info=True)

    if getattr(settings, "API_CACHE_INVALIDATE_ON_COMMIT", True):
        transaction.on_commit(run)
    else:
        run()
