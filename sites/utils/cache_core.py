"""
Server-side response cache.

Cached values are the serialized response data (plain dicts and lists), never model
instances. Entries are never deleted one by one. Every namespace has a version, the
version is part of each key, and invalidating a namespace just changes its version,
so every list, filter and page combination under it is dropped at once.

The cache is only a speed-up: if it is down or slow, the request is served from the
database instead of failing.
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
    return f"theme:{theme_id}:images"


def theme_videos(theme_id):
    return f"theme:{theme_id}:videos"


def user_sites(user_id):
    return f"user:{user_id}:sites"                    


def site_detail(site_id):
    return f"site:{site_id}:detail"


def site_pages(site_id):
    return f"site:{site_id}:pages"


def site_images(site_id):
    return f"site:{site_id}:images"


def site_videos(site_id):
    return f"site:{site_id}:videos"


def _enabled():
    return getattr(settings, "API_CACHE_ENABLED", True)


def _ttl():
    return getattr(settings, "API_CACHE_TTL", 300)


def _version_key(namespace):
    return f"{PREFIX}:v:{namespace}"


def _version(namespace):
    """
    A namespace's current version. It is a timestamp, not a counter, so if the cache
    loses the version key, the new one can never equal an old one and old entries
    stay unreachable.
    """
    key = _version_key(namespace)
    version = cache.get(key)
    if version is None:
        version = time.time_ns()
        if not cache.add(key, version, None):        
            version = cache.get(key, version)
    return version


def _digest(parts):
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]



def signature(request, *extra):
    """
    The part of the key that identifies a request: host, sorted query string, and any
    extra values (such as "list" or an id). The host is included because paginated
    responses contain absolute next and previous links.
    """
    query = urlencode(sorted(request.query_params.lists()), doseq=True)
    return (request.get_host(), query, *extra)


def cached(namespace, parts, build, ttl=None):
    """
    Return the cached value for (namespace, parts), or call build(), cache the result
    and return it. Errors raised by build() are never cached.
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
    """
    Drop everything cached under these namespaces. It runs after the surrounding
    transaction commits, so a reader can never cache data from before the write
    under the new version.
    """
    def run():
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