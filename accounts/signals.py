from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from core.audit import log


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log(request, "login", user=user)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log(request, "logout", user=user)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    log(request, "login_failed", username=str(credentials.get("username", ""))[:150])
