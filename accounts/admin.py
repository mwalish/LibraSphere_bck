from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import BranchRole, Member, MembershipTier, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
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
