from django.db import models

from .i18n import tr_lazy


class Department(models.TextChoices):
    DENTAL = "dental", tr_lazy("dept_dental")
    DERMA = "derma", tr_lazy("dept_derma")
    GENERAL = "general", tr_lazy("dept_general")


class PaymentKind(models.TextChoices):
    CASH = "cash", tr_lazy("pay_cash")
    CARD = "card", tr_lazy("pay_card")
    TRANSFER = "transfer", tr_lazy("pay_transfer")


class PriceListKind(models.TextChoices):
    STANDARD = "standard", tr_lazy("kind_standard")
    INSURANCE = "insurance", tr_lazy("kind_insurance")
    CONTRACT = "contract", tr_lazy("kind_contract")
    OFFER = "offer", tr_lazy("kind_offer")
