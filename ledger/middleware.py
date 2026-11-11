from django.contrib import messages
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from .engine import acting_user
from .models import LedgerError


class LedgerMiddleware:
    """Remembers who is working (for automatic entries) and turns a broken accounting rule
    into a clear message instead of an error page. The request's transaction (ATOMIC_REQUESTS)
    is already rolled back when the error reaches here, so nothing half-saved remains."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        with acting_user(user):
            return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, LedgerError):
            return None
        messages.error(request, str(exception))
        back = request.META.get("HTTP_REFERER", "")
        if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
            back = "/"
        return redirect(back or "/")
