from django.apps import AppConfig


class SentinelConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sentinel'
    verbose_name = 'Sentinel Vault'

    def ready(self):
        # Wiring auth + model signals happens on import; keep it inside ready()
        # so app registry is fully populated before we walk it in registry.py.
        from . import signals  # noqa: F401
