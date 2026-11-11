from django.core.management.base import BaseCommand

from ledger.posting import sync_all_finance


class Command(BaseCommand):
    help = "Create journal entries for finance documents written before the ledger existed (safe to repeat)."

    def handle(self, *args, **options):
        n = sync_all_finance()
        self.stdout.write(self.style.SUCCESS(f"{n} entries created."))
