from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm

from .models import User


class UserCreationForm(BaseUserCreationForm):
    class Meta:
        model = User
        fields = ("phone", "name", "role", "clinic")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = UserCreationForm
    ordering = ("phone",)
    list_display = ("phone", "name", "role", "clinic")
    list_filter = ("role", "clinic")
    search_fields = ("phone", "name")
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Profile", {"fields": ("name", "role", "clinic", "designation", "speciality", "registration_number")}),
        ("Access", {"fields": ("is_active", "is_staff", "is_superuser")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("phone", "name", "role", "clinic", "password1", "password2")}),
    )
