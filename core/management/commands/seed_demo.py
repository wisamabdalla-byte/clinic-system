"""Fill an empty database with demo data for trying the system.

    python manage.py seed_demo

Safe to run again (updates instead of duplicating). Refuses to run when
DJANGO_DEBUG is off unless --force is given, so it never touches a live site
by accident. Demo accounts get the password from SEED_PASSWORD (see README).
"""
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from accounts.roles import Role
from core.models import (
    Branch, Package, PackageItem, PaymentMethod, PriceList, PriceListItem, Service, ServiceCategory,
)

BRANCHES = [
    ("TRP", "فرع طرابلس", "Tripoli branch", "021 000 0001"),
    ("BEN", "فرع بنغازي", "Benghazi branch", "061 000 0002"),
]

CATEGORIES = {
    "dx": ("dental", "الكشف والأشعة", "Exams & X-rays"),
    "prev": ("dental", "الوقاية والتنظيف", "Preventive & cleaning"),
    "rest": ("dental", "الحشوات وعلاج العصب", "Fillings & root canals"),
    "surg": ("dental", "الجراحة والخلع", "Surgery & extractions"),
    "prost": ("dental", "التركيبات والزراعة", "Crowns & implants"),
    "ortho": ("dental", "التقويم والتجميل", "Orthodontics & cosmetic"),
    "dcons": ("derma", "الكشف", "Consultations"),
    "laser": ("derma", "الليزر", "Laser"),
    "skin": ("derma", "العناية بالبشرة", "Skin care"),
    "inj": ("derma", "الحقن التجميلي", "Injectables"),
    "hair": ("derma", "علاج الشعر", "Hair treatment"),
}

# code, category, Arabic, English, price (LYD, demo values), minutes, per-tooth
SERVICES = [
    ("DEN-001", "dx", "كشف واستشارة أسنان", "Dental consultation", 30, 20, False),
    ("DEN-002", "dx", "أشعة بانوراما", "Panoramic X-ray", 40, 15, False),
    ("DEN-003", "prev", "تنظيف جير وتلميع", "Scaling & polishing", 80, 45, False),
    ("DEN-004", "rest", "حشو تجميلي (كومبوزيت)", "Composite filling", 100, 45, True),
    ("DEN-005", "rest", "علاج عصب ضرس", "Root canal (molar)", 350, 90, True),
    ("DEN-006", "surg", "خلع بسيط", "Simple extraction", 60, 30, True),
    ("DEN-007", "surg", "خلع ضرس عقل جراحي", "Surgical wisdom tooth extraction", 300, 60, True),
    ("DEN-008", "prost", "تاج زيركون", "Zirconia crown", 600, 60, True),
    ("DEN-009", "prost", "زراعة سن", "Dental implant", 2500, 90, True),
    ("DEN-010", "ortho", "تبييض الأسنان بالليزر", "Laser teeth whitening", 500, 60, False),
    ("DEN-011", "ortho", "تقويم معدني كامل", "Metal braces (full)", 3500, 60, False),
    ("DER-001", "dcons", "كشف جلدية", "Dermatology consultation", 40, 20, False),
    ("DER-002", "laser", "ليزر إزالة شعر - الوجه", "Laser hair removal - face", 100, 20, False),
    ("DER-003", "laser", "ليزر إزالة شعر - الجسم كامل", "Laser hair removal - full body", 450, 90, False),
    ("DER-004", "skin", "هيدرافيشال", "HydraFacial", 200, 60, False),
    ("DER-005", "skin", "تقشير كيميائي", "Chemical peel", 150, 45, False),
    ("DER-006", "skin", "ديرمابن (وخز بالإبر الدقيقة)", "Microneedling (Dermapen)", 180, 45, False),
    ("DER-007", "inj", "بوتوكس - منطقة واحدة", "Botox - one area", 450, 30, False),
    ("DER-008", "inj", "فيلر - سرنجة واحدة", "Filler - one syringe", 750, 45, False),
    ("DER-009", "inj", "ميزوثيرابي للوجه", "Facial mesotherapy", 250, 45, False),
    ("DER-010", "hair", "بلازما للشعر (PRP)", "Hair PRP", 200, 45, False),
]

# name ar, name en, kind, needs reference, account details, order
PAYMENT_METHODS = [
    ("نقداً", "Cash", "cash", False, "", 1),
    ("بطاقة مصرفية", "Bank card", "card", True, "جهاز نقاط البيع - الاستقبال", 2),
    ("حوالة مصرفية", "Bank transfer", "transfer", True, "", 3),
]

# username, first (ar), last (ar), role, specialty, department, color, branches
USERS = [
    ("admin", "مدير", "النظام", Role.ADMIN, "", "", "#0f766e", []),
    ("manager", "هالة", "منصور", Role.MANAGER, "", "", "#0f766e", ["TRP", "BEN"]),
    ("dr.sara", "سارة", "عادل", Role.DOCTOR, "تقويم وتجميل أسنان", "dental", "#2563eb", ["TRP"]),
    ("dr.omar", "عمر", "حسن", Role.DOCTOR, "جراحة وزراعة الأسنان", "dental", "#7c3aed", ["TRP", "BEN"]),
    ("dr.lina", "لينا", "فؤاد", Role.DOCTOR, "جلدية وتجميل وليزر", "derma", "#db2777", ["TRP", "BEN"]),
    ("nurse", "منى", "سعيد", Role.NURSE, "", "", "#0f766e", ["TRP"]),
    ("reception", "ياسمين", "علي", Role.RECEPTIONIST, "", "", "#0f766e", ["TRP"]),
    ("accountant", "كريم", "محمود", Role.ACCOUNTANT, "", "", "#0f766e", ["TRP", "BEN"]),
    ("store", "طارق", "إبراهيم", Role.STOREKEEPER, "", "", "#0f766e", ["TRP"]),
]


# name, gender, birth year, phone, allergies, chronic, insured
PATIENTS = [
    ("أحمد محمود السيد", "male", 1985, "0911234501", "البنسلين", "", False),
    ("فاطمة علي حسن", "female", 1992, "0911234502", "", "", True),
    ("محمد عبد الله إبراهيم", "male", 1978, "0911234503", "", "السكري", False),
    ("نور الهدى سامي", "female", 1999, "0911234504", "", "", False),
    ("يوسف خالد منصور", "male", 2010, "0911234505", "", "", False),
    ("مريم أحمد فؤاد", "female", 1988, "0911234506", "اللاتكس", "", True),
    ("عمرو حسين طه", "male", 1965, "0911234507", "", "ضغط الدم المرتفع", False),
    ("سلمى يوسف رضا", "female", 2001, "0911234508", "", "", False),
    ("كريم سعيد عادل", "male", 1995, "0911234509", "", "", False),
    ("هدى مصطفى كمال", "female", 1983, "0911234510", "", "", True),
    ("علي جمال الدين", "male", 1990, "0911234511", "", "", False),
    ("رنا وليد شريف", "female", 1997, "0911234512", "", "", False),
]

# doctor, patient index, hour, minute, service code, status (today's plan)
TODAY = [
    ("dr.sara", 0, 9, 30, "DEN-001", "completed"),
    ("dr.sara", 1, 10, 0, "DEN-003", "completed"),
    ("dr.sara", 2, 11, 0, "DEN-004", "in_progress"),
    ("dr.sara", 3, 12, 0, "DEN-010", "arrived"),
    ("dr.sara", 4, 13, 30, "DEN-001", "confirmed"),
    ("dr.sara", 5, 15, 0, "DEN-005", "booked"),
    ("dr.omar", 6, 10, 0, "DEN-009", "completed"),
    ("dr.omar", 7, 12, 0, "DEN-007", "arrived"),
    ("dr.omar", 8, 14, 0, "DEN-008", "booked"),
    ("dr.omar", 9, 17, 0, "DEN-006", "cancelled"),
    ("dr.lina", 10, 11, 0, "DER-001", "in_progress"),
    ("dr.lina", 11, 11, 30, "DER-004", "arrived"),
    ("dr.lina", 1, 13, 0, "DER-002", "booked"),
    ("dr.lina", 3, 16, 0, "DER-007", "booked"),
    ("dr.lina", 7, 18, 0, "DER-005", "no_show"),
]


class Command(BaseCommand):
    help = "Load demo branches, services, price lists, packages and one account per role."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Run even when DEBUG is off.")

    def seed_patients_and_appointments(self, branches, services, insurance):
        from django.utils import timezone

        from clinic.models import Appointment, Patient

        main = branches["TRP"]
        patients = []
        for name, gender, year, phone, allergies, chronic, insured in PATIENTS:
            p = Patient.objects.filter(phone=phone).first() or Patient(phone=phone)
            p.full_name, p.gender, p.date_of_birth = name, gender, date(year, 3, 15)
            p.allergies, p.chronic_conditions, p.branch = allergies, chronic, main
            p.price_list = insurance if insured else None
            p.insurance_no = f"INS-{phone[-4:]}" if insured else ""
            p.source = "facebook" if year % 2 else "referral"
            p.save()
            patients.append(p)

        today = timezone.localdate()
        if Appointment.objects.filter(start__date=today).exists():
            return  # keep whatever was booked already
        doctors = {u.username: u for u in User.objects.filter(role=Role.DOCTOR)}
        now = timezone.now()
        queue = 0
        for username, idx, hour, minute, code, status in sorted(TODAY, key=lambda t: (t[2], t[3])):
            service = services[code]
            start = timezone.make_aware(datetime.combine(today, time(hour, minute)))
            appt = Appointment(branch=main, patient=patients[idx], doctor=doctors[username],
                               service=service, start=start, duration_minutes=service.duration_minutes,
                               status=status, source="phone")
            if status in ("arrived", "in_progress", "completed"):
                queue += 1
                appt.queue_number = queue
                appt.checked_in_at = min(start, now)
            if status in ("in_progress", "completed"):
                appt.started_at = min(start, now)
            if status == "completed":
                appt.completed_at = min(start + timedelta(minutes=service.duration_minutes), now)
            if status == "cancelled":
                appt.cancel_reason = "طلب المريض التأجيل"
            appt.save()
        # a few bookings tomorrow so the next day isn't empty
        tomorrow = today + timedelta(days=1)
        for username, idx, hour, code in [("dr.sara", 6, 10, "DEN-003"), ("dr.lina", 5, 12, "DER-009"),
                                          ("dr.omar", 2, 14, "DEN-002")]:
            Appointment.objects.create(
                branch=main, patient=patients[idx], doctor=doctors[username], service=services[code],
                start=timezone.make_aware(datetime.combine(tomorrow, time(hour, 0))),
                duration_minutes=services[code].duration_minutes, status="booked", source="whatsapp",
            )

    @transaction.atomic
    def handle(self, *args, force=False, **options):
        if not settings.DEBUG and not force:
            raise CommandError("DEBUG is off. This looks like a live site; use --force if you are sure.")
        password = os.environ.get("SEED_PASSWORD", "Clinic@2026")

        branches = {}
        for code, ar, en, phone in BRANCHES:
            branches[code], _ = Branch.objects.update_or_create(
                code=code, defaults={"name_ar": ar, "name_en": en, "phone": phone, "is_active": True}
            )

        cats = {}
        for key, (dept, ar, en) in CATEGORIES.items():
            cats[key], _ = ServiceCategory.objects.update_or_create(
                name_en=en, department=dept, defaults={"name_ar": ar, "is_active": True}
            )

        services = {}
        for code, cat, ar, en, price, minutes, tooth in SERVICES:
            services[code], _ = Service.objects.update_or_create(
                code=code,
                defaults={
                    "name_ar": ar, "name_en": en, "category": cats[cat],
                    "department": cats[cat].department, "price": Decimal(price),
                    "duration_minutes": minutes, "requires_tooth": tooth, "is_active": True,
                },
            )

        standard, _ = PriceList.objects.update_or_create(
            name_en="Standard prices",
            defaults={"name_ar": "الأسعار الأساسية", "kind": "standard", "is_default": True,
                      "discount_percent": 0},
        )
        insurance, _ = PriceList.objects.update_or_create(
            name_en="Insurance - Sample Co.",
            defaults={"name_ar": "تأمين - شركة نموذجية", "kind": "insurance",
                      "discount_percent": Decimal("15")},
        )
        for code, price in [("DEN-001", 20), ("DER-001", 30), ("DEN-002", 30)]:
            PriceListItem.objects.update_or_create(
                price_list=insurance, service=services[code], defaults={"price": Decimal(price)}
            )
        today = date.today()
        PriceList.objects.update_or_create(
            name_en="Offer - Benghazi branch",
            defaults={"name_ar": "عرض فرع بنغازي", "kind": "offer",
                      "discount_percent": Decimal("20"), "branch": branches["BEN"],
                      "valid_from": today, "valid_to": today + timedelta(days=60)},
        )

        packages = [
            ("Laser face - 6 sessions", "باقة ليزر الوجه ٦ جلسات", "derma", 500, 180, [("DER-002", 6)]),
            ("Glow package", "باقة نضارة البشرة", "derma", 750, 120, [("DER-004", 3), ("DER-005", 2)]),
            ("Bright smile", "باقة الابتسامة المشرقة", "dental", 499, 90, [("DEN-003", 1), ("DEN-010", 1)]),
        ]
        for ar, en, kind, needs_ref, details, order in PAYMENT_METHODS:
            PaymentMethod.objects.update_or_create(
                kind=kind, name_en=en,
                defaults={"name_ar": ar, "requires_reference": needs_ref, "account_details": details,
                          "sort_order": order, "is_active": True},
            )
        for en, ar, dept, price, days, items in packages:
            pkg, _ = Package.objects.update_or_create(
                name_en=en,
                defaults={"name_ar": ar, "department": dept, "price": Decimal(price),
                          "validity_days": days, "is_active": True},
            )
            for code, qty in items:
                PackageItem.objects.update_or_create(
                    package=pkg, service=services[code], defaults={"quantity": qty}
                )

        for username, first, last, role, specialty, dept, color, branch_codes in USERS:
            user, created = User.objects.get_or_create(username=username)
            user.first_name, user.last_name, user.role = first, last, role
            user.specialty, user.department, user.color = specialty, dept, color
            user.is_active = True
            if role == Role.ADMIN:
                user.is_staff = user.is_superuser = True
            if created:
                user.set_password(password)
            user.save()
            user.branches.set([branches[c] for c in branch_codes])

        self.seed_patients_and_appointments(branches, services, insurance)

        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready: {len(BRANCHES)} branches, {len(SERVICES)} services, "
            f"3 price lists, {len(packages)} packages, {len(USERS)} users."
        ))
