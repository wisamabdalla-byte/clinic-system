from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),  # language switch (POST)
    path("django-admin/", admin.site.urls),  # emergency/technical admin only
    path("accounts/", include("accounts.urls")),
    path("", include("clinic.urls")),
    path("", include("finance.urls")),
    path("", include("records.urls")),
    path("", include("core.urls")),
]

handler403 = "core.views.forbidden"
