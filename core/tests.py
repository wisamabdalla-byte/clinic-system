from decimal import Decimal
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from core.models import AuditLog, Package, PriceList, Service, ServiceCategory

PASSWORD = "Clinic@2026"
LIST_PAGES = ["dashboard", "branch_list", "service_list", "category_list", "pricelist_list",
              "package_list", "user_list", "audit_list", "paymentmethod_list"]


class ClinicTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", force=True, stdout=StringIO())

    def setUp(self):
        cache.clear()

    def login(self, username):
        self.client.force_login(User.objects.get(username=username))

    def test_login_page_and_sign_in(self):
        r = self.client.get(reverse("login"))
        self.assertContains(r, "تسجيل الدخول")
        r = self.client.post(reverse("login"), {"username": "admin", "password": PASSWORD})
        self.assertRedirects(r, reverse("dashboard"))
        self.assertTrue(AuditLog.objects.filter(action="login", username="admin").exists())

    def test_lockout_after_failures(self):
        for _ in range(5):
            self.client.post(reverse("login"), {"username": "admin", "password": "wrong"})
        r = self.client.post(reverse("login"), {"username": "admin", "password": PASSWORD})
        self.assertEqual(r.status_code, 200)  # still on the login page
        self.assertContains(r, "١٥ دقيقة")

    def test_anonymous_redirected(self):
        r = self.client.get(reverse("service_list"))
        self.assertRedirects(r, f"{reverse('login')}?next={reverse('service_list')}")

    def test_admin_sees_every_page_in_both_languages(self):
        self.login("admin")
        for lang in ("ar", "en"):
            self.client.cookies["django_language"] = lang
            for name in LIST_PAGES:
                r = self.client.get(reverse(name))
                self.assertEqual(r.status_code, 200, f"{name} [{lang}]")
                self.assertContains(r, f'dir="{"rtl" if lang == "ar" else "ltr"}"')
            for name in ["service_create", "package_create", "pricelist_create", "user_create",
                         "branch_create", "category_create", "password_change", "paymentmethod_create"]:
                self.assertEqual(self.client.get(reverse(name)).status_code, 200, f"{name} [{lang}]")
        pl = PriceList.objects.first()
        self.assertEqual(self.client.get(reverse("pricelist_prices", args=[pl.pk])).status_code, 200)

    def test_role_permissions(self):
        self.login("nurse")
        self.assertEqual(self.client.get(reverse("service_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("service_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("user_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("pricelist_list")).status_code, 403)
        r = self.client.get(reverse("dashboard"))
        self.assertNotContains(r, reverse("user_list"))  # hidden from the sidebar
        self.login("accountant")
        self.assertEqual(self.client.get(reverse("pricelist_create")).status_code, 200)
        self.assertEqual(self.client.get(reverse("package_create")).status_code, 403)

    def test_create_service_validates_category_department(self):
        self.login("manager")
        derma_cat = ServiceCategory.objects.filter(department="derma").first()
        data = {"code": "DEN-100", "name_ar": "خدمة", "department": "dental",
                "category": derma_cat.pk, "price": "100", "duration_minutes": "30", "is_active": "on"}
        r = self.client.post(reverse("service_create"), data)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Service.objects.filter(code="DEN-100").exists())
        data["category"] = ServiceCategory.objects.filter(department="dental").first().pk
        r = self.client.post(reverse("service_create"), data)
        self.assertRedirects(r, reverse("service_list"))
        self.assertTrue(AuditLog.objects.filter(action="create", model="service").exists())

    def test_price_list_prices(self):
        self.login("admin")
        pl = PriceList.objects.get(name_en="Insurance - Sample Co.")
        svc = Service.objects.get(code="DEN-009")  # 2500, no special price
        self.assertEqual(pl.price_for(svc), Decimal("2125.00"))  # 15% off
        data = {f"price_{s.pk}": "" for s in Service.objects.filter(is_active=True)}
        data[f"price_{svc.pk}"] = "11000"
        r = self.client.post(reverse("pricelist_prices", args=[pl.pk]), data)
        self.assertRedirects(r, reverse("pricelist_list"))
        self.assertEqual(pl.price_for(svc), Decimal("11000"))
        self.assertEqual(pl.items.count(), 1)  # the blank ones were cleared
        data[f"price_{svc.pk}"] = "abc"
        r = self.client.post(reverse("pricelist_prices", args=[pl.pk]), data)
        self.assertEqual(r.status_code, 200)

    def test_payment_methods_seeded_and_permissions(self):
        from core.models import PaymentMethod

        self.assertEqual(sorted(PaymentMethod.objects.values_list("kind", flat=True)), ["card", "cash", "transfer"])
        self.login("accountant")
        self.assertEqual(self.client.get(reverse("paymentmethod_create")).status_code, 200)
        self.login("reception")
        r = self.client.get(reverse("paymentmethod_list"))
        self.assertContains(r, "حوالة مصرفية")
        self.assertEqual(self.client.get(reverse("paymentmethod_create")).status_code, 403)

    def test_libyan_currency_shown(self):
        self.login("admin")
        r = self.client.get(reverse("service_list"))
        self.assertContains(r, "د.ل")
        self.client.cookies["django_language"] = "en"
        self.assertContains(self.client.get(reverse("service_list")), "LYD")

    def test_only_one_default_price_list(self):
        PriceList.objects.create(name_ar="جديدة", is_default=True)
        self.assertEqual(PriceList.objects.filter(is_default=True).count(), 1)

    def test_package_with_items(self):
        self.login("admin")
        s1, s2 = Service.objects.filter(department="derma")[:2]
        base = {"name_ar": "باقة اختبار", "department": "derma", "price": "1000",
                "validity_days": "30", "is_active": "on",
                "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000"}
        dup = dict(base, **{"items-TOTAL_FORMS": "2",
                            "items-0-service": s1.pk, "items-0-quantity": "2",
                            "items-1-service": s1.pk, "items-1-quantity": "1"})
        self.assertEqual(self.client.post(reverse("package_create"), dup).status_code, 200)
        ok = dict(base, **{"items-TOTAL_FORMS": "3",
                           "items-0-service": s1.pk, "items-0-quantity": "2",
                           "items-1-service": s2.pk, "items-1-quantity": "1",
                           "items-2-service": "", "items-2-quantity": "1"})
        r = self.client.post(reverse("package_create"), ok)
        self.assertRedirects(r, reverse("package_list"))
        pkg = Package.objects.get(name_ar="باقة اختبار")
        self.assertEqual(pkg.items.count(), 2)
        self.assertEqual(pkg.original_total, s1.price * 2 + s2.price)

    def test_admin_cannot_lock_self_out(self):
        self.login("admin")
        admin = User.objects.get(username="admin")
        data = {"first_name": "مدير", "username": "admin", "role": "nurse", "color": "#0f766e"}
        r = self.client.post(reverse("user_edit", args=[admin.pk]), data)
        self.assertEqual(r.status_code, 200)
        admin.refresh_from_db()
        self.assertEqual(admin.role, "admin")

    def test_create_user_and_branch_switch(self):
        self.login("admin")
        data = {"first_name": "رنا", "username": "rana", "role": "receptionist", "color": "#0f766e",
                "branches": [1], "is_active": "on",
                "password1": "Strong#Pass2026", "password2": "Strong#Pass2026"}
        r = self.client.post(reverse("user_create"), data)
        self.assertRedirects(r, reverse("user_list"))
        rana = User.objects.get(username="rana")
        self.assertTrue(rana.check_password("Strong#Pass2026"))
        self.client.force_login(rana)
        r = self.client.post(reverse("switch_branch"), {"branch": "2", "next": "/"})
        self.assertNotEqual(self.client.session.get("branch_id"), 2)  # not her branch
