"""
Invalidate response caches when model rows change.
Signals cover API, admin, shell, and cascade operations.
"""

from django.db.models.signals import post_delete, post_save

from sites.cache.cache_core import (
    THEMES, invalidate, site_detail, site_images, site_pages, site_videos,
    theme_images, theme_videos, user_sites,
)
from sites.models import Image, Page, Site, Theme, Video

MODELS = (Theme, Site, Page, Image, Video)


def _media_namespaces(instance, theme_ns, site_ns):
    """Get cache namespaces for media relationships.

    Args:
        instance: Media model instance.
        theme_ns: Theme namespace function.
        site_ns: Site namespace function.

    Returns:
        list: Related cache namespaces.
    """
    names = []
    if instance.theme_id:
        names.append(theme_ns(instance.theme_id))
    if instance.site_id:
        names.append(site_ns(instance.site_id))
    return names


def namespaces_for(instance, deleted):
    """Get cache namespaces affected by a model change.

    Args:
        instance: Changed model instance.
        deleted: Whether the instance was deleted.

    Returns:
        list: Cache namespaces to invalidate.
    """
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
    """Invalidate caches after a model is saved.

    Args:
        sender: Model class that triggered the signal.
        instance: Saved model instance.
        **kwargs: Additional signal arguments.
    """
    invalidate(*namespaces_for(instance, deleted=False))


def _on_delete(sender, instance, **kwargs):
    """Invalidate caches after a model is deleted.

    Args:
        sender: Model class that triggered the signal.
        instance: Deleted model instance.
        **kwargs: Additional signal arguments.
    """
    invalidate(*namespaces_for(instance, deleted=True))


for _model in MODELS:
    post_save.connect(_on_save, sender=_model, dispatch_uid=f"cache-save-{_model.__name__}")
    post_delete.connect(_on_delete, sender=_model, dispatch_uid=f"cache-delete-{_model.__name__}")
