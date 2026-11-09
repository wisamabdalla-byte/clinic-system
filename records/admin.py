from django.contrib import admin

from .models import ClinicalNote, Prescription, ToothRecord, TreatmentPlan

admin.site.register([ClinicalNote, Prescription, ToothRecord, TreatmentPlan])
