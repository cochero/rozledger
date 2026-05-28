from django import forms
from django.contrib import admin

from .models import AffiliateClick, Client, Invoice, Lead, PaymentGatewayConfig, PlanSubscription


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "business_type", "source", "notification_sent", "created_at")
    readonly_fields = ("public_token", "created_at")
    search_fields = ("name", "email", "phone", "business_type", "public_token")
    list_filter = ("business_type", "source", "notification_sent", "created_at")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "business_name",
        "owner_email",
        "status",
        "client_name",
        "amount_before_gst",
        "gst_rate",
        "total_text",
        "created_at",
    )
    readonly_fields = ("public_token", "created_at")
    search_fields = ("business_name", "owner_email", "client_name", "service_name", "public_token")
    list_filter = ("status", "gst_rate", "created_at")


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "owner_email", "email", "phone", "gstin", "created_at")
    search_fields = ("name", "owner_email", "email", "phone", "gstin")
    list_filter = ("created_at",)


@admin.register(PlanSubscription)
class PlanSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("owner_email", "plan", "status", "requested_at", "updated_at")
    search_fields = ("owner_email",)
    list_filter = ("plan", "status", "requested_at")


class PaymentGatewayConfigForm(forms.ModelForm):
    razorpay_key_id = forms.CharField(
        label="Razorpay Key ID",
        required=False,
        help_text="Paste to add or rotate. Leave blank to keep the currently encrypted value.",
    )
    razorpay_key_secret = forms.CharField(
        label="Razorpay Key Secret",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Encrypted before saving. Leave blank to keep the current secret.",
    )
    razorpay_webhook_secret = forms.CharField(
        label="Razorpay Webhook Secret",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Optional now, required before webhook-based auto activation.",
    )

    class Meta:
        model = PaymentGatewayConfig
        fields = ("gateway", "enabled", "mode", "razorpay_key_id", "razorpay_key_secret", "razorpay_webhook_secret")

    def clean(self):
        cleaned = super().clean()
        enabled = cleaned.get("enabled")
        instance = self.instance
        has_key_id = bool(cleaned.get("razorpay_key_id") or getattr(instance, "key_id", ""))
        has_key_secret = bool(cleaned.get("razorpay_key_secret") or getattr(instance, "key_secret", ""))
        if enabled and not (has_key_id and has_key_secret):
            raise forms.ValidationError("Razorpay Key ID and Key Secret are required before enabling the gateway.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        key_id = self.cleaned_data.get("razorpay_key_id")
        key_secret = self.cleaned_data.get("razorpay_key_secret")
        webhook_secret = self.cleaned_data.get("razorpay_webhook_secret")
        if key_id:
            instance.key_id = key_id
        if key_secret:
            instance.key_secret = key_secret
        if webhook_secret:
            instance.webhook_secret = webhook_secret
        if commit:
            instance.save()
        return instance


@admin.register(PaymentGatewayConfig)
class PaymentGatewayConfigAdmin(admin.ModelAdmin):
    form = PaymentGatewayConfigForm
    list_display = ("gateway", "enabled", "mode", "configured", "masked_key_id_display", "updated_at")
    list_filter = ("enabled", "mode", "updated_at")
    readonly_fields = (
        "configured",
        "masked_key_id_display",
        "masked_key_secret_display",
        "masked_webhook_secret_display",
        "updated_at",
    )
    fieldsets = (
        ("Gateway", {"fields": ("gateway", "enabled", "mode", "configured", "updated_at")}),
        (
            "Encrypted Razorpay credentials",
            {
                "fields": (
                    "masked_key_id_display",
                    "masked_key_secret_display",
                    "masked_webhook_secret_display",
                    "razorpay_key_id",
                    "razorpay_key_secret",
                    "razorpay_webhook_secret",
                )
            },
        ),
    )

    @admin.display(boolean=True, description="Configured")
    def configured(self, obj):
        return obj.is_configured

    @admin.display(description="Stored Key ID")
    def masked_key_id_display(self, obj):
        return obj.masked_key_id

    @admin.display(description="Stored Key Secret")
    def masked_key_secret_display(self, obj):
        return obj.masked_key_secret

    @admin.display(description="Stored Webhook Secret")
    def masked_webhook_secret_display(self, obj):
        return obj.masked_webhook_secret


@admin.register(AffiliateClick)
class AffiliateClickAdmin(admin.ModelAdmin):
    list_display = ("offer_name", "destination_url", "created_at")
    search_fields = ("offer_name", "destination_url")
    list_filter = ("created_at",)
