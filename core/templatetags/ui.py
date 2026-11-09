from decimal import Decimal, InvalidOperation

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from accounts.roles import has_perm
from core.i18n import tr

register = template.Library()


@register.simple_tag
def t(key, *args):
    return tr(key, *args)


@register.filter
def can(user, spec):
    """{% if user|can:"services.edit" %}"""
    module, _, action = spec.partition(".")
    return has_perm(user, module, action or "view")


@register.filter
def money(value):
    """1,500 for whole amounts, 12.75 otherwise."""
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value
    if amount == amount.to_integral_value():
        return f"{amount:,.0f}"
    return f"{amount:,.2f}"


@register.simple_tag(takes_context=True)
def qs(context, **changes):
    """Current query string with some parameters replaced: {% qs page=2 %}"""
    params = context["request"].GET.copy()
    for key, value in changes.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


# Simple line icons (24x24, stroke-based).
ICONS = {
    "home": '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    "calendar": '<rect x="3" y="4.5" width="18" height="17" rx="2"/><path d="M16 2.5v4M8 2.5v4M3 10h18"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20v-1a5 5 0 0 1 5-5h3a5 5 0 0 1 5 5v1"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18.5 14a5 5 0 0 1 3 4.6V20"/>',
    "clipboard": '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3h6v1M9 10h6M9 14h6M9 18h3"/>',
    "activity": '<path d="M22 12h-4l-3 8-6-16-3 8H2"/>',
    "tag": '<path d="M12.6 2.6H3v9.6l9 9 9.6-9.6z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
    "package": '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
    "building": '<rect x="4" y="2.5" width="16" height="19" rx="1.5"/><path d="M9 21.5V17h6v4.5M8.5 7h1M14.5 7h1M8.5 11h1M14.5 11h1"/>',
    "wallet": '<rect x="2.5" y="5.5" width="19" height="14" rx="2"/><path d="M2.5 10h19M16 14.5h2"/>',
    "archive": '<rect x="2.5" y="3.5" width="19" height="5" rx="1"/><path d="M4.5 8.5V19a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5V8.5M10 12.5h4"/>',
    "monitor": '<rect x="2.5" y="3.5" width="19" height="13" rx="2"/><path d="M8 21h8M12 16.5V21"/>',
    "idcard": '<rect x="2.5" y="5" width="19" height="14" rx="2"/><circle cx="8.5" cy="11" r="2"/><path d="M5.5 16a3 3 0 0 1 6 0M14 10h4M14 14h3"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M8 17v-5M13 17V8M18 17v-8"/>',
    "shield": '<path d="M12 21.5s8-3.8 8-10V5l-8-2.5L4 5v6.5c0 6.2 8 10 8 10z"/>',
    "menu": '<path d="M3 6h18M3 12h18M3 18h18"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20.5 20.5-4.2-4.2"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
    "globe": '<circle cx="12" cy="12" r="9.5"/><path d="M2.5 12h19M12 2.5c2.5 2.6 3.8 5.8 3.8 9.5s-1.3 6.9-3.8 9.5c-2.5-2.6-3.8-5.8-3.8-9.5S9.5 5.1 12 2.5z"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m14 6 4 4"/>',
    "key": '<circle cx="8" cy="15" r="4.5"/><path d="m11.5 11.5 9-9M17 6l3 3"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
    "lock": '<rect x="4" y="10.5" width="16" height="11" rx="2"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "tooth": '<path d="M12 5.5c-1.5-1.3-3.2-2-5-1.6C4.6 4.4 3.5 6.6 4 9.5c.5 3 1.6 4.4 2.2 7.5.4 2 .9 3.5 2 3.5 1.6 0 1.4-5 3.8-5s2.2 5 3.8 5c1.1 0 1.6-1.5 2-3.5.6-3.1 1.7-4.5 2.2-7.5.5-2.9-.6-5.1-3-5.6-1.8-.4-3.5.3-5 1.6z"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9.5"/><path d="m8 12.5 2.7 2.7L16.5 9"/>',
    "login": '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="m10 17 5-5-5-5M15 12H3"/>',
    "play": '<path d="M7 4.5v15l12-7.5z"/>',
    "user-x": '<circle cx="9" cy="8" r="4"/><path d="M2 21v-1a6 6 0 0 1 6-6h2a6 6 0 0 1 4 1.5M17 14l5 5M22 14l-5 5"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "tv": '<rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
    "phone": '<path d="M21.5 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 1.6 4.2 2 2 0 0 1 3.6 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L7.6 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z"/>',
    "alert": '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>',
    "chevron-start": '<path d="m15 6-6 6 6 6"/>',
    "chevron-end": '<path d="m9 6 6 6-6 6"/>',
    "expand": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    "card": '<rect x="2.5" y="5" width="19" height="14" rx="2"/><path d="M2.5 10h19M6.5 15h4"/>',
    "receipt": '<path d="M5 2.5h14v19l-2.5-1.5-2.5 1.5-2-1.5-2 1.5-2.5-1.5L5 21.5z"/><path d="M8.5 7.5h7M8.5 11.5h7M8.5 15.5h4"/>',
    "percent": '<path d="M19 5 5 19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
    "send": '<path d="M21.5 2.5 11 13M21.5 2.5 15 21.5l-4-8.5-8.5-4z"/>',
    "minus-circle": '<circle cx="12" cy="12" r="9.5"/><path d="M8 12h8"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "printer": '<path d="M6 9V2.5h12V9"/><rect x="2.5" y="9" width="19" height="8" rx="2"/><path d="M6 14h12v7.5H6z"/>',
    "camera": '<path d="M3 8a2 2 0 0 1 2-2h2.5l1.5-2.5h6L16.5 6H19a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><circle cx="12" cy="13" r="3.5"/>',
    "pill": '<rect x="2" y="8.5" width="20" height="7" rx="3.5" transform="rotate(-45 12 12)"/><path d="m8.5 8.5 7 7"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
    "trash": '<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"/>',
    "sparkle": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
}


@register.simple_tag
def icon(name, size=20):
    body = ICONS.get(name, "")
    return format_html(
        '<svg class="icon" width="{0}" height="{0}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{1}</svg>',
        size,
        mark_safe(body),
    )
