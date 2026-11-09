from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView, FormView, UpdateView

from core.audit import log
from core.i18n import tr
from core.mixins import Col, CrudFormMixin, CrudListView, PermissionMixin
from core.models import Branch

from .forms import (
    LoginForm, StyledPasswordChangeForm, StyledSetPasswordForm, UserCreateForm, UserUpdateForm,
)
from .models import User
from .roles import Role


class LoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


def last_login_text(user):
    return user.last_login if user.last_login else tr("never")


class UserList(CrudListView):
    module = "users"
    title_key, subtitle_key = "nav_users", "users_sub"
    search_fields = ["first_name", "last_name", "username", "phone", "email", "specialty"]
    create_url, edit_url = "user_create", "user_edit"
    row_actions = [("set_password", "user_password", "key", "users.edit")]
    columns = [
        Col("full_name", lambda o: str(o), "title", sub=lambda o: o.specialty or o.username),
        Col("role", lambda o: o.get_role_display(), "badge"),
        Col("branches", lambda o: tr("all_branches") if o.is_admin
            else "، ".join(b.name for b in o.branches.all()) or "—"),
        Col("phone", lambda o: o.phone, "ltr"),
        Col("last_login", last_login_text, "datetime"),
    ]

    def get_queryset(self):
        self.queryset = User.objects.prefetch_related("branches")
        self.filters = [
            ("role", "role", Role.choices),
            ("branch", "branches__id",
             [(b.pk, b.name) for b in Branch.objects.filter(is_active=True)]),
        ]
        return super().get_queryset().distinct()


class UserFormMixin(CrudFormMixin):
    module, model = "users", User
    entity_key, list_url = "ent_user", "user_list"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["editor"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = ctx["form"]
        account = ["first_name", "last_name", "username", "email", "phone", "role",
                   "password1", "password2", "is_active"]
        ctx["sections"] = [
            {"title": tr("account_section"), "fields": [form[f] for f in account if f in form.fields]},
            {"title": tr("branches_section"), "fields": [form["branches"]]},
            {"title": tr("doctor_section"), "help": tr("doctor_section_help"),
             "fields": [form[f] for f in ("specialty", "department", "color")]},
        ]
        return ctx


class UserCreate(UserFormMixin, CreateView):
    form_class = UserCreateForm


class UserUpdate(UserFormMixin, UpdateView):
    form_class = UserUpdateForm


class UserSetPassword(PermissionMixin, FormView):
    """An admin sets a new password for a staff member (e.g. they forgot it)."""

    module, action = "users", "edit"
    template_name = "crud/form.html"
    form_class = StyledSetPasswordForm

    def dispatch(self, request, *args, **kwargs):
        self.target = get_object_or_404(User, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.target
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(title=tr("set_password_for", self.target), back_url=reverse("user_list"))
        return ctx

    def form_valid(self, form):
        form.save()
        if self.target.pk == self.request.user.pk:
            update_session_auth_hash(self.request, self.target)
        log(self.request, "password", self.target)
        messages.success(self.request, tr("password_set"))
        return redirect("user_list")


class ChangeOwnPassword(PermissionMixin, FormView):
    module = "dashboard"
    template_name = "crud/form.html"
    form_class = StyledPasswordChangeForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(title=tr("change_password"), back_url=reverse("dashboard"))
        return ctx

    def form_valid(self, form):
        user = form.save()
        update_session_auth_hash(self.request, user)
        log(self.request, "password", user)
        messages.success(self.request, tr("password_changed"))
        return redirect("dashboard")
