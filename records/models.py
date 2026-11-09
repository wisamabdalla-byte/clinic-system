"""Clinical records (stage 3): visit notes, tooth chart, prescriptions,
treatment plans and before/after photos. Photos are stored in the database so
they survive server restarts and are only served to signed-in clinical staff."""
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from core.choices import Department
from core.i18n import tr_lazy
from core.models import MONEY, Service, TimeStamped

USER = settings.AUTH_USER_MODEL

# FDI numbering, as seen facing the patient: upper right → upper left, lower right → lower left.
UPPER = [18, 17, 16, 15, 14, 13, 12, 11, 21, 22, 23, 24, 25, 26, 27, 28]
LOWER = [48, 47, 46, 45, 44, 43, 42, 41, 31, 32, 33, 34, 35, 36, 37, 38]
TEETH = UPPER + LOWER


class ClinicalNote(TimeStamped):
    """What the doctor found and did at one visit."""

    patient = models.ForeignKey("clinic.Patient", on_delete=models.PROTECT, related_name="clinical_notes")
    appointment = models.ForeignKey(
        "clinic.Appointment", null=True, blank=True, on_delete=models.SET_NULL, related_name="clinical_notes"
    )
    doctor = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="clinical_notes")
    date = models.DateField(default=timezone.localdate, db_index=True)
    department = models.CharField(max_length=20, choices=Department.choices, blank=True)
    complaint = models.TextField(blank=True)
    examination = models.TextField(blank=True)
    diagnosis = models.TextField(blank=True)
    treatment = models.TextField(blank=True)
    plan_next = models.TextField(blank=True)
    follow_up = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.patient.full_name} · {self.date:%Y-%m-%d}"


class ToothCondition(models.TextChoices):
    HEALTHY = "healthy", tr_lazy("tc_healthy")
    CARIES = "caries", tr_lazy("tc_caries")
    FILLED = "filled", tr_lazy("tc_filled")
    ROOT_CANAL = "root_canal", tr_lazy("tc_root_canal")
    CROWN = "crown", tr_lazy("tc_crown")
    BRIDGE = "bridge", tr_lazy("tc_bridge")
    IMPLANT = "implant", tr_lazy("tc_implant")
    MISSING = "missing", tr_lazy("tc_missing")
    EXTRACT = "extract", tr_lazy("tc_extract")
    FRACTURE = "fracture", tr_lazy("tc_fracture")


class ToothRecord(models.Model):
    patient = models.ForeignKey("clinic.Patient", on_delete=models.CASCADE, related_name="teeth")
    tooth = models.PositiveSmallIntegerField()
    condition = models.CharField(max_length=20, choices=ToothCondition.choices, default=ToothCondition.HEALTHY)
    note = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tooth"]
        constraints = [models.UniqueConstraint(fields=["patient", "tooth"], name="one_record_per_tooth")]

    def __str__(self):
        return f"{self.tooth}: {self.get_condition_display()}"


class Prescription(TimeStamped):
    patient = models.ForeignKey("clinic.Patient", on_delete=models.PROTECT, related_name="prescriptions")
    doctor = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="prescriptions")
    date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"Rx {self.patient.full_name} · {self.date:%Y-%m-%d}"


class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE, related_name="items")
    drug = models.CharField(max_length=150)
    dose = models.CharField(max_length=80, blank=True)
    frequency = models.CharField(max_length=80, blank=True)
    duration = models.CharField(max_length=80, blank=True)
    instructions = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]


class PlanStatus(models.TextChoices):
    PROPOSED = "proposed", tr_lazy("ps_proposed")
    ACCEPTED = "accepted", tr_lazy("ps_accepted")
    IN_PROGRESS = "in_progress", tr_lazy("ps_in_progress")
    DONE = "done", tr_lazy("ps_done")
    CANCELLED = "cancelled", tr_lazy("ps_cancelled")


class TreatmentPlan(TimeStamped):
    patient = models.ForeignKey("clinic.Patient", on_delete=models.PROTECT, related_name="treatment_plans")
    doctor = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="treatment_plans")
    title = models.CharField(max_length=150)
    status = models.CharField(max_length=20, choices=PlanStatus.choices, default=PlanStatus.PROPOSED)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def total(self):
        return sum((i.price * i.quantity for i in self.items.all()), 0)

    @property
    def done_count(self):
        return sum(1 for i in self.items.all() if i.is_done)


class PlanItem(models.Model):
    plan = models.ForeignKey(TreatmentPlan, on_delete=models.CASCADE, related_name="items")
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="+")
    tooth = models.CharField(max_length=20, blank=True)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    price = models.DecimalField(**MONEY, validators=[MinValueValidator(0)])
    is_done = models.BooleanField(default=False)
    done_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["id"]


class PhotoStage(models.TextChoices):
    BEFORE = "before", tr_lazy("ph_before")
    PROGRESS = "progress", tr_lazy("ph_progress")
    AFTER = "after", tr_lazy("ph_after")


class PatientPhoto(models.Model):
    patient = models.ForeignKey("clinic.Patient", on_delete=models.CASCADE, related_name="photos")
    taken_on = models.DateField(default=timezone.localdate)
    area = models.CharField(max_length=100)  # e.g. "Face – left cheek", "Upper teeth"
    stage = models.CharField(max_length=20, choices=PhotoStage.choices, default=PhotoStage.BEFORE)
    note = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=30)
    data = models.BinaryField()
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-taken_on", "-id"]

    def __str__(self):
        return f"{self.area} · {self.taken_on:%Y-%m-%d}"
