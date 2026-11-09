from django.contrib import admin

from .models import DoctorPayout, DoctorSettlement, Expense, Invoice, InvoiceItem, Payment


class ItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "date", "patient", "doctor", "total", "paid", "is_cancelled")
    search_fields = ("number", "patient__full_name")
    inlines = [ItemInline]


admin.site.register([Payment, DoctorSettlement, DoctorPayout, Expense])
