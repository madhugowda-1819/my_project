from django.apps import AppConfig


class MyAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'my_app'

    def ready(self):
        # Register lifecycle hooks only after Django's app registry is ready.
        from . import signals  # noqa: F401
