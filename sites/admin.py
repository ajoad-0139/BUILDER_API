import os

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db.models import Count
from django.utils import timezone
from django.utils.html import format_html
from django.utils.text import slugify

from sites.utils.constraints import SUPPORTED_LANGUAGES
from .models import Image, Page, Site, Theme, Video

admin.site.site_header = "Presto administration"
admin.site.site_title = "Presto admin"

TEMPLATE_FILE_FIELDS = ("header", "footer", "body", "css_file", "js_file")
REQUIRED_TEMPLATE_FIELDS = ("header", "footer", "body", "css_file")



def human_size(size):
    """Format a file size into a readable string.

    Args:
        size: File size in bytes.

    Returns:
        str: Formatted file size.
    """
    if size is None:
        return "-"
    size = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def image_tag(field_file, height=60):
    """Generate an HTML image preview.

    Args:
        field_file: Image file.
        height: Maximum image height in pixels.

    Returns:
        str: HTML image tag or placeholder.
    """
    if not field_file:
        return "-"
    return format_html(
        '<img src="{}" style="max-height:{}px;border-radius:4px;">', field_file.url, height
    )


def copy_file(field_file):
    """Create an independent copy of a stored file.

    Args:
        field_file: Source file.

    Returns:
        ContentFile: Copied file, or None if empty.
    """
    if not field_file:
        return None
    field_file.open("rb")
    try:
        content = field_file.read()
    finally:
        field_file.close()
    return ContentFile(content, name=os.path.basename(field_file.name))


def unique_site_slug(name, exclude_pk=None):
    """Generate a unique slug for a site.

    Args:
        name: Site name.
        exclude_pk: Optional site ID to exclude.

    Returns:
        str: Unique site slug.
    """
    base = slugify(name) or "site"
    slug, counter = base, 1
    while Site.objects.filter(slug=slug).exclude(pk=exclude_pk).exists():
        slug = f"{base}-{counter}"
        counter += 1
    return slug



class LanguageFormMixin:
    """Validate and normalize the selected language."""

    def clean_language(self):
        """Validate the selected language.

        Returns:
            str: Normalized language code.

        Raises:
            ValidationError: If the language is unsupported.
        """
        value = self.cleaned_data["language"].strip().lower()
        if value not in SUPPORTED_LANGUAGES:
            raise forms.ValidationError(
                f"Unsupported language. Choose from: {', '.join(sorted(SUPPORTED_LANGUAGES))}."
            )
        return value


class ThemeAdminForm(LanguageFormMixin, forms.ModelForm):
    """Validate theme data in the admin form."""

    class Meta:
        model = Theme
        fields = "__all__"

    def clean_name(self):
        """Validate and normalize the theme name.

        Returns:
            str: Normalized theme name.

        Raises:
            ValidationError: If the name is too short.
        """
        value = self.cleaned_data["name"].strip()
        if len(value) < 3:
            raise forms.ValidationError("Name must be at least 3 characters.")
        return value

    def clean(self):
        """Validate theme name uniqueness.

        Returns:
            dict: Validated form data.
        """
        cleaned = super().clean()
        name, language = cleaned.get("name"), cleaned.get("language")
        if name and language:
            duplicates = Theme.objects.filter(name__iexact=name, language=language)
            if duplicates.exclude(pk=self.instance.pk).exists():
                self.add_error("name", "A theme with this name already exists for this language.")
        return cleaned


class SiteAdminForm(LanguageFormMixin, forms.ModelForm):
    """Validate site data in the admin form."""

    class Meta:
        model = Site
        fields = "__all__"

    def clean_name(self):
        """Validate and normalize the site name.

        Returns:
            str: Normalized site name.

        Raises:
            ValidationError: If the name is too short.
        """
        value = self.cleaned_data["name"].strip()
        if len(value) < 3:
            raise forms.ValidationError("Name must be at least 3 characters.")
        return value

    def clean_published_domain(self):
        """Validate and normalize the published domain.

        Returns:
            str: Normalized domain or None.

        Raises:
            ValidationError: If the domain is already used.
        """
        value = (self.cleaned_data.get("published_domain") or "").strip().lower()
        if not value:
            return None          
        taken = Site.objects.filter(published_domain__iexact=value)
        if taken.exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A site with this domain already exists.")
        return value

    def clean(self):
        """Validate published site requirements.

        Returns:
            dict: Validated form data.
        """
        cleaned = super().clean()
        if cleaned.get("status") == Site.Status.PUBLISHED and not cleaned.get("published_domain"):
            self.add_error("published_domain", "A domain is required to publish a site.")
        return cleaned


class PageAdminForm(forms.ModelForm):
    """Validate page data in the admin form."""

    class Meta:
        model = Page
        fields = "__all__"

    def clean_title(self):
        """Validate and normalize the page title.

        Returns:
            str: Normalized page title.

        Raises:
            ValidationError: If the title is too short.
        """
        value = self.cleaned_data["title"].strip()
        if len(value) < 2:
            raise forms.ValidationError("Title must be at least 2 characters.")
        return value

    def clean_page_type(self):
        """Normalize the page type.

        Returns:
            str: Normalized page type.
        """
        return self.cleaned_data["page_type"].strip().lower()

    def clean(self):
        """Validate page title and slug uniqueness.

        Returns:
            dict: Validated form data.
        """
        cleaned = super().clean()
        site, title = cleaned.get("site"), cleaned.get("title")
        if not site or not title:
            return cleaned

        typed_slug = cleaned.get("slug")
        slug = typed_slug or slugify(title)
        others = Page.objects.filter(site=site).exclude(pk=self.instance.pk)

        if others.filter(title__iexact=title).exists():
            self.add_error("title", "A page with this title already exists in this site.")
        elif slug and others.filter(slug=slug).exists():
            self.add_error(
                "slug" if typed_slug else "title",
                "A page with this URL (slug) already exists in this site.",
            )
        return cleaned


class MediaOwnerFormMixin:
    """Validate media ownership by theme, site, or page."""

    def clean(self):
        """Validate media ownership selection.

        Returns:
            dict: Validated form data.

        Raises:
            ValidationError: If ownership is missing or conflicting.
        """
        cleaned = super().clean()
        theme, site, page = (cleaned.get(key) for key in ("theme", "site", "page"))

        if theme and (site or page):
            raise ValidationError("A theme's media can't also belong to a site or a page.")
        if not (theme or site or page):
            raise ValidationError("Choose an owner: a theme, or a site (optionally with a page).")
        return cleaned


class ImageAdminForm(MediaOwnerFormMixin, forms.ModelForm):
    """Validate image ownership in the admin form."""

    class Meta:
        model = Image
        fields = "__all__"


class VideoAdminForm(MediaOwnerFormMixin, forms.ModelForm):
    """Validate video ownership in the admin form."""

    class Meta:
        model = Video
        fields = "__all__"



class OwnedAdminMixin:
    """Default the owner to the logged-in user and prevent later changes."""

    def get_changeform_initial_data(self, request):
        """Set the default owner for new objects.

        Args:
            request: Current admin request.

        Returns:
            dict: Initial form data.
        """
        initial = super().get_changeform_initial_data(request)
        initial.setdefault("created_by", request.user.pk)
        return initial

    def get_readonly_fields(self, request, obj=None):
        """Make the owner read-only for existing objects.

        Args:
            request: Current admin request.
            obj: Existing object, if editing.

        Returns:
            list: Read-only field names.
        """
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.append("created_by")
        return fields


class OwnerTypeFilter(admin.SimpleListFilter):
    """Filter media by its owner type."""

    title = "belongs to"
    parameter_name = "owner"

    def lookups(self, request, model_admin):
        """Return available owner filter options.

        Args:
            request: Current admin request.
            model_admin: Admin class using the filter.

        Returns:
            list: Available filter choices.
        """
        return [("theme", "Theme"), ("site", "Site"), ("page", "Site page")]

    def queryset(self, request, queryset):
        """Filter media by the selected owner type.

        Args:
            request: Current admin request.
            queryset: Media queryset.

        Returns:
            QuerySet: Filtered media queryset.
        """
        if self.value() == "theme":
            return queryset.filter(theme__isnull=False)
        if self.value() == "site":
            return queryset.filter(site__isnull=False, page__isnull=True)
        if self.value() == "page":
            return queryset.filter(page__isnull=False)
        return queryset


class MediaAdminMixin:
    """Provide shared media admin configuration and display methods."""

    ordering = ("-created_at",)
    list_select_related = ("theme", "site", "page")
    autocomplete_fields = ("theme", "site", "page")

    @admin.display(description="Belongs to")
    def owner(self, obj):
        """Return the media owner label.

        Args:
            obj: Media instance.

        Returns:
            str: Owner description.
        """
        if obj.theme_id:
            return f"Theme: {obj.theme}"
        if obj.page_id:
            return f"Page: {obj.page} ({obj.site})"
        return f"Site: {obj.site}"

    @admin.display(description="Size", ordering="size")
    def file_size(self, obj):
        """Format the media file size.

        Args:
            obj: Media instance.

        Returns:
            str: Formatted file size.
        """
        return human_size(obj.size)



class ImageInlineBase(admin.TabularInline):
    """Provide shared image inline configuration."""

    model = Image
    extra = 0
    fields = ("file", "alt_text", "preview")
    readonly_fields = ("preview",)

    @admin.display(description="Preview")
    def preview(self, obj):
        """Display an image preview.

        Args:
            obj: Image instance.

        Returns:
            str: HTML preview or placeholder.
        """
        return image_tag(obj.file, 50) if obj and obj.file else "-"


class VideoInlineBase(admin.TabularInline):
    """Provide shared video inline configuration."""

    model = Video
    extra = 0
    fields = ("file", "title", "duration", "poster")


class ThemeImageInline(ImageInlineBase):
    """Display theme images inline."""

    fk_name = "theme"


class ThemeVideoInline(VideoInlineBase):
    """Display theme videos inline."""

    fk_name = "theme"


class SiteImageInline(ImageInlineBase):
    """Display site images inline."""

    fk_name = "site"
    fields = ("file", "alt_text", "page", "preview")
    readonly_fields = ("page", "preview")     


class SiteVideoInline(VideoInlineBase):
    """Display site videos inline."""

    fk_name = "site"
    fields = ("file", "title", "duration", "poster", "page")
    readonly_fields = ("page",)


class PageImageInline(ImageInlineBase):
    """Display page images inline."""

    fk_name = "page"


class PageVideoInline(VideoInlineBase):
    """Display page videos inline."""

    fk_name = "page"


class SitePagesInline(admin.TabularInline):
    """Display a read-only list of a site's pages."""

    model = Page
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ("title", "slug", "page_type", "no_index")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        """Disable adding pages from the site screen.

        Args:
            request: Current admin request.
            obj: Site instance.

        Returns:
            bool: Always False.
        """
        return False



@admin.register(Theme)
class ThemeAdmin(OwnedAdminMixin, admin.ModelAdmin):
    """Manage themes through the Django admin."""

    form = ThemeAdminForm
    list_display = ("name", "language", "is_active", "sites_count", "created_by", "created_at", "thumb")
    list_filter = ("is_active", "language", "created_at")
    list_editable = ("is_active",)
    search_fields = ("name", "slug", "description")
    ordering = ("-created_at",)
    list_select_related = ("created_by",)
    raw_id_fields = ("created_by",)
    readonly_fields = ("id", "created_at", "updated_at", "thumbnail_preview")
    inlines = (ThemeImageInline, ThemeVideoInline)
    actions = ("activate", "deactivate")
    fieldsets = (
        ("General", {"fields": ("name", "slug", "description", "language", "is_active", "created_by")}),
        ("Files", {"fields": ("thumbnail", "thumbnail_preview", "header", "footer", "body", "css_file", "js_file")}),
        ("Info", {"fields": ("id", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        """Annotate themes with their site counts.

        Args:
            request: Current admin request.

        Returns:
            QuerySet: Annotated theme queryset.
        """
        return super().get_queryset(request).annotate(_sites_count=Count("sites", distinct=True))

    @admin.display(description="Sites", ordering="_sites_count")
    def sites_count(self, obj):
        """Return the number of related sites.

        Args:
            obj: Theme instance.

        Returns:
            int: Number of sites.
        """
        return obj._sites_count

    @admin.display(description="Thumbnail")
    def thumb(self, obj):
        """Display the theme thumbnail.

        Args:
            obj: Theme instance.

        Returns:
            str: HTML thumbnail preview.
        """
        return image_tag(obj.thumbnail, 40)

    @admin.display(description="Thumbnail preview")
    def thumbnail_preview(self, obj):
        """Display a larger theme thumbnail.

        Args:
            obj: Theme instance.

        Returns:
            str: HTML thumbnail preview or placeholder.
        """
        return image_tag(obj.thumbnail, 120) if obj and obj.thumbnail else "-"

    @admin.action(description="Activate selected themes")
    def activate(self, request, queryset):
        """Activate the selected themes.

        Args:
            request: Current admin request.
            queryset: Selected themes.
        """
        updated = queryset.update(is_active=True, updated_at=timezone.now())
        self.message_user(request, f"{updated} theme(s) activated.")

    @admin.action(description="Deactivate selected themes")
    def deactivate(self, request, queryset):
        """Deactivate the selected themes.

        Args:
            request: Current admin request.
            queryset: Selected themes.
        """
        updated = queryset.update(is_active=False, updated_at=timezone.now())
        self.message_user(request, f"{updated} theme(s) deactivated.")



@admin.register(Site)
class SiteAdmin(OwnedAdminMixin, admin.ModelAdmin):
    """Manage sites through the Django admin."""

    form = SiteAdminForm
    list_display = ("name", "status", "language", "published_domain", "theme", "pages_count", "created_by", "created_at")
    list_filter = ("status", "language", "theme", "created_at")
    search_fields = ("name", "slug", "published_domain")
    ordering = ("-created_at",)
    list_select_related = ("theme", "created_by")
    autocomplete_fields = ("theme",)
    raw_id_fields = ("created_by",)
    readonly_fields = ("id", "created_at", "updated_at", "favicon_preview")
    inlines = (SitePagesInline, SiteImageInline, SiteVideoInline)
    actions = ("publish", "mark_draft", "mark_archived")
    fieldsets = (
        ("General", {"fields": ("name", "slug", "description", "language", "status", "published_domain", "theme", "created_by")}),
        ("SEO", {"fields": ("meta_description", "og_image", "favicon", "favicon_preview", "no_index")}),
        ("Layout files", {
            "fields": TEMPLATE_FILE_FIELDS,
            "description": "On a new site, leave these empty to copy them from the theme.",
        }),
        ("Info", {"fields": ("id", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        """Annotate sites with their page counts.

        Args:
            request: Current admin request.

        Returns:
            QuerySet: Annotated site queryset.
        """
        return super().get_queryset(request).annotate(_pages_count=Count("pages", distinct=True))

    def get_readonly_fields(self, request, obj=None):
        """Make the theme read-only for existing sites.

        Args:
            request: Current admin request.
            obj: Existing site, if editing.

        Returns:
            list: Read-only field names.
        """
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.append("theme")          
        return fields

    def get_form(self, request, obj=None, **kwargs):
        """Make required template files optional for new sites.

        Args:
            request: Current admin request.
            obj: Existing site, if editing.
            **kwargs: Additional form arguments.

        Returns:
            ModelForm: Configured admin form.
        """
        form = super().get_form(request, obj, **kwargs)
        if obj is None:
            for field in REQUIRED_TEMPLATE_FIELDS:
                if field in form.base_fields:
                    form.base_fields[field].required = False
        return form

    def save_model(self, request, obj, form, change):
        """Generate the site slug and copy missing theme files.

        Args:
            request: Current admin request.
            obj: Site instance.
            form: Submitted admin form.
            change: Whether an existing site is being updated.
        """
        if not obj.slug:
            obj.slug = unique_site_slug(obj.name, exclude_pk=obj.pk)

        if not change:
            for field in TEMPLATE_FILE_FIELDS:
                if not getattr(obj, field):
                    setattr(obj, field, copy_file(getattr(obj.theme, field)))

        super().save_model(request, obj, form, change)

    @admin.display(description="Pages", ordering="_pages_count")
    def pages_count(self, obj):
        """Return the number of related pages.

        Args:
            obj: Site instance.

        Returns:
            int: Number of pages.
        """
        return obj._pages_count

    @admin.display(description="Favicon")
    def favicon_preview(self, obj):
        """Display the site favicon.

        Args:
            obj: Site instance.

        Returns:
            str: HTML favicon preview or placeholder.
        """
        return image_tag(obj.favicon, 40) if obj and obj.favicon else "-"

    @admin.action(description="Publish selected sites (needs a domain)")
    def publish(self, request, queryset):
        """Publish sites that have a domain.

        Args:
            request: Current admin request.
            queryset: Selected sites.
        """
        publishable = queryset.exclude(published_domain__isnull=True).exclude(published_domain="")
        skipped = queryset.count() - publishable.count()
        updated = publishable.update(status=Site.Status.PUBLISHED, updated_at=timezone.now())
        self.message_user(request, f"{updated} site(s) published.")
        if skipped:
            self.message_user(request, f"{skipped} site(s) skipped: no domain set.", level="warning")

    @admin.action(description="Mark selected sites as draft")
    def mark_draft(self, request, queryset):
        """Mark the selected sites as drafts.

        Args:
            request: Current admin request.
            queryset: Selected sites.
        """
        updated = queryset.update(status=Site.Status.DRAFT, updated_at=timezone.now())
        self.message_user(request, f"{updated} site(s) set to draft.")

    @admin.action(description="Archive selected sites")
    def mark_archived(self, request, queryset):
        """Archive the selected sites.

        Args:
            request: Current admin request.
            queryset: Selected sites.
        """
        updated = queryset.update(status=Site.Status.ARCHIVED, updated_at=timezone.now())
        self.message_user(request, f"{updated} site(s) archived.")



@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    """Manage pages through the Django admin."""

    form = PageAdminForm
    list_display = ("title", "site", "page_type", "slug", "no_index", "created_at")
    list_filter = ("page_type", "no_index", "site", "created_at")
    search_fields = ("title", "slug", "page_type", "site__name")
    ordering = ("-created_at",)
    list_select_related = ("site",)
    autocomplete_fields = ("site",)
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = (PageImageInline, PageVideoInline)
    fieldsets = (
        ("Content", {"fields": ("site", "title", "slug", "page_type", "canonical_url")}),
        ("SEO", {"fields": ("meta_title", "meta_description", "og_title", "og_description", "og_image", "no_index")}),
        ("Files", {"fields": ("html_file", "css_file", "js_file")}),
        ("Info", {"fields": ("id", "created_at", "updated_at")}),
    )

    def get_readonly_fields(self, request, obj=None):
        """Make the site read-only for existing pages.

        Args:
            request: Current admin request.
            obj: Existing page, if editing.

        Returns:
            list: Read-only field names.
        """
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.append("site")           
        return fields



@admin.register(Image)
class ImageAdmin(MediaAdminMixin, admin.ModelAdmin):
    """Manage images through the Django admin."""

    form = ImageAdminForm
    list_display = ("preview", "alt_text", "owner", "dimensions", "file_size", "created_at")
    list_display_links = ("preview", "alt_text")
    list_filter = (OwnerTypeFilter, "created_at")
    search_fields = ("alt_text", "file", "theme__name", "site__name", "page__title")
    readonly_fields = ("id", "preview", "width", "height", "size", "created_at", "updated_at")
    fieldsets = (
        ("Belongs to", {"fields": ("theme", "site", "page")}),
        ("File", {"fields": ("file", "preview", "alt_text")}),
        ("Info", {"fields": ("id", "width", "height", "size", "created_at", "updated_at")}),
    )

    @admin.display(description="Preview")
    def preview(self, obj):
        """Display the image preview.

        Args:
            obj: Image instance.

        Returns:
            str: HTML preview or placeholder.
        """
        return image_tag(obj.file, 60) if obj and obj.file else "-"

    @admin.display(description="Dimensions")
    def dimensions(self, obj):
        """Return the image dimensions.

        Args:
            obj: Image instance.

        Returns:
            str: Image width and height.
        """
        return f"{obj.width} x {obj.height}" if obj.width and obj.height else "-"



@admin.register(Video)
class VideoAdmin(MediaAdminMixin, admin.ModelAdmin):
    """Manage videos through the Django admin."""

    form = VideoAdminForm
    list_display = ("poster_preview", "title", "owner", "duration_display", "file_size", "created_at")
    list_display_links = ("poster_preview", "title")
    list_filter = (OwnerTypeFilter, "created_at")
    search_fields = ("title", "file", "theme__name", "site__name", "page__title")
    readonly_fields = ("id", "poster_preview", "size", "created_at", "updated_at")
    fieldsets = (
        ("Belongs to", {"fields": ("theme", "site", "page")}),
        ("File", {"fields": ("file", "title", "duration", "poster", "poster_preview")}),
        ("Info", {"fields": ("id", "size", "created_at", "updated_at")}),
    )

    @admin.display(description="Poster")
    def poster_preview(self, obj):
        """Display the video poster.

        Args:
            obj: Video instance.

        Returns:
            str: HTML poster preview or placeholder.
        """
        return image_tag(obj.poster, 60) if obj and obj.poster else "-"

    @admin.display(description="Duration", ordering="duration")
    def duration_display(self, obj):
        """Format the video duration.

        Args:
            obj: Video instance.

        Returns:
            str: Duration in minutes and seconds.
        """
        if obj.duration is None:
            return "-"
        minutes, seconds = divmod(obj.duration, 60)
        return f"{minutes}:{seconds:02d}"
