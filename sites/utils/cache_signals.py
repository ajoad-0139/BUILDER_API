# sites/cache_signals.py
"""
Invalidate the response cache whenever a row changes, from anywhere: the API, the admin,
a shell, or a cascade (deleting a site deletes its pages and media, and each of those
sends its own signal).

Not covered, because Django sends no signals for them: queryset.update(), bulk_create()
and raw SQL. Call invalidate() yourself after those.
"""
from django.db.models.signals import post_delete, post_save

from sites.utils.cache_core import (
    THEMES, invalidate, site_detail, site_images, site_pages, site_videos,
    theme_images, theme_videos, user_sites,
)
from sites.models import Image, Page, Site, Theme, Video

MODELS = (Theme, Site, Page, Image, Video)


def _media_namespaces(instance, theme_ns, site_ns):
    names = []
    if instance.theme_id:
        names.append(theme_ns(instance.theme_id))
    if instance.site_id:
        names.append(site_ns(instance.site_id))
    return names


def namespaces_for(instance, deleted):
    if isinstance(instance, Theme):
        names = [THEMES]
        if deleted:
            names += [theme_images(instance.pk), theme_videos(instance.pk)]
        return names

    if isinstance(instance, Site):
        names = [user_sites(instance.created_by_id), site_detail(instance.pk)]
        if deleted:
            names += [site_pages(instance.pk), site_images(instance.pk), site_videos(instance.pk)]
        return names

    if isinstance(instance, Page):
        return [site_pages(instance.site_id)]

    if isinstance(instance, Image):
        return _media_namespaces(instance, theme_images, site_images)

    if isinstance(instance, Video):
        return _media_namespaces(instance, theme_videos, site_videos)

    return []


def _on_save(sender, instance, **kwargs):
    invalidate(*namespaces_for(instance, deleted=False))


def _on_delete(sender, instance, **kwargs):
    invalidate(*namespaces_for(instance, deleted=True))


for _model in MODELS:
    post_save.connect(_on_save, sender=_model, dispatch_uid=f"cache-save-{_model.__name__}")
    post_delete.connect(_on_delete, sender=_model, dispatch_uid=f"cache-delete-{_model.__name__}")