from django.contrib.auth import views as auth_views
from django.urls import path

from . import views as v

urlpatterns = [
    path("login/", v.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),  # POST only
    path("password/", v.ChangeOwnPassword.as_view(), name="password_change"),
    path("users/", v.UserList.as_view(), name="user_list"),
    path("users/new/", v.UserCreate.as_view(), name="user_create"),
    path("users/<int:pk>/edit/", v.UserUpdate.as_view(), name="user_edit"),
    path("users/<int:pk>/password/", v.UserSetPassword.as_view(), name="user_password"),
]
