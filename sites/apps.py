from django.apps import AppConfig


class SitesConfig(AppConfig):
    """Configure the sites application."""

    name = 'sites'

    def ready(self):
        """Import cache signals when the application is ready."""
        from sites.cache import cache_signals  # noqa: F401