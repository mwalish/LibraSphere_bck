from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from .models import BranchRole, Member, MembershipTier, User


class UserCreationForm(forms.ModelForm):
    """Django's stock form assumes a `username` field; this user logs in with email."""
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Password confirmation", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ("email", "full_name")

    def clean_password2(self):
        p1, p2 = self.cleaned_data.get("password1"), self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("The two passwords don't match")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class UserChangeForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField(help_text="Passwords are stored hashed. Use the change-password form to set a new one.")

    class Meta:
        model = User
        fields = ("email", "password", "full_name", "phone", "locale", "accessibility_prefs",
                  "is_active", "is_staff", "is_superuser")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    ordering = ("email",)
    list_display = ("email", "full_name", "is_staff", "is_superuser")
    search_fields = ("email", "full_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "phone", "locale", "accessibility_prefs")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "full_name", "password1", "password2")}),)


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("member_no", "user", "home_branch", "tier", "status")
    list_filter = ("status", "home_branch", "tier")
    search_fields = ("member_no", "user__email", "user__full_name")


admin.site.register(BranchRole)
admin.site.register(MembershipTier)
