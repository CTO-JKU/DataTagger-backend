from django.apps import AppConfig


class ElabftwConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fdm.elabftw"

    def ready(self):
        # import signal handlers
        import fdm.elabftw.handlers  # noqa: F401
