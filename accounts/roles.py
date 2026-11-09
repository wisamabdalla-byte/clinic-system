"""Roles and what each role may do.

A module is one area of the system (services, users, ...). Each module has a
"view" and an "edit" list of roles. Superusers can do everything.
Later stages add modules here (appointments, patients, finance, ...).
"""
from django.db import models

from core.i18n import tr_lazy


class Role(models.TextChoices):
    ADMIN = "admin", tr_lazy("role_admin")
    MANAGER = "manager", tr_lazy("role_manager")
    DOCTOR = "doctor", tr_lazy("role_doctor")
    NURSE = "nurse", tr_lazy("role_nurse")
    RECEPTIONIST = "receptionist", tr_lazy("role_receptionist")
    ACCOUNTANT = "accountant", tr_lazy("role_accountant")
    STOREKEEPER = "storekeeper", tr_lazy("role_storekeeper")


R = Role
EVERYONE = set(Role.values)

PERMISSIONS = {
    "dashboard": {"view": EVERYONE},
    "users": {"view": {R.ADMIN, R.MANAGER}, "edit": {R.ADMIN}},
    "branches": {"view": {R.ADMIN, R.MANAGER}, "edit": {R.ADMIN}},
    "services": {"view": EVERYONE, "edit": {R.ADMIN, R.MANAGER}},
    "pricelists": {
        "view": {R.ADMIN, R.MANAGER, R.RECEPTIONIST, R.ACCOUNTANT},
        "edit": {R.ADMIN, R.MANAGER, R.ACCOUNTANT},
    },
    "packages": {
        "view": {R.ADMIN, R.MANAGER, R.RECEPTIONIST, R.ACCOUNTANT, R.DOCTOR},
        "edit": {R.ADMIN, R.MANAGER},
    },
    "audit": {"view": {R.ADMIN}},
    "paymentmethods": {
        "view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT, R.RECEPTIONIST},
        "edit": {R.ADMIN, R.ACCOUNTANT},
    },
    # Stage 2
    "patients": {
        "view": {R.ADMIN, R.MANAGER, R.DOCTOR, R.NURSE, R.RECEPTIONIST, R.ACCOUNTANT},
        "edit": {R.ADMIN, R.MANAGER, R.DOCTOR, R.NURSE, R.RECEPTIONIST},
    },
    "appointments": {
        "view": {R.ADMIN, R.MANAGER, R.DOCTOR, R.NURSE, R.RECEPTIONIST},
        "edit": {R.ADMIN, R.MANAGER, R.RECEPTIONIST},  # book / reschedule
        "status": {R.ADMIN, R.MANAGER, R.RECEPTIONIST, R.NURSE, R.DOCTOR},  # check-in, start, ...
    },
    # Stage 3: clinical records
    "clinical": {
        "view": {R.ADMIN, R.MANAGER, R.DOCTOR, R.NURSE},
        "edit": {R.ADMIN, R.DOCTOR, R.NURSE},  # notes, tooth chart, photos, treatment plans
        "prescribe": {R.ADMIN, R.DOCTOR},
    },
    # Stage 4: finance
    "invoices": {
        "view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT, R.RECEPTIONIST},
        "edit": {R.ADMIN, R.MANAGER, R.ACCOUNTANT, R.RECEPTIONIST},  # write invoices, take payments
        "void": {R.ADMIN, R.MANAGER, R.ACCOUNTANT},  # remove a wrong payment
    },
    "doctor_accounts": {
        "view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT, R.DOCTOR},  # a doctor sees only their own
        "edit": {R.ADMIN, R.MANAGER, R.ACCOUNTANT},  # close a month
        "reopen": {R.ADMIN},
    },
    "payouts": {"view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT}, "edit": {R.ADMIN, R.MANAGER, R.ACCOUNTANT}},
    "expenses": {"view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT}, "edit": {R.ADMIN, R.MANAGER, R.ACCOUNTANT}},
    "cashbox": {"view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT, R.RECEPTIONIST}},
    "finance_reports": {"view": {R.ADMIN, R.MANAGER, R.ACCOUNTANT}},
    # Clinic name, logo and colours
    "settings": {"view": {R.ADMIN}, "edit": {R.ADMIN}},
}

# Status actions a role may perform (others are refused even with "status" permission).
STATUS_ACTIONS = {
    R.DOCTOR: {"start", "complete"},  # own appointments only
    R.NURSE: {"confirm", "checkin", "start", "complete", "cancel", "noshow"},
    R.RECEPTIONIST: {"confirm", "checkin", "start", "cancel", "noshow", "restore"},
}


def has_perm(user, module, action="view"):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return user.role in PERMISSIONS.get(module, {}).get(action, set())
