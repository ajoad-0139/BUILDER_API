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
    """Get a Django setting with a fallback value."""
    return getattr(settings, name, default)


def _file_name(field_file):
    """Return the basename of a stored file."""
    return os.path.basename(field_file.name)


def _extension(field_file):
    """Return the lowercase extension of a stored file."""
    return os.path.splitext(field_file.name)[1].lower()


def _read_text(field_file):
    """Read a stored file and decode its content as UTF-8."""
    with field_file.storage.open(field_file.name, "rb") as source:
        html = source.read().decode("utf-8")
        html = re.sub(r"\s+", " ", html)
        html = re.sub(r">\s+<", "><", html)
        
    return html.strip()

def _copy(field_file, destination):
    """Copy a stored file to a destination atomically.

    Returns:
        bool: True if the file was copied, False if the source is missing.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
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


class ExportService:
    """Export a site's pages and media for preview or publishing."""

    @classmethod
    def export(cls, *, site_id, user, is_publish=False) -> dict:
        """Export every page of a site.

        Args:
            site_id: ID of the site to export.
            user: User requesting the export.
            is_publish: Whether to export in publish mode.

        Returns:
            dict: Export mode, relative path and exported page slugs.

        Raises:
            PermissionDenied: If the user is not the owner or staff.
            ValidationError: If the site slug is unsafe.
            SiteSlugConflict: If another site has the same slug.
            FileNotFound: If a page's required HTML or CSS file is missing.
        """
        mode = "publish" if is_publish else "preview"

        site = SiteService.get(site_id=site_id)
        if not (user.is_staff or site.created_by_id == user.id):
            raise PermissionDenied(f"You can't {mode} this site.")

        cls._check_site(site, is_publish)

        written = set()
        pages = []

        written |= cls._build_header_and_footer_json(site, mode)

        for page in site.pages.order_by("slug"):
            written |= cls._build_page_json(page, site, mode)
            pages.append(page.slug)

        cls._export_media(site)
        cls._remove_stale(cls._folder(site, mode), written)

        version = _setting("EXPORT_VERSION", "beta")
        return {"mode": mode, "path": f"{version}/{mode}/{site.slug}", "pages": pages}

    @classmethod
    def _build_header_and_footer_json(cls, site, mode) ->set:
        if not site.header :
            raise FileNotFound(f"Site {site.slug} has no Header file.")

        try :
            header = _read_text(site.header)
        except (FileNotFoundError, UnicodeDecodeError) :
            raise FileNotFound(f"Site {site.slug} has not readable header file")

        try :
            footer = _read_text(site.footer)
        except (FileNotFoundError, UnicodeDecodeError) :
            raise FileNotFound(f"Site {site.slug} has not readable footer file")

        written = set()
        css_file = cls._copy_flat(site.css_file, f"{site.slug}-css-file.css", site, mode, written, required=True)
        js_file = cls._copy_flat(site.js_file, f"{site.slug}-js-file.js", site, mode, written, required=True)
        media = cls._root() / _setting("EXPORT_VERSION", "beta") / "sites" / "templates"
        og_image = cls._copy_media(site.og_image, media / "images" / str(site.number))
        favicon = cls._copy_media(site.favicon, media / "images" / str(site.number))

        document = cls._build_header_document(name=site.name, description=site.description, language=site.language,status= site.status
        , header=header, css_file=css_file, js_file=js_file, favicon=favicon, meta_description=site.meta_description, og_image=og_image)

        cls._write_json(cls._folder(site, mode) / "header.json", document)
        written.add("header.json")

        cls._write_json(cls._folder(site, mode) / "footer.json", {"footer":footer})
        written.add("footer.json")

        return written

    @staticmethod
    def _build_header_document(*,name, description, language, status, header, css_file, js_file, favicon,
     meta_description, og_image)->dict :
        return {
            "site_name":name,
            "site_description":description,
            "site_language" : language,
            "site_status" : status,
            "header" : header,
            "css_file" : css_file,
            "js_file" : js_file,
            "favicon" : favicon,
            "meta_description" : meta_description,
            "og_image" : og_image
        }

    @classmethod
    def _build_page_json(cls, page, site, mode) -> set:
        """Build and write a page's JSON document and associated files.

        Args:
            page: Page instance to export.
            site: Parent site instance.
            mode: Export mode, either preview or publish.

        Returns:
            set: Names of files written for the page.

        Raises:
            ValidationError: If the page slug is unsafe.
            FileNotFound: If the HTML file is missing or unreadable.
        """
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
        """Copy a file into the site's export folder.

        Args:
            field_file: Stored file to copy.
            name: Destination filename.
            site: Parent site instance.
            mode: Export mode.
            written: Set of successfully written filenames.
            required: Whether a missing file should raise an exception.

        Returns:
            str | None: CDN URL of the copied file, or None if unavailable.

        Raises:
            FileNotFound: If a required file is missing.
        """
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
        """Build the JSON document for a page.

        Args:
            page: Page instance containing metadata.
            html: HTML content of the page.
            canonical_url: Canonical URL of the page.
            css_url: CDN URL of the page's CSS file.
            js_url: CDN URL of the page's JavaScript file.
            og_image_url: CDN URL of the Open Graph image.

        Returns:
            dict: Page document in the required export format.
        """
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
        """Build the canonical URL for a page.

        Args:
            page: Page instance.
            site: Parent site instance.
            mode: Export mode.

        Returns:
            str: Existing canonical URL or generated page URL.
        """
        if page.canonical_url:
            return page.canonical_url

        if mode == "publish":
            host = site.published_domain
        else:
            version = _setting("EXPORT_VERSION", "beta")
            host = f"{site.slug}.{version}.{_setting('EXPORT_SUBDOMAIN', '123presto')}.com"

        base = f"https://{host}"
        return base if page.page_type == "home" else f"{base}/{page.slug}"

    @classmethod
    def _export_media(cls, site):
        """Export all image and video assets belonging to a site."""
        media = cls._root() / _setting("EXPORT_VERSION", "beta") / "sites" / "templates"

        for image in site.image_assets.all():
            cls._copy_media(image.file, media / "images" / str(site.number))

        for video in site.video_assets.all():
            for field_file in (video.file, video.poster):
                cls._copy_media(field_file, media / "videos" / str(site.number))

    @staticmethod
    def _copy_media(field_file, folder):
        """Copy a media file into its corresponding export folder."""
        if field_file:
            _copy(field_file, folder / _file_name(field_file))

    @staticmethod
    def _root():
        """Return the configured export root directory.

        Raises:
            ImproperlyConfigured: If EXPORT_ROOT is not configured.
        """
        root = _setting("EXPORT_ROOT", None)
        if not root:
            raise ImproperlyConfigured("Set EXPORT_ROOT to the folder exports are written to.")
        return Path(root)

    @classmethod
    def _folder(cls, site, mode):
        """Return the export directory for a site's mode."""
        return cls._root() / _setting("EXPORT_VERSION", "beta") / mode / site.slug

    @staticmethod
    def _cdn_base(site, mode):
        """Build the base CDN URL for a site's exported files."""
        cdn = _setting("EXPORT_CDN_BASE_URL", "https://cdn.123presto.com").rstrip("/")
        return f"{cdn}/{_setting('EXPORT_VERSION', 'beta')}/{mode}/{site.slug}"

    @staticmethod
    def _check_site(site, is_publish):
        """Validate the site's slug and check for duplicate slugs.

        Args:
            site: Site instance to validate.
            is_publish: Whether the export is for publishing.

        Raises:
            ValidationError: If the site slug is unsafe.
            SiteSlugConflict: If another site has the same slug.
        """
        if not SAFE_NAME.match(site.slug or ""):
            raise ValidationError({"slug": [f"The site slug '{site.slug}' can't be used in a file path."]})

        if Site.objects.filter(slug=site.slug).exclude(pk=site.pk).exists():
            raise SiteSlugConflict()

    @staticmethod
    def _write_json(path, document):
        """Write a JSON document to disk using an atomic file replacement."""
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(document, ensure_ascii=False, indent=_setting("EXPORT_JSON_INDENT", 4))
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        temp.write_text(text + "\n", encoding="utf-8")
        os.replace(temp, path)

    @staticmethod
    def _remove_stale(folder, written):
        """Remove files from the export folder that were not written in this run.

        Args:
            folder: Site export directory to clean.
            written: Set of filenames written during the current export.
        """
        if not folder.exists():
            return

        for path in folder.iterdir():
            if path.is_file() and path.name not in written:
                path.unlink(missing_ok=True)