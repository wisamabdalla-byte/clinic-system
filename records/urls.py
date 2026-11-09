from django.urls import path

from . import views as v

urlpatterns = [
    path("clinical/", v.ClinicalHome.as_view(), name="clinical_home"),
    path("patients/<int:pk>/clinical/", v.PatientClinical.as_view(), name="clinical_patient"),
    path("patients/<int:pk>/notes/new/", v.NoteEdit.as_view(), name="note_create"),
    path("notes/<int:note_id>/edit/", v.NoteEdit.as_view(), name="note_edit"),
    path("patients/<int:pk>/teeth/", v.ToothSave.as_view(), name="tooth_save"),
    path("patients/<int:pk>/rx/new/", v.RxEdit.as_view(), name="rx_create"),
    path("rx/<int:pk>/print/", v.RxPrint.as_view(), name="rx_print"),
    path("patients/<int:pk>/plans/new/", v.PlanEdit.as_view(), name="plan_create"),
    path("plans/<int:obj_id>/edit/", v.PlanEdit.as_view(), name="plan_edit"),
    path("patients/<int:pk>/photos/new/", v.PhotoUpload.as_view(), name="photo_create"),
    path("photos/<int:photo_id>/", v.PhotoImage.as_view(), name="photo_image"),
    path("photos/<int:photo_id>/delete/", v.PhotoDelete.as_view(), name="photo_delete"),
]
