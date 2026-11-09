"""Reusable building blocks for the list and form pages of every module."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.urls import reverse
from django.views.generic import ListView

from accounts.roles import has_perm

from .audit import log
from .i18n import tr


class PermissionMixin(LoginRequiredMixin):
    module = None
    action = "view"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not has_perm(request.user, self.module, self.action):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class Col:
    """One table column: header key, how to read the value, how to show it."""

    def __init__(self, header, get, kind="text", sub=None):
        self.header, self.get, self.kind, self.sub = header, get, kind, sub

    def cell(self, obj):
        return {
            "kind": self.kind,
            "value": self.get(obj),
            "sub": self.sub(obj) if self.sub else "",
        }


class CrudListView(PermissionMixin, ListView):
    """Searchable, filterable, paginated table."""

    template_name = "crud/list.html"
    paginate_by = 25
    title_key = ""
    subtitle_key = ""
    columns = []
    search_fields = []
    filters = []  # (GET param, model lookup, choices)
    has_status = True
    create_url = None
    edit_url = None
    row_actions = []  # (label key, url name, icon, module.action)
    header_links = []  # (label key, url name, icon, module.action)

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.GET
        q = params.get("q", "").strip()
        if q and self.search_fields:
            cond = Q()
            for field in self.search_fields:
                cond |= Q(**{f"{field}__icontains": q})
            qs = qs.filter(cond)
        for param, lookup, choices in self.filters:
            value = params.get(param, "")
            if value and value in {str(c[0]) for c in choices}:
                qs = qs.filter(**{lookup: value})
        if self.has_status:
            status = params.get("status", "active")
            if status == "active":
                qs = qs.filter(is_active=True)
            elif status == "inactive":
                qs = qs.filter(is_active=False)
        return qs

    def allowed(self, spec):
        module, _, action = spec.partition(".")
        return has_perm(self.request.user, module, action or "view")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user_can_edit = has_perm(self.request.user, self.module, "edit")
        actions = [a for a in self.row_actions if self.allowed(a[3])]
        headers = [tr(c.header) for c in self.columns]
        rows = []
        for obj in ctx["object_list"]:
            links = []
            if user_can_edit and self.edit_url:
                links.append({"label": tr("edit"), "url": reverse(self.edit_url, args=[obj.pk]), "icon": "edit"})
            for label, url_name, icon, _ in actions:
                links.append({"label": tr(label), "url": reverse(url_name, args=[obj.pk]), "icon": icon})
            cells = [dict(c.cell(obj), header=h) for c, h in zip(self.columns, headers)]
            rows.append({"cells": cells, "links": links,
                         "inactive": self.has_status and not obj.is_active})
        params = self.request.GET
        ctx.update(
            title=tr(self.title_key),
            subtitle=tr(self.subtitle_key) if self.subtitle_key else "",
            headers=headers,
            status_options=[("active", tr("active")), ("inactive", tr("inactive")), ("all", tr("all"))],
            rows=rows,
            has_actions=any(r["links"] for r in rows),
            create_url=reverse(self.create_url) if user_can_edit and self.create_url else None,
            header_links=[
                {"label": tr(label), "url": reverse(url_name), "icon": icon}
                for label, url_name, icon, spec in self.header_links if self.allowed(spec)
            ],
            q=params.get("q", ""),
            status=params.get("status", "active"),
            has_status=self.has_status,
            has_search=bool(self.search_fields),
            filter_controls=[
                {"param": p, "value": params.get(p, ""), "label": tr(p),
                 "choices": [(str(v), str(lbl)) for v, lbl in choices]}
                for p, _, choices in self.filters
            ],
            total=ctx["paginator"].count if ctx.get("paginator") else len(rows),
        )
        return ctx


class CrudFormMixin(PermissionMixin):
    """Shared behaviour for create/update pages: permission, audit, message."""

    action = "edit"
    template_name = "crud/form.html"
    entity_key = ""
    list_url = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        verb = "edit" if getattr(self, "object", None) and self.object.pk else "add"
        ctx.update(title=f"{tr(verb)} {tr(self.entity_key)}", back_url=reverse(self.list_url))
        return ctx

    def form_valid(self, form):
        is_new = form.instance.pk is None
        response = super().form_valid(form)
        log(self.request, "create" if is_new else "update", self.object, form.changed_data)
        messages.success(self.request, tr("saved"))
        return response

    def form_invalid(self, form):
        messages.error(self.request, tr("fix_errors"))
        return super().form_invalid(form)

    def get_success_url(self):
        return reverse(self.list_url)
