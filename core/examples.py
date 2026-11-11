"""Example data for trying every part of the system on the live site, and erasing it again.

Everything created here is listed in ExampleItem, so erasing removes exactly
these records (newest first, so invoices go before patients, and so on).
Real data entered by staff is never touched. Example names end with "(مثال)".
"""
import struct
import zlib
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

from accounts.models import User
from accounts.roles import Role

from .models import (
    Branch, ExampleItem, Package, PackageItem, PaymentMethod, PriceList, PriceListItem, Service, ServiceCategory,
)

TAG = " (مثال)"


def has_examples():
    return ExampleItem.objects.exists()


def example_counts():
    counts = {}
    for label in ExampleItem.objects.values_list("model_label", flat=True):
        counts[label] = counts.get(label, 0) + 1
    return counts


def _png(width, height, rgb):
    """A plain-colour PNG, used as the example before/after photos."""
    row = b"\x00" + bytes(rgb) * width
    raw = zlib.compress(row * height, 9)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


class _Recorder:
    def __call__(self, obj):
        ExampleItem.objects.create(model_label=obj._meta.label_lower, object_id=str(obj.pk))
        return obj


@transaction.atomic
def load_examples(branch, user):
    from clinic.models import Appointment, Patient
    from finance.models import DoctorPayout, Expense, Invoice, InvoiceItem, Payment, month_start
    from finance.services import close_month
    from records.models import (
        ClinicalNote, PatientPhoto, PlanItem, Prescription, PrescriptionItem, ToothRecord, TreatmentPlan,
    )

    if has_examples():
        raise ValueError("already loaded")
    rec = _Recorder()
    today = timezone.localdate()
    last_month = month_start(month_start(today) - timedelta(days=1))
    if branch is None:
        branch = rec(Branch.objects.create(code="EX", name_ar="الفرع الرئيسي" + TAG, name_en="Main branch (example)"))

    # ---- payment methods: reuse the clinic's own if they exist
    methods = {}
    for kind, ar, en, needs_ref in [("cash", "نقداً", "Cash", False), ("card", "بطاقة مصرفية", "Bank card", True),
                                    ("transfer", "حوالة مصرفية", "Bank transfer", True)]:
        methods[kind] = PaymentMethod.objects.filter(kind=kind, is_active=True).first() or rec(
            PaymentMethod.objects.create(kind=kind, name_ar=ar, name_en=en, requires_reference=needs_ref))

    # ---- services, price list, package
    dental = rec(ServiceCategory.objects.create(department="dental", name_ar="علاج الأسنان" + TAG, name_en="Dental treatment (example)"))
    derma = rec(ServiceCategory.objects.create(department="derma", name_ar="الجلدية والتجميل" + TAG, name_en="Skin & aesthetics (example)"))
    svc = {}
    for code, cat, ar, en, price, minutes, tooth in [
        ("EX-D1", dental, "كشف أسنان", "Dental check-up", 30, 20, False),
        ("EX-D2", dental, "حشو تجميلي", "Composite filling", 100, 45, True),
        ("EX-D3", dental, "علاج عصب", "Root canal", 350, 90, True),
        ("EX-D4", dental, "زراعة سن", "Dental implant", 2500, 90, True),
        ("EX-S1", derma, "كشف جلدية", "Skin consultation", 40, 20, False),
        ("EX-S2", derma, "ليزر الوجه", "Face laser", 100, 30, False),
        ("EX-S3", derma, "فيلر", "Filler", 750, 45, False),
    ]:
        svc[code] = rec(Service.objects.create(
            code=code, category=cat, department=cat.department, name_ar=ar + TAG, name_en=f"{en} (example)",
            price=Decimal(price), duration_minutes=minutes, requires_tooth=tooth))
    insurance = rec(PriceList.objects.create(kind="insurance", name_ar="تأمين شركة نموذجية" + TAG,
                                             name_en="Sample insurance (example)", discount_percent=Decimal(15)))
    rec(PriceListItem.objects.create(price_list=insurance, service=svc["EX-D1"], price=Decimal(20)))
    package = rec(Package.objects.create(department="derma", name_ar="باقة ليزر ٦ جلسات" + TAG,
                                         name_en="Laser 6 sessions (example)", price=Decimal(500), validity_days=180))
    PackageItem.objects.create(package=package, service=svc["EX-S2"], quantity=6)

    # ---- staff (they can't sign in: no password)
    doctors = {}
    for key, first, specialty, dept, color, pct in [
        ("dent", "د. سارة", "تجميل وحشوات", "dental", "#2563eb", 40),
        ("surg", "د. عمر", "جراحة وزراعة", "dental", "#7c3aed", 35),
        ("derm", "د. لينا", "جلدية وتجميل", "derma", "#db2777", 45),
    ]:
        username = f"example.{key}"
        u = User(username=username, first_name=first, last_name="(مثال)", role=Role.DOCTOR, specialty=specialty,
                 department=dept, color=color, commission_percent=Decimal(pct))
        u.set_unusable_password()
        u.save()
        u.branches.set([branch])
        doctors[key] = rec(u)

    # ---- patients
    patients = []
    for i, (name, gender, year, allergies) in enumerate([
        ("أحمد محمود", "male", 1985, ""), ("فاطمة علي", "female", 1992, "البنسلين"),
        ("خالد سالم", "male", 1978, ""), ("مريم يوسف", "female", 2001, ""),
        ("سالم الورفلي", "male", 1969, ""), ("هند الترهوني", "female", 1995, ""),
    ]):
        patients.append(rec(Patient.objects.create(
            full_name=name + TAG, gender=gender, date_of_birth=datetime(year, 5, 10).date(),
            phone=f"09100000{i + 1:02d}", allergies=allergies, branch=branch, source="facebook",
            price_list=insurance if i == 3 else None, insurance_no="INS-0001" if i == 3 else "",
            created_by=user)))

    # ---- appointments today (one of each status) and tomorrow
    now = timezone.now()
    queue = 0
    for doc, idx, hour, code, status in [
        ("dent", 0, 9, "EX-D1", "completed"), ("dent", 1, 10, "EX-D2", "in_progress"),
        ("dent", 2, 11, "EX-D1", "arrived"), ("surg", 4, 10, "EX-D4", "completed"),
        ("surg", 2, 13, "EX-D3", "confirmed"), ("derm", 3, 11, "EX-S1", "arrived"),
        ("derm", 5, 12, "EX-S2", "booked"), ("derm", 0, 16, "EX-S3", "cancelled"),
    ]:
        start = timezone.make_aware(datetime.combine(today, time(hour, 0)))
        a = Appointment(branch=branch, patient=patients[idx], doctor=doctors[doc], service=svc[code], start=start,
                        duration_minutes=svc[code].duration_minutes, status=status, source="phone", created_by=user)
        if status in ("arrived", "in_progress", "completed"):
            queue += 1
            a.queue_number, a.checked_in_at = queue, min(start, now)
        if status in ("in_progress", "completed"):
            a.started_at = min(start, now)
        if status == "completed":
            a.completed_at = min(start + timedelta(minutes=a.duration_minutes), now)
        if status == "cancelled":
            a.cancel_reason = "طلب المريض التأجيل"
        a.save()
        rec(a)
    tomorrow = today + timedelta(days=1)
    rec(Appointment.objects.create(branch=branch, patient=patients[1], doctor=doctors["dent"], service=svc["EX-D2"],
                                   start=timezone.make_aware(datetime.combine(tomorrow, time(10, 0))),
                                   duration_minutes=45, source="whatsapp", created_by=user))

    # ---- invoices and payments
    def invoice(day, idx, doc, lines, pays):
        inv = rec(Invoice.objects.create(branch=branch, patient=patients[idx], doctor=doctors[doc], date=day,
                                         doctor_percent=doctors[doc].commission_percent, created_by=user))
        for code, qty, material, tooth in lines:
            InvoiceItem.objects.create(invoice=inv, service=svc[code], quantity=qty, tooth=tooth,
                                       unit_price=svc[code].price, material_cost=Decimal(material))
        inv.recalc()
        for pday, amount, kind, ref in pays:
            rec(Payment.objects.create(invoice=inv, branch=branch, date=pday, amount=Decimal(amount),
                                       method=methods[kind], reference=ref, received_by=user))
        inv.recalc()
        return inv

    # last month: implant paid in two parts, closed and partly paid out to the surgeon
    invoice(last_month + timedelta(days=3), 4, "surg", [("EX-D4", 1, 900, "36")],
            [(last_month + timedelta(days=3), 1500, "cash", ""), (last_month + timedelta(days=18), 1000, "transfer", "TRX-1001")])
    invoice(last_month + timedelta(days=8), 5, "derm", [("EX-S3", 1, 250, "")],
            [(last_month + timedelta(days=8), 750, "card", "POS-2001")])
    settlement = rec(close_month(doctors["surg"], last_month, user, Decimal("-50"), "سلفة (مثال)"))
    rec(DoctorPayout.objects.create(doctor=doctors["surg"], branch=branch, settlement=settlement,
                                    date=month_start(today), amount=Decimal(400), method=methods["cash"],
                                    notes="دفعة أولى (مثال)", paid_by=user))
    # this month: paid in full / part paid / not paid yet
    invoice(today, 0, "dent", [("EX-D1", 1, 0, "")], [(today, 30, "cash", "")])
    invoice(today, 1, "dent", [("EX-D1", 1, 0, ""), ("EX-D2", 2, 40, "16")], [(today, 100, "card", "POS-2002")])
    invoice(today, 4, "surg", [("EX-D4", 1, 900, "46")], [])

    # ---- expenses
    for cat, amount, kind, text in [("rent", 3000, "transfer", "إيجار المقر"), ("lab", 900, "cash", "معمل الأسنان – زرعة"),
                                    ("materials", 350, "cash", "مواد حشو ومستهلكات")]:
        rec(Expense.objects.create(branch=branch, date=today, category=cat, amount=Decimal(amount),
                                   method=methods[kind], description=text + TAG, created_by=user))

    # ---- clinical records
    dent, derm = doctors["dent"], doctors["derm"]
    rec(ClinicalNote.objects.create(
        patient=patients[1], doctor=dent, date=today, department="dental",
        complaint="ألم مع البارد في الضرس العلوي الأيمن", examination="تسوس متوسط في 16 و 17",
        diagnosis="تسوس عاجي", treatment="حشو كومبوزيت للسن 16", plan_next="حشو 17 في الزيارة القادمة",
        follow_up=today + timedelta(days=7), created_by=user))
    rec(ClinicalNote.objects.create(
        patient=patients[3], doctor=derm, date=today, department="derma", complaint="حبوب وآثار على الخدين",
        diagnosis="حب شباب متوسط", treatment="تنظيف بشرة وتقشير خفيف", plan_next="جلسة متابعة بعد أسبوعين",
        created_by=user))
    for tooth, cond, note in [(16, "filled", "كومبوزيت"), (17, "caries", "يحتاج حشو"), (36, "root_canal", ""),
                              (46, "crown", "زيركون"), (48, "missing", "")]:
        rec(ToothRecord.objects.create(patient=patients[1], tooth=tooth, condition=cond, note=note, updated_by=user))
    rx = rec(Prescription.objects.create(patient=patients[1], doctor=dent, date=today, notes="بعد الأكل"))
    PrescriptionItem.objects.create(prescription=rx, drug="Amoxicillin 500mg", dose="كبسولة", frequency="3 مرات يومياً", duration="5 أيام")
    PrescriptionItem.objects.create(prescription=rx, drug="Ibuprofen 400mg", dose="قرص", frequency="عند اللزوم", duration="3 أيام")
    plan = rec(TreatmentPlan.objects.create(patient=patients[1], doctor=dent, title="خطة علاج الأضراس" + TAG,
                                            status="in_progress"))
    PlanItem.objects.create(plan=plan, service=svc["EX-D2"], tooth="16", price=Decimal(100), is_done=True, done_on=today)
    PlanItem.objects.create(plan=plan, service=svc["EX-D2"], tooth="17", price=Decimal(100))
    PlanItem.objects.create(plan=plan, service=svc["EX-D3"], tooth="36", price=Decimal(350))
    for stage, rgb, day in [("before", (214, 160, 140), today - timedelta(days=30)), ("after", (236, 200, 180), today)]:
        data = _png(320, 240, rgb)
        rec(PatientPhoto.objects.create(patient=patients[3], taken_on=day, area="الوجه – الخدين", stage=stage,
                                        note="صورة مثال", content_type="image/png", data=data, size=len(data),
                                        uploaded_by=user))
    return ExampleItem.objects.count()


def erase_examples():
    """Delete every example record, newest first. Returns (deleted, kept) counts.

    A record is kept only if staff linked real data to it (for example a real
    invoice for an example patient)."""
    from ledger.engine import purging

    with purging():  # their journal entries go with them instead of being reversed
        return _erase()


def _erase():
    deleted = kept = 0
    for item in ExampleItem.objects.order_by("-id"):
        model = apps.get_model(item.model_label)
        try:
            with transaction.atomic():
                model.objects.filter(pk=item.object_id).delete()
            item.delete()
            deleted += 1
        except ProtectedError:
            kept += 1
    return deleted, kept
