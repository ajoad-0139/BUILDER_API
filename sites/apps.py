from django.apps import AppConfig


class SitesConfig(AppConfig):
    name = 'sites'

    def ready(self):
        from sites.cache import cache_signals  # noqa: F401