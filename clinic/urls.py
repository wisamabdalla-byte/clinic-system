from django.urls import path

from . import views as v

urlpatterns = [
    path("patients/", v.PatientList.as_view(), name="patient_list"),
    path("patients/new/", v.PatientCreate.as_view(), name="patient_create"),
    path("patients/search.json", v.PatientSearch.as_view(), name="patient_search"),
    path("patients/<int:pk>/", v.PatientDetail.as_view(), name="patient_detail"),
    path("patients/<int:pk>/edit/", v.PatientUpdate.as_view(), name="patient_edit"),
    path("patients/<int:pk>/book/", v.PatientBook.as_view(), name="patient_book"),
    path("appointments/", v.AppointmentList.as_view(), name="appointment_list"),
    path("appointments/calendar/", v.AppointmentCalendar.as_view(), name="appointment_calendar"),
    path("appointments/new/", v.AppointmentCreate.as_view(), name="appointment_create"),
    path("appointments/<int:pk>/edit/", v.AppointmentUpdate.as_view(), name="appointment_edit"),
    path("appointments/<int:pk>/<str:action>/", v.AppointmentAction.as_view(), name="appointment_action"),
    path("queue/", v.QueueScreen.as_view(), name="queue_screen"),
    path("queue/data.json", v.QueueData.as_view(), name="queue_data"),
]
