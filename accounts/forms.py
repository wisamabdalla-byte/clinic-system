import hashlib

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm
from django.core.cache import cache
from django.core.exceptions import ValidationError

from core.audit import client_ip
from core.forms import StyledFormMixin, StyledModelForm
from core.i18n import tr
from core.models import Branch

from .models import User
from .roles import Role

MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60


class LoginForm(StyledFormMixin, AuthenticationForm):
    """Sign-in with a lock after repeated failures (per username + IP)."""

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        self.style_fields()
        self.fields["password"].label = tr("password1")
        self.error_messages["invalid_login"] = tr("login_invalid")

    def _lock_key(self):
        username = (self.data.get("username") or "").strip().lower()
        raw = f"{username}|{client_ip(self.request)}"
        return "login-fail:" + hashlib.sha256(raw.encode()).hexdigest()

    def clean(self):
        key = self._lock_key()
        if cache.get(key, 0) >= MAX_FAILURES:
            raise ValidationError(tr("login_locked"), code="locked")
        try:
            data = super().clean()
        except ValidationError:
            cache.set(key, cache.get(key, 0) + 1, LOCK_SECONDS)
            raise
        cache.delete(key)
        return data


class UserFormBase(StyledModelForm):
    class Meta:
        model = User
        fields = [
            "first_name", "last_name", "username", "email", "phone", "role", "branches",
            "specialty", "department", "color", "commission_percent", "is_active",
        ]
        widgets = {
            "branches": forms.CheckboxSelectMultiple,
            "color": forms.TextInput(attrs={"type": "color"}),
        }

    doctor_fields = ("specialty", "department", "color", "commission_percent")

    def __init__(self, *args, editor=None, **kwargs):
        self.editor = editor
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["branches"].queryset = Branch.objects.filter(is_active=True)
        self.fields["username"].help_text = ""
        self.fields["commission_percent"].required = False
        self.fields["commission_percent"].widget.attrs.update(min=0, max=100, step="0.5")

    def clean_commission_percent(self):
        return self.cleaned_data.get("commission_percent") or 0

    def clean(self):
        data = super().clean()
        if self.editor and self.instance.pk == self.editor.pk:
            if data.get("is_active") is False or data.get("role") != Role.ADMIN and not self.editor.is_superuser:
                raise ValidationError(tr("cannot_lock_self"))
        return data


class UserCreateForm(UserFormBase):
    password1 = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].help_text = password_validation.password_validators_help_text_html()

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", tr("password_mismatch"))
        elif p1:
            candidate = User(username=data.get("username", ""), first_name=data.get("first_name", ""),
                             email=data.get("email", ""))
            try:
                password_validation.validate_password(p1, candidate)
            except ValidationError as err:
                self.add_error("password1", err)
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            self.save_m2m()
        return user


class UserUpdateForm(UserFormBase):
    pass


class StyledSetPasswordForm(StyledFormMixin, SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()
