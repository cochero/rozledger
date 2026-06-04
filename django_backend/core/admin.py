from datetime import timedelta

from django import forms
from django.contrib import admin
from django.utils import timezone

from .models import AffiliateClick, BusinessProfile, Client, Invoice, Lead, PaymentGatewayConfig, PlanSubscription


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "email",
        "phone",
        "business_type",
        "source",
        "ip_address",
        "utm_source",
        "notification_sent",
        "created_at",
    )
    readonly_fields = ("public_token", "phone_digits", "ip_address", "user_agent", "created_at")
    search_fields = (
        "name",
        "email",
        "phone",
        "phone_digits",
        "business_type",
        "source",
        "ip_address",
        "utm_source",
        "utm_campaign",
        "public_token",
    )
    list_filter = ("business_type", "source", "utm_source", "notification_sent", "created_at")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "business_name",
        "business_phone",
        "owner",
        "owner_email",
        "status",
        "client_name",
        "client_phone",
        "amount_before_gst",
        "gst_rate",
        "total_text",
        "created_at",
    )
    readonly_fields = ("public_token", "created_at")
    search_fields = ("business_name", "business_phone", "owner__username", "owner__email", "owner_email", "client_name", "client_phone", "service_name", "public_token")
    list_filter = ("status", "gst_rate", "created_at")


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "owner_email", "email", "phone", "gstin", "created_at")
    search_fields = ("name", "owner__username", "owner__email", "owner_email", "email", "phone", "address", "gstin")
    list_filter = ("created_at",)


@admin.register(BusinessProfile)
class BusinessProfileAdmin(admin.ModelAdmin):
    list_display = ("business_name", "owner", "owner_email", "business_phone", "gstin", "updated_at")
    search_fields = ("business_name", "business_phone", "owner__username", "owner__email", "owner_email", "business_address", "gstin")
    list_filter = ("updated_at", "created_at")


@admin.register(PlanSubscription)
class PlanSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("owner", "owner_email", "plan", "status", "requested_at", "activated_at", "expires_at", "updated_at")
    search_fields = ("owner__username", "owner__email", "owner_email")
    list_filter = ("plan", "status", "requested_at", "activated_at", "expires_at")
    readonly_fields = ("requested_at", "activated_at", "expires_at", "paused_at", "cancelled_at", "updated_at")
    fieldsets = (
        ("Customer", {"fields": ("owner", "owner_email")}),
        ("Plan status", {"fields": ("plan", "status", "requested_at", "activated_at", "expires_at", "paused_at", "cancelled_at")}),
        ("Admin note", {"fields": ("admin_note",)}),
    )
    actions = ("activate_15_day_trial", "approve_pro", "mark_requested", "pause_subscription", "cancel_subscription")

    def save_model(self, request, obj, form, change):
        if obj.plan == "pro" and obj.status == "active" and obj.activated_at is None:
            obj.activated_at = timezone.now()
        super().save_model(request, obj, form, change)

    def notify_pro_active(self, subscription):
        recipient = subscription.owner.email if subscription.owner and subscription.owner.email else subscription.owner_email
        if not recipient:
            return
        expiry_line = f"Your Pro trial is active until {subscription.expires_at:%d %b %Y}." if subscription.expires_at else "Your Pro access is active."
        from django.conf import settings
        from django.core.mail import send_mail

        send_mail(
            "Your RozLedger Pro access is active",
            "\n".join(
                [
                    "Hello,",
                    "",
                    "Your RozLedger Pro access has been activated.",
                    expiry_line,
                    "",
                    "Login here: https://rozledger.in/dashboard/",
                    "",
                    "RozLedger",
                ]
            ),
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
            fail_silently=True,
        )

    @admin.action(description="Approve selected customers for RozLedger Pro")
    def approve_pro(self, request, queryset):
        now = timezone.now()
        updated = queryset.update(plan="pro", status="active", activated_at=now, expires_at=None, paused_at=None, cancelled_at=None)
        for subscription in queryset:
            subscription.activated_at = now
            subscription.expires_at = None
            self.notify_pro_active(subscription)
        self.message_user(request, f"{updated} Pro subscription(s) approved.")

    @admin.action(description="Activate selected customers for 15-day Pro trial")
    def activate_15_day_trial(self, request, queryset):
        now = timezone.now()
        expires_at = now + timedelta(days=15)
        updated = queryset.update(
            plan="pro",
            status="active",
            activated_at=now,
            expires_at=expires_at,
            paused_at=None,
            cancelled_at=None,
        )
        for subscription in queryset:
            subscription.activated_at = now
            subscription.expires_at = expires_at
            self.notify_pro_active(subscription)
        self.message_user(request, f"{updated} Pro subscription(s) activated for 15 days.")

    @admin.action(description="Mark selected customers as Pro requested")
    def mark_requested(self, request, queryset):
        updated = queryset.update(plan="pro", status="requested", requested_at=timezone.now(), expires_at=None, paused_at=None, cancelled_at=None)
        self.message_user(request, f"{updated} subscription(s) marked as requested.")

    @admin.action(description="Pause selected Pro subscriptions")
    def pause_subscription(self, request, queryset):
        updated = queryset.update(status="paused", paused_at=timezone.now(), expires_at=None)
        self.message_user(request, f"{updated} subscription(s) paused.")

    @admin.action(description="Cancel selected subscriptions")
    def cancel_subscription(self, request, queryset):
        updated = queryset.update(status="cancelled", cancelled_at=timezone.now(), expires_at=None)
        self.message_user(request, f"{updated} subscription(s) cancelled.")


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
