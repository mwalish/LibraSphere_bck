from django.contrib import admin

from .models import Creator, Edition, Item, ResourceType, Subject, Work


class EditionInline(admin.TabularInline):
    model = Edition
    extra = 0


@admin.register(Work)
class WorkAdmin(admin.ModelAdmin):
    list_display = ("title", "language", "created_at")
    search_fields = ("title", "search_text")
    inlines = [EditionInline]


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("barcode", "edition", "branch", "status")
    list_filter = ("branch", "status")
    search_fields = ("barcode", "edition__work__title")


admin.site.register([Creator, Subject, ResourceType, Edition])
