# sites/services/preview_publish.py
"""Write a site's pages as JSON files, for preview or publish, and copy the files they use.

    <EXPORT_ROOT>/beta/preview/<site-slug>/home.json  home.css  home.js  home.og.jpg
    <EXPORT_ROOT>/beta/publish/<site-slug>/...                                          (same layout)
    <EXPORT_ROOT>/beta/sites/templates/images/<image_number>/<file>                     (shared)
    <EXPORT_ROOT>/beta/sites/templates/videos/<video_number>/<file and poster>          (shared)
"""
import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import PermissionDenied, ValidationError

from sites.exceptions import FileNotFound, SiteSlugConflict
from sites.models import Site
from sites.services.site import SiteService

logger = logging.getLogger(__name__)

COPY_CHUNK = 1024 * 1024
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def _setting(name, default):
    return getattr(settings, name, default)


def _file_name(field_file):
    return os.path.basename(field_file.name)


def _extension(field_file):
    return os.path.splitext(field_file.name)[1].lower()


def _read_text(field_file):
    with field_file.storage.open(field_file.name, "rb") as source:
        return source.read().decode("utf-8")


def _copy(field_file, destination):
    """Copy a stored file to `destination`. Returns False if the stored file is missing."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    # copy to a temporary name first, so nobody can fetch a half-copied file
    temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with field_file.storage.open(field_file.name, "rb") as source, open(temp, "wb") as target:
            shutil.copyfileobj(source, target, COPY_CHUNK)
    except FileNotFoundError:
        temp.unlink(missing_ok=True)
        logger.warning("Export: stored file %s is missing", field_file.name)
        return False
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    os.replace(temp, destination)
    return True


class PreviewPublishService:
    """Export a site's pages for preview or publish."""

    @classmethod
    def preview_and_publish(cls, *, site_id, user, is_publish=False) -> dict:
        """Export every page of a site.

        Args:
            site_id: ID of the site.
            user: User requesting the export.
            is_publish: True to write the publish folder, False for preview.

        Returns:
            dict: The mode, the relative path and the slugs of the exported pages.

        Raises:
            SiteNotFound: If the site does not exist.
            PermissionDenied: If the user is not the owner or staff.
            ValidationError: If the slug is unsafe, or a site to publish has no domain.
            SiteSlugConflict: If another site has the same slug.
            FileNotFound: If a page's HTML or CSS file is missing.
        """
        mode = "publish" if is_publish else "preview"

        site = SiteService.get(site_id=site_id)
        if not (user.is_staff or site.created_by_id == user.id):
            raise PermissionDenied(f"You can't {mode} this site.")
        cls._check_site(site, is_publish)

        written = set()
        pages = []
        for page in site.pages.order_by("slug"):
            written |= cls._build_json(page, site, mode)
            pages.append(page.slug)

        cls._export_media(site)                              # once per site, not once per page
        cls._remove_stale(cls._folder(site, mode), written)  # pages deleted since the last export

        version = _setting("EXPORT_VERSION", "beta")
        return {"mode": mode, "path": f"{version}/{mode}/{site.slug}", "pages": pages}

    # ----- one page -----

    @classmethod
    def _build_json(cls, page, site, mode) -> set:
        """Write one page's JSON and its files. Returns the names of the files written."""
        slug = page.slug
        if not SAFE_NAME.match(slug or ""):
            raise ValidationError({"slug": [f"The page slug '{slug}' can't be used in a file name."]})

        if not page.html_file:
            raise FileNotFound(f"Page '{slug}' has no HTML file.")
        try:
            html = _read_text(page.html_file)
        except (FileNotFoundError, UnicodeDecodeError):
            raise FileNotFound(f"Page '{slug}' has no readable HTML file.")

        written = set()
        css_url = cls._copy_flat(page.css_file, f"{slug}.css", site, mode, written, required=True)
        js_url = cls._copy_flat(page.js_file, f"{slug}.js", site, mode, written)
        og_name = f"{slug}.og{_extension(page.og_image)}" if page.og_image else None
        og_url = cls._copy_flat(page.og_image, og_name, site, mode, written)

        document = cls._build_document(
            page=page, html=html, canonical_url=cls._canonical_url(page, site, mode),
            css_url=css_url, js_url=js_url, og_image_url=og_url,
        )
        file_name = f"{slug}.json"
        cls._write_json(cls._folder(site, mode) / file_name, document)
        written.add(file_name)
        return written

    @classmethod
    def _copy_flat(cls, field_file, name, site, mode, written, required=False):
        """Copy a file into the site's folder as `name`. Returns its CDN URL, or None."""
        if not field_file:
            if required:
                raise FileNotFound(f"A required file is missing: {name}.")
            return None
        if not _copy(field_file, cls._folder(site, mode) / name):
            if required:
                raise FileNotFound(f"A required file is missing: {field_file.name}.")
            return None
        written.add(name)
        return f"{cls._cdn_base(site, mode)}/{name}"

    @staticmethod
    def _build_document(*, page, html, canonical_url, css_url, js_url, og_image_url) -> dict:
        """The JSON document of one page. The key order is part of the format."""
        return {
            "head": {
                "title": page.meta_title or page.title,
                "meta_info": {
                    "meta_description": page.meta_description,
                    "meta_keywords": "",
                    "og_title": page.og_title,
                    "og_description": page.og_description,
                    "og_image": og_image_url,
                    "og_url": "",
                    "page_type": page.page_type,
                },
                "js_file": js_url,
                "css_file": css_url,
                "canonical_url": canonical_url,
                "preload_images": [],
            },
            "ck_enabled": False,
            "body": {"content": html},
            "isDynamicBlogPage": False,
            "has_weather_component": False,
        }

    @staticmethod
    def _canonical_url(page, site, mode):
        if page.canonical_url:
            return page.canonical_url
        if mode == "publish":
            host = site.published_domain
        else:
            version = _setting("EXPORT_VERSION", "beta")
            host = f"{site.slug}.{version}.{_setting('EXPORT_SUBDOMAIN', '123presto')}.com"
        base = f"https://{host}"
        return base if page.page_type == "home" else f"{base}/{page.slug}"

    # ----- media -----

    @classmethod
    def _export_media(cls, site):
        media = cls._root() / _setting("EXPORT_VERSION", "beta") / "sites" / "templates"

        for image in site.image_assets.all():
            cls._copy_media(image.file, media / "images" / str(image.image_number))

        for video in site.video_assets.all():
            for field_file in (video.file, video.poster):
                cls._copy_media(field_file, media / "videos" / str(video.video_number))

    @staticmethod
    def _copy_media(field_file, folder):
        if field_file:                                       # a video's poster is optional
            _copy(field_file, folder / _file_name(field_file))

    # ----- paths and files -----

    @staticmethod
    def _root():
        root = _setting("EXPORT_ROOT", None)
        if not root:
            raise ImproperlyConfigured("Set EXPORT_ROOT to the folder exports are written to.")
        return Path(root)

    @classmethod
    def _folder(cls, site, mode):
        return cls._root() / _setting("EXPORT_VERSION", "beta") / mode / site.slug

    @staticmethod
    def _cdn_base(site, mode):
        cdn = _setting("EXPORT_CDN_BASE_URL", "https://cdn.123presto.com").rstrip("/")
        return f"{cdn}/{_setting('EXPORT_VERSION', 'beta')}/{mode}/{site.slug}"

    @staticmethod
    def _check_site(site, is_publish):
        if not SAFE_NAME.match(site.slug or ""):
            raise ValidationError({"slug": [f"The site slug '{site.slug}' can't be used in a file path."]})
        if Site.objects.filter(slug=site.slug).exclude(pk=site.pk).exists():
            raise SiteSlugConflict()
        if is_publish and not site.published_domain:
            raise ValidationError({"published_domain": ["A domain is required to publish a site."]})

    @staticmethod
    def _write_json(path, document):
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(document, ensure_ascii=False, indent=_setting("EXPORT_JSON_INDENT", 4))
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        temp.write_text(text + "\n", encoding="utf-8")
        os.replace(temp, path)                               # readers never see a half-written file

    @staticmethod
    def _remove_stale(folder, written):
        """Delete files in the site's folder that this run did not write (deleted pages)."""
        if not folder.exists():
            return
        for path in folder.iterdir():
            if path.is_file() and path.name not in written:
                path.unlink(missing_ok=True)