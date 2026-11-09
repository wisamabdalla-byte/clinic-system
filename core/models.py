from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction

from .choices import Department, PaymentKind, PriceListKind
from .i18n import current_lang

MONEY = {"max_digits": 12, "decimal_places": 2}
CENT = Decimal("0.01")


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Named(models.Model):
    """Arabic + English name; str() shows the one for the current language."""

    name_ar = models.CharField(max_length=150)
    name_en = models.CharField(max_length=150, blank=True)

    class Meta:
        abstract = True

    @property
    def name(self):
        if current_lang() == "en" and self.name_en:
            return self.name_en
        return self.name_ar or self.name_en

    def __str__(self):
        return self.name


class Branch(TimeStamped, Named):
    code = models.CharField(max_length=20, unique=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]  # the first branch created is everyone's default


class ServiceCategory(TimeStamped, Named):
    department = models.CharField(max_length=20, choices=Department.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["department", "name_ar"]
        verbose_name_plural = "service categories"


class Service(TimeStamped, Named):
    code = models.CharField(max_length=30, unique=True)
    department = models.CharField(max_length=20, choices=Department.choices)
    category = models.ForeignKey(ServiceCategory, on_delete=models.PROTECT, related_name="services")
    price = models.DecimalField(**MONEY, validators=[MinValueValidator(0)])
    duration_minutes = models.PositiveIntegerField(default=30, validators=[MinValueValidator(5)])
    requires_tooth = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["department", "category__name_ar", "name_ar"]


class PriceList(TimeStamped, Named):
    kind = models.CharField(max_length=20, choices=PriceListKind.choices, default=PriceListKind.STANDARD)
    branch = models.ForeignKey(
        Branch, null=True, blank=True, on_delete=models.PROTECT, related_name="price_lists"
    )
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_default", "name_ar"]

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.is_default:
                PriceList.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)
            super().save(*args, **kwargs)

    def price_for(self, service, special=None):
        """Price charged for a service under this list."""
        if special is None:
            item = self.items.filter(service=service).first()
            special = item.price if item else None
        if special is not None:
            return special
        factor = (Decimal(100) - self.discount_percent) / Decimal(100)
        return (service.price * factor).quantize(CENT, rounding=ROUND_HALF_UP)


class PriceListItem(models.Model):
    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name="items")
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="price_list_items")
    price = models.DecimalField(**MONEY, validators=[MinValueValidator(0)])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["price_list", "service"], name="unique_price_per_list")
        ]


class Package(TimeStamped, Named):
    department = models.CharField(max_length=20, choices=Department.choices)
    price = models.DecimalField(**MONEY, validators=[MinValueValidator(0)])
    validity_days = models.PositiveIntegerField(default=180)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["department", "name_ar"]

    @property
    def original_total(self):
        return sum(
            (i.service.price * i.quantity for i in self.items.all()), Decimal(0)
        )

    @property
    def savings(self):
        return max(self.original_total - self.price, Decimal(0))


class PackageItem(models.Model):
    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name="items")
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="package_items")
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["package", "service"], name="unique_service_per_package")
        ]


class PaymentMethod(TimeStamped, Named):
    """How patients pay: cash, card (POS), bank transfer. Used by invoices and receipts."""

    kind = models.CharField(max_length=20, choices=PaymentKind.choices)
    requires_reference = models.BooleanField(default=False)
    account_details = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name_ar"]


class AuditLog(models.Model):
    """Append-only record of sign-ins and changes."""

    ACTIONS = ["create", "update", "login", "logout", "login_failed", "password", "prices", "status"]

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    username = models.CharField(max_length=150, blank=True)  # kept even if the user is removed
    action = models.CharField(max_length=20, choices=[(a, a) for a in ACTIONS])
    model = models.CharField(max_length=60, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=list, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ClinicProfile(models.Model):
    """The clinic's own name, logo and colour (one row). Shown in the sidebar, sign-in page and printouts."""

    name_ar = models.CharField(max_length=150, blank=True)
    name_en = models.CharField(max_length=150, blank=True)
    tagline_ar = models.CharField(max_length=150, blank=True)
    tagline_en = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=60, blank=True)
    address = models.CharField(max_length=255, blank=True)
    primary_color = models.CharField(max_length=7, default="#1587d0")
    logo = models.TextField(blank=True, editable=False)  # data: URI, kept in the database
    invoice_footer = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    CACHE_KEY = "clinic-profile"

    @classmethod
    def get(cls):
        from django.core.cache import cache

        profile = cache.get(cls.CACHE_KEY)
        if profile is None:
            profile = cls.objects.first() or cls()
            cache.set(cls.CACHE_KEY, profile, 60)
        return profile

    def save(self, *args, **kwargs):
        from django.core.cache import cache

        if not self.pk:
            existing = ClinicProfile.objects.first()
            if existing:
                self.pk = existing.pk
        super().save(*args, **kwargs)
        cache.delete(self.CACHE_KEY)

    def display_name(self, lang):
        if lang == "en":
            return self.name_en or settings.CLINIC_NAME_EN
        return self.name_ar or settings.CLINIC_NAME_AR

    def display_tagline(self, lang):
        if lang == "en":
            return self.tagline_en or settings.CLINIC_TAGLINE_EN
        return self.tagline_ar or settings.CLINIC_TAGLINE_AR


class ExampleItem(models.Model):
    """A record created by "Load examples", so "Erase examples" removes exactly those and nothing else."""

    model_label = models.CharField(max_length=60)  # e.g. "clinic.patient"
    object_id = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
