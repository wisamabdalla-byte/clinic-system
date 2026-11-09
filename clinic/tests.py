from datetime import datetime, time, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.models import Service

from .models import Appointment, Patient, Status


class ClinicStage2Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", force=True, stdout=StringIO())

    def login(self, username):
        self.client.force_login(User.objects.get(username=username))

    def test_pages_load_for_reception_in_both_languages(self):
        self.login("reception")
        patient = Patient.objects.first()
        appt = Appointment.objects.exclude(status=Status.COMPLETED).first()
        urls = [reverse("patient_list"), reverse("patient_create"), reverse("patient_detail", args=[patient.pk]),
                reverse("patient_edit", args=[patient.pk]), reverse("appointment_list"),
                reverse("appointment_calendar"), reverse("appointment_create"),
                reverse("appointment_edit", args=[appt.pk]), reverse("queue_screen"), reverse("dashboard")]
        for lang in ("ar", "en"):
            self.client.cookies["django_language"] = lang
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 200, f"{url} [{lang}]")

    def test_file_numbers_are_assigned(self):
        p = Patient.objects.create(full_name="مريض تجربة", gender="male", phone="0100")
        self.assertEqual(p.file_no, f"P-{p.pk:06d}")

    def test_patient_search_json(self):
        self.login("reception")
        r = self.client.get(reverse("patient_search"), {"q": "فاطمة"})
        self.assertEqual(r.json()["results"][0]["name"], "فاطمة علي حسن")
        self.login("store")
        self.assertEqual(self.client.get(reverse("patient_search"), {"q": "فاطمة"}).status_code, 403)

    def test_create_patient_then_book(self):
        self.login("reception")
        r = self.client.post(reverse("patient_create"), {
            "full_name": "سارة جديد", "gender": "female", "phone": "01099999999", "is_active": "on",
            "then": "book",
        })
        p = Patient.objects.get(phone="01099999999")
        self.assertRedirects(r, f"{reverse('appointment_create')}?patient={p.pk}")

    def book(self, doctor, day, hh, mm, minutes=30, patient=None):
        return self.client.post(reverse("appointment_create"), {
            "patient": (patient or Patient.objects.first()).pk, "doctor": doctor.pk,
            "service": Service.objects.get(code="DEN-001").pk,
            "date": day.isoformat(), "time": f"{hh:02d}:{mm:02d}", "duration_minutes": minutes,
            "source": "phone",
        })

    def test_booking_and_double_booking_is_refused(self):
        self.login("reception")
        doctor = User.objects.get(username="dr.sara")
        day = timezone.localdate() + timedelta(days=7)
        r = self.book(doctor, day, 10, 0)
        self.assertEqual(r.status_code, 302)
        r = self.book(doctor, day, 10, 15)  # overlaps 10:00-10:30
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "مشغول")
        r = self.book(doctor, day, 10, 30)  # right after: fine
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Appointment.objects.filter(doctor=doctor, start__date=day).count(), 2)

    def test_status_flow_and_queue_numbers(self):
        self.login("reception")
        doctor = User.objects.get(username="dr.lina")
        day = timezone.localdate()
        start = timezone.make_aware(datetime.combine(day, time(21, 0)))
        appt = Appointment.objects.create(branch=doctor.branches.first(), patient=Patient.objects.last(),
                                          doctor=doctor, start=start, duration_minutes=20)
        before = Appointment.objects.filter(branch=appt.branch, start__date=day).exclude(
            queue_number=None).count()
        self.client.post(reverse("appointment_action", args=[appt.pk, "checkin"]))
        appt.refresh_from_db()
        self.assertEqual(appt.status, Status.ARRIVED)
        self.assertEqual(appt.queue_number, before + 1)
        self.login("dr.sara")  # another doctor can't touch it
        self.assertEqual(self.client.post(reverse("appointment_action", args=[appt.pk, "start"])).status_code, 403)
        self.login("reception")  # reception calls the patient in...
        self.client.post(reverse("appointment_action", args=[appt.pk, "start"]))
        # ...but only the doctor (or nurse) finishes the visit
        self.assertEqual(self.client.post(reverse("appointment_action", args=[appt.pk, "complete"])).status_code, 403)
        self.login("dr.lina")
        self.client.post(reverse("appointment_action", args=[appt.pk, "complete"]))
        appt.refresh_from_db()
        self.assertEqual(appt.status, Status.COMPLETED)
        self.assertIsNotNone(appt.completed_at)
        # finished visits can't be checked in again
        self.login("reception")
        self.assertEqual(self.client.post(reverse("appointment_action", args=[appt.pk, "checkin"])).status_code, 403)

    def test_queue_data_shows_first_names_only(self):
        self.login("reception")
        data = self.client.get(reverse("queue_data")).json()
        names = [p["name"] for d in data["doctors"] for p in d["now"] + d["waiting"]]
        self.assertTrue(names)
        self.assertTrue(all(" " not in n for n in names))

    def test_roles_cannot_reach_appointments(self):
        for username in ("accountant", "store"):
            self.login(username)
            self.assertEqual(self.client.get(reverse("appointment_list")).status_code, 403)
        self.login("store")
        self.assertEqual(self.client.get(reverse("patient_list")).status_code, 403)

    def test_doctor_sees_own_schedule_by_default(self):
        self.login("dr.lina")
        r = self.client.get(reverse("appointment_list"))
        doctors = {a.doctor.username for a in r.context["appointments"]}
        self.assertEqual(doctors, {"dr.lina"})
