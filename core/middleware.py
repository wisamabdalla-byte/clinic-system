from django.conf import settings
from django.utils import translation


class LanguageMiddleware:
    """Arabic unless the user picked English (cookie set by /i18n/setlang/)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        lang = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if lang not in dict(settings.LANGUAGES):
            lang = settings.LANGUAGE_CODE
        translation.activate(lang)
        request.LANGUAGE_CODE = lang
        response = self.get_response(request)
        response.headers.setdefault("Content-Language", lang)
        return response


class BranchMiddleware:
    """Attach the branches the user may work in, and the one currently chosen."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.branch = None
        request.branches = []
        if request.user.is_authenticated:
            branches = list(request.user.allowed_branches())
            chosen = request.session.get("branch_id")
            request.branches = branches
            request.branch = next(
                (b for b in branches if b.pk == chosen), branches[0] if branches else None
            )
        return self.get_response(request)
