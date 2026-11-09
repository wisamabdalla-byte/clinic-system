"""Patients and appointments (stage 2)."""
from datetime import date, timedelta

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone

from core.i18n import tr_lazy
from core.models import Branch, PriceList, Service, TimeStamped


class Gender(models.TextChoices):
    MALE = "male", tr_lazy("gender_male")
    FEMALE = "female", tr_lazy("gender_female")


class PatientSource(models.TextChoices):
    WALK_IN = "walk_in", tr_lazy("src_walk_in")
    REFERRAL = "referral", tr_lazy("src_referral")
    FACEBOOK = "facebook", tr_lazy("src_facebook")
    INSTAGRAM = "instagram", tr_lazy("src_instagram")
    GOOGLE = "google", tr_lazy("src_google")
    WHATSAPP = "whatsapp", tr_lazy("src_whatsapp")
    OTHER = "other", tr_lazy("src_other")


class Patient(TimeStamped):
    file_no = models.CharField(max_length=20, unique=True, editable=False)
    full_name = models.CharField(max_length=200)
    gender = models.CharField(max_length=10, choices=Gender.choices)
    date_of_birth = models.DateField(null=True, blank=True)
    phone = models.CharField(max_length=30, db_index=True)
    phone2 = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    national_id = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name="patients")
    source = models.CharField(max_length=20, choices=PatientSource.choices, blank=True)
    price_list = models.ForeignKey(
        PriceList, null=True, blank=True, on_delete=models.SET_NULL, related_name="patients"
    )
    insurance_no = models.CharField(max_length=50, blank=True)
    allergies = models.TextField(blank=True)
    chronic_conditions = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} ({self.file_no})"

    @property
    def age(self):
        if not self.date_of_birth:
            return None
        today = date.today()
        dob = self.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    @property
    def first_name(self):
        return self.full_name.split()[0] if self.full_name else ""

    def save(self, *args, **kwargs):
        if not self.file_no:
            with transaction.atomic():
                super().save(*args, **kwargs)  # get a primary key first
                self.file_no = f"P-{self.pk:06d}"
                super().save(update_fields=["file_no"])
            return
        super().save(*args, **kwargs)


class Status(models.TextChoices):
    BOOKED = "booked", tr_lazy("st_booked")
    CONFIRMED = "confirmed", tr_lazy("st_confirmed")
    ARRIVED = "arrived", tr_lazy("st_arrived")
    IN_PROGRESS = "in_progress", tr_lazy("st_in_progress")
    COMPLETED = "completed", tr_lazy("st_completed")
    CANCELLED = "cancelled", tr_lazy("st_cancelled")
    NO_SHOW = "no_show", tr_lazy("st_no_show")


class BookingSource(models.TextChoices):
    PHONE = "phone", tr_lazy("bsrc_phone")
    WALK_IN = "walk_in", tr_lazy("bsrc_walk_in")
    WHATSAPP = "whatsapp", tr_lazy("bsrc_whatsapp")
    ONLINE = "online", tr_lazy("bsrc_online")
    FOLLOW_UP = "follow_up", tr_lazy("bsrc_follow_up")


# Status changes: action -> (statuses it can start from, resulting status)
TRANSITIONS = {
    "confirm": ({Status.BOOKED}, Status.CONFIRMED),
    "checkin": ({Status.BOOKED, Status.CONFIRMED}, Status.ARRIVED),
    "start": ({Status.ARRIVED}, Status.IN_PROGRESS),
    "complete": ({Status.IN_PROGRESS}, Status.COMPLETED),
    "cancel": ({Status.BOOKED, Status.CONFIRMED, Status.ARRIVED}, Status.CANCELLED),
    "noshow": ({Status.BOOKED, Status.CONFIRMED}, Status.NO_SHOW),
    "restore": ({Status.CANCELLED, Status.NO_SHOW}, Status.BOOKED),
}
INACTIVE_STATUSES = {Status.CANCELLED, Status.NO_SHOW}


class Appointment(TimeStamped):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="appointments")
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="appointments")
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="appointments"
    )
    service = models.ForeignKey(
        Service, null=True, blank=True, on_delete=models.SET_NULL, related_name="appointments"
    )
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField(editable=False)
    duration_minutes = models.PositiveIntegerField(default=30, validators=[MinValueValidator(5)])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BOOKED, db_index=True)
    source = models.CharField(max_length=20, choices=BookingSource.choices, default=BookingSource.PHONE)
    notes = models.TextField(blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)
    queue_number = models.PositiveIntegerField(null=True, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["start"]
        indexes = [models.Index(fields=["doctor", "start"]), models.Index(fields=["branch", "start"])]

    def __str__(self):
        local = timezone.localtime(self.start)
        return f"{self.patient.full_name} · {local:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        self.end = self.start + timedelta(minutes=self.duration_minutes)
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return self.status not in INACTIVE_STATUSES

    def overlapping(self):
        """Other active appointments of the same doctor that overlap this one."""
        end = self.start + timedelta(minutes=self.duration_minutes)
        return (
            Appointment.objects.filter(doctor=self.doctor, start__lt=end, end__gt=self.start)
            .exclude(status__in=INACTIVE_STATUSES)
            .exclude(pk=self.pk)
        )

    def can(self, action):
        allowed_from, _ = TRANSITIONS[action]
        return self.status in allowed_from

    def apply(self, action):
        """Move to the next status. Caller checks permissions and can()."""
        _, target = TRANSITIONS[action]
        now = timezone.now()
        with transaction.atomic():
            if action == "checkin":
                self.checked_in_at = now
                if not self.queue_number:
                    day = timezone.localdate(self.start)
                    last = Appointment.objects.filter(
                        branch=self.branch, start__date=day, queue_number__isnull=False
                    ).aggregate(m=Max("queue_number"))["m"]
                    self.queue_number = (last or 0) + 1
            elif action == "start":
                self.started_at = now
            elif action == "complete":
                self.completed_at = now
            elif action == "restore":
                self.cancel_reason = ""
            self.status = target
            self.save()
