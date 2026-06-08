from datetime import timedelta

from django import forms
from django.contrib import admin
from django.utils import timezone

from .models import Account, AffiliateClick, AuditLog, BusinessProfile, Client, CustomerCreditNote, ExpenseUploadDraft, Godown, InventoryItem, Invoice, InvoiceLineItem, JournalEntry, JournalLine, Lead, PaymentEvent, PaymentGatewayConfig, PaymentReceipt, PaymentReversal, PlanSubscription, ReconciliationLine, ReconciliationSession, StockCostLayer, StockGroup, StockLayerConsumption, StockMovement, UnitOfMeasure, VendorBill, VendorBillPayment, VendorDebitNote, Voucher, VoucherInventoryLine, VoucherLedgerLine


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "market",
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
    list_filter = ("market", "business_type", "source", "utm_source", "notification_sent", "created_at")


class InvoiceLineItemInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 0
    fields = ("description", "quantity", "rate")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "market",
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
        "sales_voucher",
        "created_at",
    )
    readonly_fields = ("public_token", "sales_voucher", "created_at")
    search_fields = ("business_name", "business_phone", "owner__username", "owner__email", "owner_email", "client_name", "client_phone", "service_name", "public_token", "sales_voucher__voucher_number")
    list_filter = ("market", "status", "gst_rate", "created_at")
    inlines = (InvoiceLineItemInline,)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("market", "name", "owner", "owner_email", "email", "phone", "gstin", "created_at")
    search_fields = ("name", "owner__username", "owner__email", "owner_email", "email", "phone", "address", "gstin")
    list_filter = ("market", "created_at")


@admin.register(BusinessProfile)
class BusinessProfileAdmin(admin.ModelAdmin):
    list_display = ("market", "business_name", "business_type", "owner", "owner_email", "business_phone", "gstin", "updated_at")
    search_fields = ("business_name", "business_phone", "owner__username", "owner__email", "owner_email", "business_address", "gstin")
    list_filter = ("market", "business_type", "updated_at", "created_at")


@admin.register(PlanSubscription)
class PlanSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("market", "owner", "owner_email", "plan", "status", "provider", "requested_at", "activated_at", "expires_at", "updated_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "razorpay_subscription_id")
    list_filter = ("market", "plan", "status", "provider", "requested_at", "activated_at", "expires_at")
    readonly_fields = ("requested_at", "activated_at", "expires_at", "paused_at", "cancelled_at", "razorpay_subscription_id", "razorpay_customer_id", "last_payment_id", "updated_at")
    fieldsets = (
        ("Customer", {"fields": ("market", "owner", "owner_email")}),
        ("Plan status", {"fields": ("plan", "status", "requested_at", "activated_at", "expires_at", "paused_at", "cancelled_at")}),
        ("Razorpay subscription", {"fields": ("provider", "razorpay_subscription_id", "razorpay_customer_id", "last_payment_id")}),
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


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "code", "name", "account_type", "normal_balance", "is_active", "created_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "code", "name")
    list_filter = ("market", "account_type", "normal_balance", "is_active", "created_at")


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "name", "symbol", "created_at")
    search_fields = ("owner_email", "name", "symbol")
    list_filter = ("market", "created_at")


@admin.register(Godown)
class GodownAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "name", "is_active", "created_at")
    search_fields = ("owner_email", "name", "address")
    list_filter = ("market", "is_active", "created_at")


@admin.register(StockGroup)
class StockGroupAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "name", "parent", "created_at")
    search_fields = ("owner_email", "name", "parent__name")
    list_filter = ("market", "created_at")


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    fields = ("account", "description", "debit", "credit")


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "entry_date", "memo", "source", "total_debit", "total_credit", "is_posted", "created_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "memo", "source", "lines__account__code", "lines__account__name")
    list_filter = ("market", "source", "is_posted", "entry_date", "created_at")
    readonly_fields = ("total_debit", "total_credit", "created_at")
    inlines = (JournalLineInline,)


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "payment_date", "payer_name", "amount", "method", "invoice", "voucher", "created_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "payer_name", "reference", "notes", "invoice__client_name", "voucher__voucher_number")
    list_filter = ("market", "method", "payment_date", "created_at")
    readonly_fields = ("journal_entry", "voucher", "created_at")


@admin.register(CustomerCreditNote)
class CustomerCreditNoteAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "credit_date", "credit_note_number", "client_name", "invoice", "total_amount", "voucher", "created_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "credit_note_number", "client_name", "reason", "notes", "invoice__client_name", "voucher__voucher_number")
    list_filter = ("market", "credit_date", "created_at")
    readonly_fields = ("voucher", "journal_entry", "created_at")


@admin.register(VendorBill)
class VendorBillAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "bill_date", "vendor_name", "category", "amount", "status", "due_date", "paid_date", "voucher", "payment_voucher", "created_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "vendor_name", "category", "reference", "payment_reference", "notes", "voucher__voucher_number", "payment_voucher__voucher_number")
    list_filter = ("market", "status", "bill_date", "due_date", "paid_date", "created_at")
    readonly_fields = ("journal_entry", "voucher", "payment_voucher", "created_at")


@admin.register(VendorBillPayment)
class VendorBillPaymentAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "payment_date", "vendor_name", "amount", "method", "bill", "voucher", "created_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "vendor_name", "reference", "notes", "bill__vendor_name", "bill__reference", "voucher__voucher_number")
    list_filter = ("market", "method", "payment_date", "created_at")
    readonly_fields = ("voucher", "created_at")


@admin.register(VendorDebitNote)
class VendorDebitNoteAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "debit_date", "debit_note_number", "vendor_name", "bill", "amount", "voucher", "created_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "debit_note_number", "vendor_name", "reason", "notes", "bill__vendor_name", "voucher__voucher_number")
    list_filter = ("market", "debit_date", "created_at")
    readonly_fields = ("voucher", "journal_entry", "created_at")


@admin.register(PaymentReversal)
class PaymentReversalAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "reversal_date", "reversal_number", "reversal_type", "party_name", "amount", "voucher", "created_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "reversal_number", "party_name", "reason", "notes", "voucher__voucher_number")
    list_filter = ("market", "reversal_type", "reversal_date", "created_at")
    readonly_fields = ("voucher", "journal_entry", "created_at")


@admin.register(ExpenseUploadDraft)
class ExpenseUploadDraftAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "vendor_name", "amount", "bill_status", "status", "original_filename", "vendor_bill", "created_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "vendor_name", "category", "reference", "original_filename", "extracted_text")
    list_filter = ("market", "status", "bill_status", "payment_method", "created_at")
    readonly_fields = ("public_token", "vendor_bill", "created_at")


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "sku", "name", "stock_group", "category", "item_type", "unit", "sales_rate", "purchase_rate", "reorder_level", "track_inventory", "is_active")
    search_fields = ("owner__username", "owner__email", "owner_email", "sku", "name", "category", "stock_group__name")
    list_filter = ("market", "stock_group", "item_type", "track_inventory", "is_active", "created_at")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "item", "godown", "movement_type", "movement_date", "quantity", "unit_cost", "reference", "created_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "item__name", "item__sku", "godown__name", "reference", "notes")
    list_filter = ("market", "godown", "movement_type", "movement_date", "created_at")


class VoucherLedgerLineInline(admin.TabularInline):
    model = VoucherLedgerLine
    extra = 0
    fields = ("account", "description", "debit", "credit")


class VoucherInventoryLineInline(admin.TabularInline):
    model = VoucherInventoryLine
    extra = 0
    fields = ("item", "godown", "description", "quantity", "rate", "amount", "stock_movement")
    readonly_fields = ("stock_movement",)


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "voucher_type", "voucher_number", "voucher_date", "party_name", "total_amount", "journal_entry", "created_at")
    search_fields = ("owner_email", "voucher_number", "party_name", "narration", "ledger_lines__account__name", "inventory_lines__item__name")
    list_filter = ("market", "voucher_type", "voucher_date", "created_at")
    readonly_fields = ("journal_entry", "created_at")
    inlines = (VoucherLedgerLineInline, VoucherInventoryLineInline)


@admin.register(StockCostLayer)
class StockCostLayerAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "item", "godown", "layer_date", "original_quantity", "remaining_quantity", "unit_cost", "source_movement")
    search_fields = ("owner_email", "item__name", "item__sku", "godown__name")
    list_filter = ("market", "godown", "layer_date", "created_at")


@admin.register(StockLayerConsumption)
class StockLayerConsumptionAdmin(admin.ModelAdmin):
    list_display = ("sale_line", "layer", "quantity", "unit_cost", "amount", "created_at")
    search_fields = ("sale_line__item__name", "layer__item__name")
    list_filter = ("created_at",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "action", "object_type", "object_id", "summary", "ip_address", "created_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "action", "object_type", "object_id", "summary", "ip_address")
    list_filter = ("market", "action", "object_type", "created_at")
    readonly_fields = ("market", "owner", "owner_email", "action", "object_type", "object_id", "summary", "ip_address", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class ReconciliationLineInline(admin.TabularInline):
    model = ReconciliationLine
    extra = 0
    fields = ("journal_line", "amount", "created_at")
    readonly_fields = ("created_at",)


@admin.register(ReconciliationSession)
class ReconciliationSessionAdmin(admin.ModelAdmin):
    list_display = ("market", "owner_email", "statement_date", "account", "statement_balance", "ledger_balance", "difference", "created_at")
    search_fields = ("owner__username", "owner__email", "owner_email", "account__code", "account__name", "notes")
    list_filter = ("market", "account", "statement_date", "created_at")
    readonly_fields = ("ledger_balance", "difference", "created_at")
    inlines = (ReconciliationLineInline,)


class PaymentGatewayConfigForm(forms.ModelForm):
    razorpay_key_id = forms.CharField(
        label="Gateway Key ID / Client ID",
        required=False,
        help_text="Paste to add or rotate. Leave blank to keep the currently encrypted value.",
    )
    razorpay_key_secret = forms.CharField(
        label="Gateway Key Secret",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Encrypted before saving. Leave blank to keep the current secret.",
    )
    razorpay_webhook_secret = forms.CharField(
        label="Gateway Webhook Secret",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Optional now, required before webhook-based auto activation.",
    )

    class Meta:
        model = PaymentGatewayConfig
        fields = ("market", "gateway", "enabled", "mode", "subscription_amount", "razorpay_plan_id", "razorpay_key_id", "razorpay_key_secret", "razorpay_webhook_secret")

    def clean(self):
        cleaned = super().clean()
        enabled = cleaned.get("enabled")
        instance = self.instance
        has_key_id = bool(cleaned.get("razorpay_key_id") or getattr(instance, "key_id", ""))
        has_key_secret = bool(cleaned.get("razorpay_key_secret") or getattr(instance, "key_secret", ""))
        if enabled and not (has_key_id and has_key_secret):
            raise forms.ValidationError("Gateway Key ID / Client ID and Key Secret are required before enabling the gateway.")
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
    list_display = ("market", "gateway", "enabled", "mode", "configured", "masked_key_id_display", "updated_at")
    list_filter = ("market", "gateway", "enabled", "mode", "updated_at")
    readonly_fields = (
        "configured",
        "masked_key_id_display",
        "masked_key_secret_display",
        "masked_webhook_secret_display",
        "updated_at",
    )
    fieldsets = (
        ("Gateway", {"fields": ("market", "gateway", "enabled", "mode", "configured", "updated_at")}),
        (
            "Recurring subscription",
            {
                "fields": ("subscription_amount", "razorpay_plan_id"),
                "description": "Amount is in the smallest currency unit (paise for INR). Leave plan id blank to auto-create on the first subscription; clear it to force a new plan.",
            },
        ),
        (
            "Encrypted gateway credentials",
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


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = ("provider", "event_type", "reference_id", "event_id", "created_at")
    search_fields = ("event_id", "reference_id", "event_type")
    list_filter = ("provider", "event_type", "created_at")
    readonly_fields = ("provider", "event_id", "event_type", "reference_id", "summary", "created_at")

    def has_add_permission(self, request):
        return False
