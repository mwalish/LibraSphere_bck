from django.contrib import admin

from .models import Hold, Loan, Policy

admin.site.register([Policy, Hold])


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ("id", "item", "member", "issued_at", "due_at", "returned_at")
    list_filter = ("branch",)
    search_fields = ("item__barcode", "member__member_no")
