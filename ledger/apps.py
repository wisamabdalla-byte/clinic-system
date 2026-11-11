from django.apps import AppConfig


class LedgerConfig(AppConfig):
    name = "ledger"
    verbose_name = "General ledger"

    def ready(self):
        from . import signals  # noqa: F401
