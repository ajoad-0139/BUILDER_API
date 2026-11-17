from django.apps import AppConfig


class SitesConfig(AppConfig):
    name = 'sites'

    def ready(self):
        from sites.utils import cache_signals  # noqa: F401