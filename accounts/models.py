from django.contrib.auth.models import AbstractUser
from django.db import models

from core.choices import Department

from .roles import Role, has_perm


class User(AbstractUser):
    """A staff member who signs in to the system."""

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.RECEPTIONIST)
    phone = models.CharField(max_length=30, blank=True)
    branches = models.ManyToManyField("core.Branch", blank=True, related_name="staff")
    # Doctor details (blank for other roles)
    specialty = models.CharField(max_length=120, blank=True)
    department = models.CharField(max_length=20, blank=True, choices=Department.choices)
    color = models.CharField(max_length=7, default="#0f766e")

    class Meta:
        ordering = ["first_name", "last_name", "username"]

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def is_admin(self):
        return self.is_superuser or self.role == Role.ADMIN

    def can(self, module, action="view"):
        return has_perm(self, module, action)

    def allowed_branches(self):
        from core.models import Branch

        qs = Branch.objects.filter(is_active=True)
        return qs if self.is_admin else qs.filter(staff=self)
