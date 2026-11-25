import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from sites.cache import cache_core
from sites.cache.cache_core import (
    THEMES, cached, invalidate, signature, site_detail, site_images,
    site_pages, site_videos, theme_images, theme_videos, user_sites,
)
from sites.cache.cache_signals import namespaces_for
from sites.models import Image, Page, Site, Theme, Video
from sites.services.site import SiteService
from sites.services.theme import ThemeService
from sites.tests.helpers import make_css, make_html, make_image, make_video


pytestmark = pytest.mark.django_db

NS = "test:namespace"


@pytest.fixture(autouse=True)
def cache_settings(settings):
    """
    Give each test a clean cache and predictable cache settings.

    Invalidation runs immediately by default so tests do not need to
    capture on-commit callbacks (signals normally defer invalidation to
    transaction commit, which never happens inside a test transaction).
    Requires a real cache backend (for example LocMemCache).
    """
    settings.API_CACHE_ENABLED = True
    settings.API_CACHE_TTL = 300
    settings.API_CACHE_INVALIDATE_ON_COMMIT = False
    settings.ALLOWED_HOSTS = ["*"]
    cache.clear()
    yield
    cache.clear()


class Builder:
    """
    Callable that records how many times it was invoked.

    Args:
        value: Value returned by every call.
    """

    def __init__(self, value="data"):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.value


def make_request(path="/api/v1/themes", host="testserver", **query):
    """
    Build a DRF request carrying the given query parameters.

    Args:
        path: Request path.
        host: Host header value.
        **query: Query string parameters.

    Returns:
        Request: DRF request wrapping a Django test request.
    """
    return Request(APIRequestFactory().get(path, query, HTTP_HOST=host))



@pytest.mark.parametrize("func, arg, expected", [
    (cache_core.theme_images, 7, "theme:7:images"),
    (cache_core.theme_videos, 7, "theme:7:videos"),
    (cache_core.user_sites, 3, "user:3:sites"),
    (cache_core.site_detail, "abc", "site:abc:detail"),
    (cache_core.site_pages, "abc", "site:abc:pages"),
    (cache_core.site_images, "abc", "site:abc:images"),
    (cache_core.site_videos, "abc", "site:abc:videos"),
])
def test_namespace_helpers(func, arg, expected):
    """
    Verify that namespace helper functions build the expected names.

    Args:
        func: Namespace helper under test.
        arg: ID passed to the helper.
        expected: Expected namespace string.
    """
    assert func(arg) == expected


def test_namespaces_are_distinct_per_id():
    """
    Verify that different IDs produce different namespaces.
    """
    assert cache_core.site_pages(1) != cache_core.site_pages(2)
    assert cache_core.site_pages(1) != cache_core.site_images(1)



def test_digest_is_deterministic_and_fixed_length():
    """
    Verify that the same parts always produce the same 32-character digest.
    """
    first = cache_core._digest(("host", "a=1", 5))
    second = cache_core._digest(("host", "a=1", 5))

    assert first == second
    assert len(first) == 32


def test_digest_differs_for_different_parts():
    """
    Verify that changing any part changes the digest.
    """
    base = cache_core._digest(("host", "a=1"))

    assert cache_core._digest(("host", "a=2")) != base
    assert cache_core._digest(("other", "a=1")) != base
    assert cache_core._digest(("host", "a=1", "extra")) != base



def test_version_is_created_and_stable():
    """
    Verify that a namespace version is initialised once and then reused.
    """
    first = cache_core._version(NS)

    assert isinstance(first, int)
    assert cache_core._version(NS) == first
    assert cache.get(cache_core._version_key(NS)) == first


def test_version_is_independent_per_namespace():
    """
    Verify that namespaces keep separate versions.
    """
    a = cache_core._version("ns:a")
    invalidate("ns:b")

    assert cache_core._version("ns:a") == a


def test_version_uses_existing_value_when_add_loses_race():
    """
    Verify that when another process initialises the version first,
    the stored value is used instead of the locally generated one.
    """
    fake = MagicMock()
    fake.get.side_effect = [None, 12345]
    fake.add.return_value = False

    with patch.object(cache_core, "cache", fake):
        assert cache_core._version(NS) == 12345



def test_signature_contains_host_query_and_extra():
    """
    Verify that the signature holds the host, query string, and extra values.
    """
    request = make_request(page="2")

    assert signature(request, "user-1") == ("testserver", "page=2", "user-1")


def test_signature_ignores_query_parameter_order():
    """
    Verify that the same query parameters in a different order produce
    the same signature.
    """
    first = Request(APIRequestFactory().get("/x?b=2&a=1"))
    second = Request(APIRequestFactory().get("/x?a=1&b=2"))

    assert signature(first) == signature(second)


def test_signature_keeps_repeated_parameters():
    """
    Verify that repeated query parameters are preserved.
    """
    request = Request(APIRequestFactory().get("/x?tag=a&tag=b"))

    assert signature(request)[1] == "tag=a&tag=b"


def test_signature_differs_by_query_value():
    """
    Verify that different query values produce different signatures.
    """
    assert signature(make_request(page="1")) != signature(make_request(page="2"))


def test_signature_differs_by_host():
    """
    Verify that the same request on different hosts produces different
    signatures.
    """
    assert signature(make_request(host="a.com")) != signature(make_request(host="b.com"))


def test_signature_without_query_is_empty_string():
    """
    Verify that a request without parameters yields an empty query part.
    """
    assert signature(make_request())[1] == ""



def test_cached_builds_once_then_hits():
    """
    Verify that the builder runs on a miss and is skipped on later calls.
    """
    build = Builder({"a": 1})

    first = cached(NS, ("k",), build)
    second = cached(NS, ("k",), build)

    assert first == second == {"a": 1}
    assert build.calls == 1


def test_cached_different_parts_use_different_entries():
    """
    Verify that different key parts are cached separately.
    """
    build = Builder()

    cached(NS, ("a",), build)
    cached(NS, ("b",), build)

    assert build.calls == 2


def test_cached_different_namespaces_are_isolated():
    """
    Verify that identical parts in different namespaces do not collide.
    """
    first, second = Builder("one"), Builder("two")

    assert cached("ns:one", ("k",), first) == "one"
    assert cached("ns:two", ("k",), second) == "two"


def test_cached_stores_falsy_values():
    """
    Verify that empty lists and zero are cached rather than rebuilt.
    """
    empty, zero = Builder([]), Builder(0)

    cached(NS, ("empty",), empty)
    cached(NS, ("empty",), empty)
    cached(NS, ("zero",), zero)
    cached(NS, ("zero",), zero)

    assert empty.calls == 1
    assert zero.calls == 1


def test_cached_does_not_store_none():
    """
    Verify that a None result is never treated as a cache hit.
    """
    build = Builder(None)

    cached(NS, ("k",), build)
    cached(NS, ("k",), build)

    assert build.calls == 2


def test_cached_build_error_propagates_and_is_not_cached():
    """
    Verify that builder exceptions reach the caller and leave no entry behind.
    """
    def broken():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        cached(NS, ("k",), broken)

    build = Builder("ok")
    assert cached(NS, ("k",), build) == "ok"
    assert build.calls == 1


def test_cached_disabled_always_builds(settings):
    """
    Verify that disabling the cache bypasses reads and writes.
    """
    settings.API_CACHE_ENABLED = False
    build = Builder()

    cached(NS, ("k",), build)
    cached(NS, ("k",), build)

    assert build.calls == 2
    settings.API_CACHE_ENABLED = True
    assert cached(NS, ("k",), build) == "data"
    assert build.calls == 3


def test_cached_enabled_by_default_when_setting_missing(settings):
    """
    Verify that caching defaults to enabled when the setting is absent.
    """
    del settings.API_CACHE_ENABLED
    build = Builder()

    cached(NS, ("k",), build)
    cached(NS, ("k",), build)

    assert build.calls == 1


def test_cached_uses_default_ttl_from_settings(settings):
    """
    Verify that the TTL from settings is applied when none is passed.
    """
    settings.API_CACHE_TTL = 42
    fake = MagicMock()
    fake.get.return_value = None

    with patch.object(cache_core, "cache", fake):
        cached(NS, ("k",), Builder())

    assert fake.set.call_args.args[2] == 42


def test_cached_explicit_ttl_overrides_default():
    """
    Verify that an explicit TTL takes precedence over the default.
    """
    fake = MagicMock()
    fake.get.return_value = None

    with patch.object(cache_core, "cache", fake):
        cached(NS, ("k",), Builder(), ttl=9)

    assert fake.set.call_args.args[2] == 9


def test_cached_read_failure_falls_back_to_builder(caplog):
    """
    Verify that a cache read error is logged and the builder result returned.
    """
    fake = MagicMock()
    fake.get.side_effect = ConnectionError("cache down")
    build = Builder("fresh")

    with patch.object(cache_core, "cache", fake), caplog.at_level(logging.WARNING):
        assert cached(NS, ("k",), build) == "fresh"

    assert build.calls == 1
    assert "Cache read failed" in caplog.text
    fake.set.assert_not_called()


def test_cached_write_failure_still_returns_data(caplog):
    """
    Verify that a cache write error is logged and does not affect the response.
    """
    fake = MagicMock()
    fake.get.return_value = None
    fake.set.side_effect = ConnectionError("cache down")

    with patch.object(cache_core, "cache", fake), caplog.at_level(logging.WARNING):
        assert cached(NS, ("k",), Builder("fresh")) == "fresh"

    assert "Cache write failed" in caplog.text



def test_invalidate_forces_rebuild():
    """
    Verify that invalidating a namespace makes cached entries unreachable.
    """
    build = Builder()
    cached(NS, ("k",), build)

    invalidate(NS)
    cached(NS, ("k",), build)

    assert build.calls == 2


def test_invalidate_changes_namespace_version():
    """
    Verify that invalidation replaces the namespace version.
    """
    before = cache_core._version(NS)

    invalidate(NS)

    assert cache_core._version(NS) != before


def test_invalidate_only_affects_given_namespaces():
    """
    Verify that other namespaces keep their cached entries.
    """
    kept, dropped = Builder("kept"), Builder("dropped")
    cached("ns:kept", ("k",), kept)
    cached("ns:dropped", ("k",), dropped)

    invalidate("ns:dropped")
    cached("ns:kept", ("k",), kept)
    cached("ns:dropped", ("k",), dropped)

    assert kept.calls == 1
    assert dropped.calls == 2


def test_invalidate_multiple_namespaces():
    """
    Verify that several namespaces can be invalidated in one call.
    """
    first, second = Builder(), Builder()
    cached("ns:one", ("k",), first)
    cached("ns:two", ("k",), second)

    invalidate("ns:one", "ns:two")
    cached("ns:one", ("k",), first)
    cached("ns:two", ("k",), second)

    assert first.calls == 2
    assert second.calls == 2


def test_invalidate_with_no_namespaces_is_noop():
    """
    Verify that calling invalidate without arguments does nothing.
    """
    build = Builder()
    cached(NS, ("k",), build)

    invalidate()
    cached(NS, ("k",), build)

    assert build.calls == 1


def test_invalidate_applies_to_every_key_in_namespace():
    """
    Verify that invalidation drops all entries under a namespace,
    whatever their key parts.
    """
    build = Builder()
    cached(NS, ("a",), build)
    cached(NS, ("b",), build)

    invalidate(NS)
    cached(NS, ("a",), build)
    cached(NS, ("b",), build)

    assert build.calls == 4


def test_invalidate_waits_for_commit_by_default(
    settings, django_capture_on_commit_callbacks
):
    """
    Verify that invalidation is deferred until the transaction commits.
    """
    settings.API_CACHE_INVALIDATE_ON_COMMIT = True
    build = Builder()
    cached(NS, ("k",), build)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        invalidate(NS)
        cached(NS, ("k",), build)
        assert build.calls == 1

    assert len(callbacks) == 1
    callbacks[0]()
    cached(NS, ("k",), build)
    assert build.calls == 2


def test_invalidate_on_commit_never_runs_after_rollback(
    settings, django_capture_on_commit_callbacks
):
    """
    Verify that a deferred invalidation that never commits leaves
    existing entries intact.
    """
    settings.API_CACHE_INVALIDATE_ON_COMMIT = True
    build = Builder()
    cached(NS, ("k",), build)

    with django_capture_on_commit_callbacks(execute=False):
        invalidate(NS)

    cached(NS, ("k",), build)
    assert build.calls == 1


def test_invalidate_on_commit_runs_when_callbacks_execute(
    settings, django_capture_on_commit_callbacks
):
    """
    Verify that executing on-commit callbacks bumps the version.
    """
    settings.API_CACHE_INVALIDATE_ON_COMMIT = True
    before = cache_core._version(NS)

    with django_capture_on_commit_callbacks(execute=True):
        invalidate(NS)

    assert cache_core._version(NS) != before


def test_invalidate_failure_is_logged_and_does_not_raise(caplog):
    """
    Verify that cache errors during invalidation are logged per namespace
    and never propagate to the caller.
    """
    fake = MagicMock()
    fake.set.side_effect = ConnectionError("cache down")

    with patch.object(cache_core, "cache", fake), caplog.at_level(logging.ERROR):
        invalidate("ns:one", "ns:two")

    assert fake.set.call_count == 2
    assert caplog.text.count("Cache invalidation failed") == 2


def test_invalidate_continues_after_one_namespace_fails():
    """
    Verify that a failure on one namespace does not stop the others.
    """
    fake = MagicMock()
    fake.set.side_effect = [ConnectionError("down"), None]

    with patch.object(cache_core, "cache", fake):
        invalidate("ns:one", "ns:two")

    assert fake.set.call_count == 2



def test_lost_version_key_makes_old_entries_unreachable():
    """
    Verify that deleting the version key does not resurrect stale data,
    because the replacement version is a new timestamp.
    """
    build = Builder()
    cached(NS, ("k",), build)

    cache.delete(cache_core._version_key(NS))
    cached(NS, ("k",), build)

    assert build.calls == 2



def version_of(namespace):
    """
    Return the current version of a cache namespace.

    Args:
        namespace: Cache namespace.

    Returns:
        int: Namespace version.
    """
    return cache_core._version(namespace)


def snapshot(*namespaces):
    """
    Record the current versions of the given namespaces.

    Args:
        *namespaces: Cache namespaces to read.

    Returns:
        dict: Mapping of namespace to version.
    """
    return {ns: version_of(ns) for ns in namespaces}


def assert_bumped(before):
    """
    Assert that every namespace in the snapshot has a new version.

    Args:
        before: Snapshot taken before the change.
    """
    for namespace, version in before.items():
        assert version_of(namespace) != version, f"{namespace} was not invalidated"


def assert_untouched(before):
    """
    Assert that every namespace in the snapshot kept its version.

    Args:
        before: Snapshot taken before the change.
    """
    for namespace, version in before.items():
        assert version_of(namespace) == version, f"{namespace} was invalidated"


def create_page(site, title="Extra Page"):
    """
    Create a page directly in the database.

    Args:
        site: Site that owns the page.
        title: Page title.

    Returns:
        Page: The created page.
    """
    return Page.objects.create(
        site=site, title=title, page_type="general",
        html_file=make_html("p.html"), css_file=make_css("p.css"),
    )


def create_video(**owner):
    """
    Create a video attached to a theme and/or site.

    Args:
        **owner: Owning relations such as site=... or theme=....

    Returns:
        Video: The created video.
    """
    return Video.objects.create(
        file=make_video("v.mp4"), poster=make_image("poster.png"), **owner
    )



def test_namespaces_for_theme_save():
    """
    Verify that saving a theme invalidates only the themes list.
    """
    theme = Theme(pk=5)

    assert namespaces_for(theme, deleted=False) == [THEMES]


def test_namespaces_for_theme_delete():
    """
    Verify that deleting a theme also invalidates its media namespaces.
    """
    theme = Theme(pk=5)

    assert namespaces_for(theme, deleted=True) == [
        THEMES, theme_images(5), theme_videos(5),
    ]


def test_namespaces_for_site_save():
    """
    Verify that saving a site invalidates the owner's list and the detail.
    """
    site = Site(pk=9, created_by_id=3)

    assert namespaces_for(site, deleted=False) == [user_sites(3), site_detail(9)]


def test_namespaces_for_site_delete():
    """
    Verify that deleting a site also invalidates its pages and media.
    """
    site = Site(pk=9, created_by_id=3)

    assert namespaces_for(site, deleted=True) == [
        user_sites(3), site_detail(9),
        site_pages(9), site_images(9), site_videos(9),
    ]


def test_namespaces_for_page():
    """
    Verify that a page change invalidates only its site's page list.
    """
    page = Page(site_id=4)

    assert namespaces_for(page, deleted=False) == [site_pages(4)]
    assert namespaces_for(page, deleted=True) == [site_pages(4)]


def test_namespaces_for_image_with_site_only():
    """
    Verify that a site image invalidates only the site images namespace.
    """
    image = Image(site_id=4)

    assert namespaces_for(image, deleted=False) == [site_images(4)]


def test_namespaces_for_image_with_theme_only():
    """
    Verify that a theme image invalidates only the theme images namespace.
    """
    image = Image(theme_id=6)

    assert namespaces_for(image, deleted=False) == [theme_images(6)]


def test_namespaces_for_image_with_theme_and_site():
    """
    Verify that an image linked to both a theme and a site invalidates both.
    """
    image = Image(theme_id=6, site_id=4)

    assert set(namespaces_for(image, deleted=False)) == {theme_images(6), site_images(4)}


def test_namespaces_for_video_with_site_only():
    """
    Verify that a site video invalidates only the site videos namespace.
    """
    assert namespaces_for(Video(site_id=4), deleted=False) == [site_videos(4)]


def test_namespaces_for_video_with_theme_only():
    """
    Verify that a theme video invalidates only the theme videos namespace.
    """
    assert namespaces_for(Video(theme_id=6), deleted=False) == [theme_videos(6)]


def test_namespaces_for_unrelated_object_is_empty():
    """
    Verify that objects outside the cached models invalidate nothing.
    """
    assert namespaces_for(object(), deleted=False) == []
    assert namespaces_for(object(), deleted=True) == []



def test_theme_save_invalidates_themes(theme):
    """
    Verify that saving a theme invalidates the themes list but not its media.
    """
    list_ns = snapshot(THEMES)
    media_ns = snapshot(theme_images(theme.pk), theme_videos(theme.pk))

    theme.name = "Renamed Theme"
    theme.save()

    assert_bumped(list_ns)
    assert_untouched(media_ns)


def test_theme_delete_invalidates_list_and_media(owner, theme_payload):
    """
    Verify that deleting a theme invalidates the list and its media caches.
    """
    doomed = ThemeService.create(created_by=owner, **theme_payload(name="Doomed Theme"))
    before = snapshot(THEMES, theme_images(doomed.pk), theme_videos(doomed.pk))

    doomed.delete()

    assert_bumped(before)


def test_theme_save_does_not_touch_site_caches(theme, site):
    """
    Verify that saving a theme leaves site caches alone.
    """
    before = snapshot(site_detail(site.pk), site_pages(site.pk))

    theme.save()

    assert_untouched(before)



def test_site_save_invalidates_owner_list_and_detail(owner, site):
    """
    Verify that saving a site invalidates the owner's list and the detail.
    """
    before = snapshot(user_sites(owner.pk), site_detail(site.pk))

    site.name = "Renamed Site"
    site.save()

    assert_bumped(before)


def test_site_save_keeps_pages_and_media_caches(site):
    """
    Verify that saving a site does not invalidate its pages or media.
    """
    before = snapshot(site_pages(site.pk), site_images(site.pk), site_videos(site.pk))

    site.save()

    assert_untouched(before)


def test_site_save_does_not_touch_other_users_lists(site, other_user):
    """
    Verify that another user's site list is unaffected.
    """
    before = snapshot(user_sites(other_user.pk))

    site.save()

    assert_untouched(before)


def test_site_delete_invalidates_everything_for_site(owner, site):
    """
    Verify that deleting a site invalidates its list, detail, pages,
    images, and videos caches.
    """
    before = snapshot(
        user_sites(owner.pk), site_detail(site.pk),
        site_pages(site.pk), site_images(site.pk), site_videos(site.pk),
    )

    site.delete()

    assert_bumped(before)


def test_site_delete_leaves_other_sites_alone(owner, theme, site):
    """
    Verify that deleting one site does not invalidate another site's caches.
    """
    keeper = SiteService.create(created_by=owner, theme=theme, name="Keeper")
    before = snapshot(
        site_detail(keeper.pk), site_pages(keeper.pk),
        site_images(keeper.pk), site_videos(keeper.pk),
    )

    site.delete()

    assert_untouched(before)



def test_page_save_invalidates_site_pages(site, page):
    """
    Verify that saving a page invalidates its site's page list only.
    """
    pages_ns = snapshot(site_pages(site.pk))
    others = snapshot(site_detail(site.pk), site_images(site.pk))

    page.meta_title = "Changed"
    page.save()

    assert_bumped(pages_ns)
    assert_untouched(others)


def test_page_create_invalidates_site_pages(site):
    """
    Verify that creating a page invalidates its site's page list.
    """
    before = snapshot(site_pages(site.pk))

    create_page(site)

    assert_bumped(before)


def test_page_delete_invalidates_site_pages(site, page):
    """
    Verify that deleting a page invalidates its site's page list.
    """
    before = snapshot(site_pages(site.pk))

    page.delete()

    assert_bumped(before)


def test_page_change_in_one_site_leaves_other_site_alone(owner, theme, site, page):
    """
    Verify that page changes only affect the owning site's cache.
    """
    other = SiteService.create(created_by=owner, theme=theme, name="Second Site")
    before = snapshot(site_pages(other.pk))

    page.save()

    assert_untouched(before)



def test_site_image_create_invalidates_site_images(site):
    """
    Verify that adding a site image invalidates the site images cache only.
    """
    images_ns = snapshot(site_images(site.pk))
    others = snapshot(site_videos(site.pk), site_pages(site.pk))

    Image.objects.create(site=site, file=make_image("a.png"))

    assert_bumped(images_ns)
    assert_untouched(others)


def test_theme_image_create_invalidates_theme_images(theme):
    """
    Verify that adding a theme image invalidates the theme images cache only.
    """
    images_ns = snapshot(theme_images(theme.pk))
    others = snapshot(theme_videos(theme.pk), THEMES)

    Image.objects.create(theme=theme, file=make_image("a.png"))

    assert_bumped(images_ns)
    assert_untouched(others)


def test_image_delete_invalidates_site_images(site):
    """
    Verify that deleting a site image invalidates the site images cache.
    """
    image = Image.objects.create(site=site, file=make_image("a.png"))
    before = snapshot(site_images(site.pk))

    image.delete()

    assert_bumped(before)


def test_page_image_invalidates_site_images(site, page):
    """
    Verify that an image attached to a page invalidates the site images cache.
    """
    before = snapshot(site_images(site.pk))

    Image.objects.create(site=site, page=page, file=make_image("a.png"))

    assert_bumped(before)



def test_site_video_create_invalidates_site_videos(site):
    """
    Verify that adding a site video invalidates the site videos cache only.
    """
    videos_ns = snapshot(site_videos(site.pk))
    others = snapshot(site_images(site.pk))

    create_video(site=site)

    assert_bumped(videos_ns)
    assert_untouched(others)


def test_theme_video_create_invalidates_theme_videos(theme):
    """
    Verify that adding a theme video invalidates the theme videos cache only.
    """
    videos_ns = snapshot(theme_videos(theme.pk))
    others = snapshot(theme_images(theme.pk))

    create_video(theme=theme)

    assert_bumped(videos_ns)
    assert_untouched(others)


def test_video_delete_invalidates_site_videos(site):
    """
    Verify that deleting a site video invalidates the site videos cache.
    """
    video = create_video(site=site)
    before = snapshot(site_videos(site.pk))

    video.delete()

    assert_bumped(before)



def test_site_delete_cascade_invalidates_page_media_caches(site, page):
    """
    Verify that cascade-deleting a site's children still invalidates caches,
    since signals fire for each deleted row.
    """
    Image.objects.create(site=site, page=page, file=make_image("a.png"))
    create_video(site=site, page=page)
    before = snapshot(site_pages(site.pk), site_images(site.pk), site_videos(site.pk))

    site.delete()

    assert_bumped(before)


def test_queryset_delete_fires_signals(site):
    """
    Verify that bulk deletes through a queryset also invalidate caches.
    """
    create_page(site, "One")
    create_page(site, "Two")
    before = snapshot(site_pages(site.pk))

    Page.objects.filter(site=site).delete()

    assert_bumped(before)



def test_cached_value_is_dropped_after_model_change(site):
    """
    Verify the full flow: a cached value is served until a model change
    invalidates its namespace.
    """
    calls = []

    def build():
        calls.append(1)
        return len(calls)

    namespace = site_pages(site.pk)

    assert cache_core.cached(namespace, ("k",), build) == 1
    assert cache_core.cached(namespace, ("k",), build) == 1

    create_page(site)

    assert cache_core.cached(namespace, ("k",), build) == 2


def test_api_page_list_reflects_new_page(auth_client, site, page):
    """
    Verify that the page list endpoint never serves a stale list after
    a page is created through the model layer.
    """
    url = f"/api/v1/sites/{site.id}/pages"
    assert len(auth_client.get(url).json()["data"]) == 1

    create_page(site, "Fresh Page")

    titles = [item["title"] for item in auth_client.get(url).json()["data"]]
    assert "Fresh Page" in titles
    assert len(titles) == 2


def test_api_page_list_reflects_delete(auth_client, site, page):
    """
    Verify that the page list endpoint drops a deleted page immediately.
    """
    url = f"/api/v1/sites/{site.id}/pages"
    assert len(auth_client.get(url).json()["data"]) == 1

    page.delete()

    assert auth_client.get(url).json()["data"] == []


def test_signals_ignore_unknown_ids_gracefully():
    """
    Verify that namespace calculation works for instances whose related
    IDs are random UUIDs that do not exist in the database.
    """
    page = Page(site_id=uuid.uuid4())

    assert namespaces_for(page, deleted=False) == [site_pages(page.site_id)]