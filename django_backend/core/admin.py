from django.contrib import admin

from .models import AffiliateClick, Invoice, Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "business_type", "source", "created_at")
    search_fields = ("name", "phone", "business_type")
    list_filter = ("business_type", "source", "created_at")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "business_name",
        "client_name",
        "amount_before_gst",
        "gst_rate",
        "total_text",
        "created_at",
    )
    search_fields = ("business_name", "client_name", "service_name")
    list_filter = ("gst_rate", "created_at")


@admin.register(AffiliateClick)
class AffiliateClickAdmin(admin.ModelAdmin):
    list_display = ("offer_name", "destination_url", "created_at")
    search_fields = ("offer_name", "destination_url")
    list_filter = ("created_at",)
