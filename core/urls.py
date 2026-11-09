from django.urls import path

from . import views as v

urlpatterns = [
    path("", v.DashboardView.as_view(), name="dashboard"),
    path("branch/switch/", v.switch_branch, name="switch_branch"),
    path("branches/", v.BranchList.as_view(), name="branch_list"),
    path("branches/new/", v.BranchCreate.as_view(), name="branch_create"),
    path("branches/<int:pk>/edit/", v.BranchUpdate.as_view(), name="branch_edit"),
    path("services/", v.ServiceList.as_view(), name="service_list"),
    path("services/new/", v.ServiceCreate.as_view(), name="service_create"),
    path("services/<int:pk>/edit/", v.ServiceUpdate.as_view(), name="service_edit"),
    path("services/categories/", v.CategoryList.as_view(), name="category_list"),
    path("services/categories/new/", v.CategoryCreate.as_view(), name="category_create"),
    path("services/categories/<int:pk>/edit/", v.CategoryUpdate.as_view(), name="category_edit"),
    path("price-lists/", v.PriceListList.as_view(), name="pricelist_list"),
    path("price-lists/new/", v.PriceListCreate.as_view(), name="pricelist_create"),
    path("price-lists/<int:pk>/edit/", v.PriceListUpdate.as_view(), name="pricelist_edit"),
    path("price-lists/<int:pk>/prices/", v.PriceListPrices.as_view(), name="pricelist_prices"),
    path("packages/", v.PackageList.as_view(), name="package_list"),
    path("packages/new/", v.PackageCreate.as_view(), name="package_create"),
    path("packages/<int:pk>/edit/", v.PackageUpdate.as_view(), name="package_edit"),
    path("payment-methods/", v.PaymentMethodList.as_view(), name="paymentmethod_list"),
    path("payment-methods/new/", v.PaymentMethodCreate.as_view(), name="paymentmethod_create"),
    path("payment-methods/<int:pk>/edit/", v.PaymentMethodUpdate.as_view(), name="paymentmethod_edit"),
    path("activity/", v.AuditList.as_view(), name="audit_list"),
    path("settings/clinic/", v.ClinicSettings.as_view(), name="clinic_settings"),
    path("settings/examples/", v.ExamplesView.as_view(), name="examples"),
]
