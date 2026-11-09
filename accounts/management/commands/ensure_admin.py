"""Create the first system admin from environment variables, if missing.

Runs on every deploy (see build.sh). Does nothing when the user already
exists, so changing the variables later never resets a password.

    DJANGO_ADMIN_USERNAME=owner
    DJANGO_ADMIN_PASSWORD=<a strong password>
"""
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from accounts.roles import Role


class Command(BaseCommand):
    help = "Create the first admin account from DJANGO_ADMIN_USERNAME / DJANGO_ADMIN_PASSWORD."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_ADMIN_USERNAME", "").strip()
        password = os.environ.get("DJANGO_ADMIN_PASSWORD", "")
        if not username or not password:
            self.stdout.write("ensure_admin: variables not set, skipping.")
            return
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"ensure_admin: '{username}' already exists, nothing to do.")
            return
        candidate = User(username=username)
        try:
            validate_password(password, candidate)
        except ValidationError as err:
            raise CommandError("DJANGO_ADMIN_PASSWORD is too weak: " + " ".join(err.messages))
        user = User(username=username, first_name=username, role=Role.ADMIN,
                    is_staff=True, is_superuser=True)
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"ensure_admin: created admin '{username}'."))
