from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, TemplateView, UpdateView

from accounts.models import User
from accounts.roles import Role, has_perm

from .audit import log
from .choices import Department, PriceListKind
from .forms import (
    BranchForm, CategoryForm, PackageForm, PackageItemFormSet, PaymentMethodForm, PriceListForm,
    ServiceForm,
)
from .i18n import tr
from .mixins import Col, CrudFormMixin, CrudListView, PermissionMixin
from .models import (
    AuditLog, Branch, Package, PaymentMethod, PriceList, PriceListItem, Service, ServiceCategory,
)


def forbidden(request, exception=None):
    return render(request, "403.html", status=403)


@login_required
@require_POST
def switch_branch(request):
    branch_id = request.POST.get("branch")
    if any(str(b.pk) == branch_id for b in request.branches):
        request.session["branch_id"] = int(branch_id)
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        target = reverse("dashboard")
    return redirect(target)


# ---------------------------------------------------------------- dashboard
class DashboardView(PermissionMixin, TemplateView):
    module = "dashboard"
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        role_counts = dict(
            User.objects.filter(is_active=True).values_list("role").annotate(n=Count("id"))
        )
        dept_counts = dict(
            Service.objects.filter(is_active=True).values_list("department").annotate(n=Count("id"))
        )
        ctx.update(
            stats=[
                {"label": tr("stat_services"), "value": sum(dept_counts.values()), "icon": "activity"},
                {"label": tr("stat_staff"), "value": sum(role_counts.values()), "icon": "users"},
                {"label": tr("stat_pricelists"), "value": PriceList.objects.filter(is_active=True).count(), "icon": "tag"},
                {"label": tr("stat_packages"), "value": Package.objects.filter(is_active=True).count(), "icon": "package"},
            ],
            roles=[(label, role_counts.get(value, 0)) for value, label in Role.choices],
            departments=[(label, dept_counts.get(value, 0)) for value, label in Department.choices],
            stages=[
                {"n": n, "label": tr(f"stage_{n}"),
                 "state": "done" if n <= 4 else ("next" if n == 5 else "planned")}
                for n in range(1, 8)
            ],
        )
        from clinic.models import Patient, Status
        from clinic.views import todays_counts

        if has_perm(self.request.user, "appointments"):
            today = todays_counts(self.request.branch)
            ctx["today"] = [
                {"label": tr("today_appointments"), "value": sum(today.values()), "icon": "calendar",
                 "url": reverse("appointment_list")},
                {"label": tr("today_waiting"), "value": today.get(Status.ARRIVED, 0), "icon": "users",
                 "url": f"{reverse('appointment_list')}?status=arrived"},
                {"label": tr("st_in_progress"), "value": today.get(Status.IN_PROGRESS, 0), "icon": "activity",
                 "url": f"{reverse('appointment_list')}?status=in_progress"},
                {"label": tr("today_done"), "value": today.get(Status.COMPLETED, 0), "icon": "check-circle",
                 "url": f"{reverse('appointment_list')}?status=completed"},
            ]
        if has_perm(self.request.user, "patients"):
            ctx["stats"].insert(0, {"label": tr("stat_patients"), "value": Patient.objects.count(), "icon": "user"})
        user = self.request.user
        ctx["quick"] = [
            q for q in [
                ("book", "", "appointment_create", "appointments.edit", "calendar"),
                ("add", "ent_invoice", "invoice_create", "invoices.edit", "receipt"),
                ("add", "ent_expense", "expense_create", "expenses.edit", "minus-circle"),
                ("add", "ent_patient", "patient_create", "patients.edit", "user"),
                ("add", "ent_service", "service_create", "services.edit", "activity"),
                ("add", "ent_package", "package_create", "packages.edit", "package"),
                ("add", "ent_pricelist", "pricelist_create", "pricelists.edit", "tag"),
                ("add", "ent_user", "user_create", "users.edit", "users"),
            ]
            if has_perm(user, *q[3].split("."))
        ]
        ctx["quick"] = [
            {"label": f"{tr(verb)} {tr(ent)}".strip() if ent else tr(verb), "url": reverse(url), "icon": icon}
            for verb, ent, url, _, icon in ctx["quick"]
        ]
        return ctx


# ---------------------------------------------------------------- branches
class BranchList(CrudListView):
    module = "branches"
    model = Branch
    title_key, subtitle_key = "nav_branches", "branches_sub"
    search_fields = ["name_ar", "name_en", "code", "phone"]
    create_url, edit_url = "branch_create", "branch_edit"
    columns = [
        Col("name", lambda o: o.name, "title", sub=lambda o: o.address),
        Col("code", lambda o: o.code, "code"),
        Col("phone", lambda o: o.phone, "ltr"),
        Col("stat_staff", lambda o: o.staff.filter(is_active=True).count(), "num"),
    ]


class BranchCreate(CrudFormMixin, CreateView):
    module, model, form_class = "branches", Branch, BranchForm
    entity_key, list_url = "ent_branch", "branch_list"


class BranchUpdate(CrudFormMixin, UpdateView):
    module, model, form_class = "branches", Branch, BranchForm
    entity_key, list_url = "ent_branch", "branch_list"


# ---------------------------------------------------------------- categories
class CategoryList(CrudListView):
    module = "services"
    title_key, subtitle_key = "nav_categories", "categories_sub"
    search_fields = ["name_ar", "name_en"]
    filters = [("department", "department", Department.choices)]
    create_url, edit_url = "category_create", "category_edit"
    columns = [
        Col("name", lambda o: o.name, "title"),
        Col("department", lambda o: o.get_department_display(), "badge"),
        Col("stat_services", lambda o: o.n_services, "num"),
    ]

    def get_queryset(self):
        self.queryset = ServiceCategory.objects.annotate(
            n_services=Count("services", filter=Q(services__is_active=True))
        )
        return super().get_queryset()


class CategoryCreate(CrudFormMixin, CreateView):
    module, model, form_class = "services", ServiceCategory, CategoryForm
    entity_key, list_url = "ent_category", "category_list"


class CategoryUpdate(CrudFormMixin, UpdateView):
    module, model, form_class = "services", ServiceCategory, CategoryForm
    entity_key, list_url = "ent_category", "category_list"


# ---------------------------------------------------------------- services
class ServiceList(CrudListView):
    module = "services"
    title_key, subtitle_key = "nav_services", "services_sub"
    search_fields = ["name_ar", "name_en", "code"]
    filters = [("department", "department", Department.choices)]
    create_url, edit_url = "service_create", "service_edit"
    header_links = [("nav_categories", "category_list", "list", "services.view")]
    columns = [
        Col("name", lambda o: o.name, "title", sub=lambda o: o.category.name),
        Col("code", lambda o: o.code, "code"),
        Col("department", lambda o: o.get_department_display(), "badge"),
        Col("duration_minutes", lambda o: o.duration_minutes, "num"),
        Col("base_price", lambda o: o.price, "money"),
    ]

    def get_queryset(self):
        self.queryset = Service.objects.select_related("category")
        return super().get_queryset()


class ServiceCreate(CrudFormMixin, CreateView):
    module, model, form_class = "services", Service, ServiceForm
    entity_key, list_url = "ent_service", "service_list"


class ServiceUpdate(CrudFormMixin, UpdateView):
    module, model, form_class = "services", Service, ServiceForm
    entity_key, list_url = "ent_service", "service_list"


# ---------------------------------------------------------------- price lists
def validity_text(pl):
    if not pl.valid_from and not pl.valid_to:
        return tr("always")
    return f"{pl.valid_from or '…'} → {pl.valid_to or '…'}"


class PriceListList(CrudListView):
    module = "pricelists"
    title_key, subtitle_key = "nav_pricelists", "pricelists_sub"
    search_fields = ["name_ar", "name_en"]
    filters = [("kind", "kind", PriceListKind.choices)]
    create_url, edit_url = "pricelist_create", "pricelist_edit"
    row_actions = [("set_prices", "pricelist_prices", "tag", "pricelists.edit")]
    columns = [
        Col("name", lambda o: o.name, "title",
            sub=lambda o: tr("default_badge") if o.is_default else ""),
        Col("kind", lambda o: o.get_kind_display(), "badge"),
        Col("branch", lambda o: o.branch.name if o.branch else tr("all_branches")),
        Col("discount_percent", lambda o: f"{o.discount_percent.normalize():f}%", "num"),
        Col("special_count", lambda o: o.n_items, "num"),
        Col("validity", validity_text),
    ]

    def get_queryset(self):
        self.queryset = PriceList.objects.select_related("branch").annotate(n_items=Count("items"))
        return super().get_queryset()


class PriceListCreate(CrudFormMixin, CreateView):
    module, model, form_class = "pricelists", PriceList, PriceListForm
    entity_key, list_url = "ent_pricelist", "pricelist_list"

    def get_success_url(self):
        return reverse("pricelist_prices", args=[self.object.pk])


class PriceListUpdate(CrudFormMixin, UpdateView):
    module, model, form_class = "pricelists", PriceList, PriceListForm
    entity_key, list_url = "ent_pricelist", "pricelist_list"


class PriceListPrices(PermissionMixin, View):
    """One table with every active service: type a special price or leave blank."""

    module, action = "pricelists", "edit"
    template_name = "core/pricelist_prices.html"

    def rows(self, price_list, posted=None):
        specials = dict(price_list.items.values_list("service_id", "price"))
        rows = []
        for service in Service.objects.filter(is_active=True).select_related("category"):
            special = specials.get(service.pk)
            value = posted.get(f"price_{service.pk}", "") if posted is not None else (
                "" if special is None else f"{special:.2f}"
            )
            rows.append({
                "service": service,
                "value": value,
                "effective": price_list.price_for(service, special),
            })
        return rows

    def render_page(self, request, price_list, rows):
        groups = {}
        for row in rows:
            groups.setdefault(row["service"].get_department_display(), []).append(row)
        return render(request, self.template_name, {
            "price_list": price_list,
            "title": tr("prices_for", price_list.name),
            "help": tr("prices_help", f"{price_list.discount_percent.normalize():f}"),
            "groups": groups.items(),
            "back_url": reverse("pricelist_list"),
        })

    def get(self, request, pk):
        price_list = get_object_or_404(PriceList, pk=pk)
        return self.render_page(request, price_list, self.rows(price_list))

    def post(self, request, pk):
        price_list = get_object_or_404(PriceList, pk=pk)
        services = list(Service.objects.filter(is_active=True))
        parsed, bad = {}, []
        for service in services:
            raw = request.POST.get(f"price_{service.pk}", "").strip().replace(",", "")
            if raw == "":
                parsed[service.pk] = None
                continue
            try:
                value = Decimal(raw)
                if value < 0 or value.as_tuple().exponent < -2 or value >= Decimal("1e10"):
                    raise InvalidOperation
                parsed[service.pk] = value
            except InvalidOperation:
                bad.append(service.name)
        if bad:
            messages.error(request, tr("invalid_price", "، ".join(bad)))
            return self.render_page(request, price_list, self.rows(price_list, request.POST))
        with transaction.atomic():
            for service_id, value in parsed.items():
                if value is None:
                    price_list.items.filter(service_id=service_id).delete()
                else:
                    PriceListItem.objects.update_or_create(
                        price_list=price_list, service_id=service_id, defaults={"price": value}
                    )
        count = sum(1 for v in parsed.values() if v is not None)
        log(request, "prices", price_list, [f"{count} special prices"])
        messages.success(request, tr("prices_saved", count))
        return redirect("pricelist_list")


# ---------------------------------------------------------------- packages
class PackageList(CrudListView):
    module = "packages"
    title_key, subtitle_key = "nav_packages", "packages_sub"
    search_fields = ["name_ar", "name_en"]
    filters = [("department", "department", Department.choices)]
    create_url, edit_url = "package_create", "package_edit"
    columns = [
        Col("name", lambda o: o.name, "title",
            sub=lambda o: " + ".join(f"{i.quantity}× {i.service.name}" for i in o.items.all())),
        Col("department", lambda o: o.get_department_display(), "badge"),
        Col("validity_days", lambda o: o.validity_days, "num"),
        Col("original_total", lambda o: o.original_total, "money-muted"),
        Col("price", lambda o: o.price, "money"),
        Col("savings", lambda o: o.savings, "money-good"),
    ]

    def get_queryset(self):
        self.queryset = Package.objects.prefetch_related("items__service")
        return super().get_queryset()


class PackageFormMixin(CrudFormMixin):
    module, model, form_class = "packages", Package, PackageForm
    entity_key, list_url = "ent_package", "package_list"
    template_name = "core/package_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if "formset" not in kwargs:
            instance = self.object if getattr(self, "object", None) else Package()
            ctx["formset"] = PackageItemFormSet(instance=instance, prefix="items")
        ctx["service_prices"] = {
            str(s.pk): str(s.price) for s in Service.objects.filter(is_active=True)
        }
        return ctx

    def post(self, request, *args, **kwargs):
        self.object = self.get_object() if "pk" in kwargs else None
        form = self.get_form()
        formset = PackageItemFormSet(
            request.POST, instance=self.object or Package(), prefix="items"
        )
        if form.is_valid() and formset.is_valid():
            is_new = self.object is None
            with transaction.atomic():
                self.object = form.save()
                formset.instance = self.object
                formset.save()
            changed = form.changed_data + (["items"] if formset.has_changed() else [])
            log(request, "create" if is_new else "update", self.object, changed)
            messages.success(request, tr("saved"))
            return redirect(self.get_success_url())
        messages.error(request, tr("fix_errors"))
        return self.render_to_response(self.get_context_data(form=form, formset=formset))


class PackageCreate(PackageFormMixin, CreateView):
    pass


class PackageUpdate(PackageFormMixin, UpdateView):
    pass


# ---------------------------------------------------------------- payment methods
class PaymentMethodList(CrudListView):
    module = "paymentmethods"
    model = PaymentMethod
    title_key, subtitle_key = "nav_payment_methods", "payment_methods_sub"
    search_fields = ["name_ar", "name_en", "account_details"]
    create_url, edit_url = "paymentmethod_create", "paymentmethod_edit"
    columns = [
        Col("name", lambda o: o.name, "title", sub=lambda o: o.account_details),
        Col("payment_kind", lambda o: o.get_kind_display(), "badge"),
        Col("requires_reference", lambda o: tr("yes") if o.requires_reference else tr("no")),
    ]


class PaymentMethodCreate(CrudFormMixin, CreateView):
    module, model, form_class = "paymentmethods", PaymentMethod, PaymentMethodForm
    entity_key, list_url = "ent_payment_method", "paymentmethod_list"


class PaymentMethodUpdate(CrudFormMixin, UpdateView):
    module, model, form_class = "paymentmethods", PaymentMethod, PaymentMethodForm
    entity_key, list_url = "ent_payment_method", "paymentmethod_list"


# ---------------------------------------------------------------- audit
class AuditList(CrudListView):
    module = "audit"
    model = AuditLog
    title_key, subtitle_key = "nav_audit", "audit_sub"
    has_status = False
    paginate_by = 50
    search_fields = ["username", "object_repr", "model", "ip"]
    filters = [("action", "action", [(a, tr(f"act_{a}")) for a in AuditLog.ACTIONS])]
    columns = [
        Col("when", lambda o: o.created_at, "datetime"),
        Col("user", lambda o: o.username or "—"),
        Col("action", lambda o: tr(f"act_{o.action}"), "badge"),
        Col("record", lambda o: o.object_repr, sub=lambda o: o.model),
        Col("changed_fields", lambda o: ", ".join(o.changes), "muted"),
        Col("ip", lambda o: o.ip or "", "ltr"),
    ]

    def get_context_data(self, **kwargs):
        # Translate the action labels at request time, in the user's language.
        self.filters = [("action", "action", [(a, tr(f"act_{a}")) for a in AuditLog.ACTIONS])]
        return super().get_context_data(**kwargs)


# ---------------------------------------------------------------- clinic settings (branding)
class ClinicSettings(CrudFormMixin, UpdateView):
    module = "settings"
    entity_key, list_url = "nav_clinic_settings", "clinic_settings"
    template_name = "core/clinic_settings.html"

    def get_form_class(self):
        from .forms import ClinicProfileForm

        return ClinicProfileForm

    def get_object(self, queryset=None):
        from .models import ClinicProfile

        return ClinicProfile.objects.first() or ClinicProfile()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = ctx["form"]
        ctx["title"] = tr("nav_clinic_settings")
        ctx["sections"] = [
            {"title": tr("brand_names"), "fields": [form[f] for f in ("name_ar", "name_en", "tagline_ar", "tagline_en")]},
            {"title": tr("brand_look"), "help": tr("brand_look_help"),
             "fields": [form[f] for f in ("logo_file", "remove_logo", "primary_color")]},
            {"title": tr("brand_print"), "fields": [form[f] for f in ("phone", "address", "invoice_footer")]},
        ]
        return ctx


# ---------------------------------------------------------------- example data (try the system, then erase)
EXAMPLE_LABELS = {
    "core.branch": "nav_branches", "core.paymentmethod": "nav_payment_methods", "core.servicecategory": "nav_categories",
    "core.service": "nav_services", "core.pricelist": "nav_pricelists", "core.pricelistitem": "special_prices",
    "core.package": "nav_packages", "accounts.user": "ex_doctors", "clinic.patient": "nav_patients",
    "clinic.appointment": "nav_appointments", "finance.invoice": "nav_invoices", "finance.payment": "payments",
    "finance.doctorsettlement": "ex_settlements", "finance.doctorpayout": "nav_payouts", "finance.expense": "nav_expenses",
    "records.clinicalnote": "tab_notes", "records.toothrecord": "tab_teeth", "records.prescription": "tab_rx",
    "records.treatmentplan": "tab_plans", "records.patientphoto": "tab_photos",
}


class ExamplesView(PermissionMixin, TemplateView):
    module = "settings"
    template_name = "core/examples.html"

    def get_context_data(self, **kwargs):
        from .examples import example_counts

        ctx = super().get_context_data(**kwargs)
        from django.apps import apps

        counts = example_counts()
        rows = []
        for label, n in counts.items():
            model = apps.get_model(label)
            key = EXAMPLE_LABELS.get(label)
            rows.append({"label": tr(key) if key else str(model._meta.verbose_name_plural), "key": label, "n": n})
        ctx.update(title=tr("nav_examples"), rows=sorted(rows, key=lambda r: r["key"]), total=sum(counts.values()))
        return ctx

    def post(self, request):
        from .examples import erase_examples, has_examples, load_examples

        if not has_perm(request.user, "settings", "edit"):
            return forbidden(request)
        if request.POST.get("do") == "load":
            if has_examples():
                messages.error(request, tr("examples_already"))
            else:
                n = load_examples(request.branch, request.user)
                log(request, "create", None, [f"examples loaded ({n})"])
                messages.success(request, tr("examples_loaded", n))
        elif request.POST.get("do") == "erase":
            deleted, kept = erase_examples()
            log(request, "update", None, [f"examples erased ({deleted}, kept {kept})"])
            messages.success(request, tr("examples_erased", deleted))
            if kept:
                messages.error(request, tr("examples_kept", kept))
        return redirect("examples")
