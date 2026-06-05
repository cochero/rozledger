import secrets
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models
from django.utils import timezone


def public_token() -> str:
    return secrets.token_urlsafe(18)


def secret_cipher() -> Fernet:
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return secret_cipher().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return secret_cipher().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


def mask_secret(value: str) -> str:
    if not value:
        return "Not set"
    if len(value) <= 8:
        return "Configured"
    return f"{value[:4]}...{value[-4:]}"


INVOICE_TEMPLATE_CHOICES = [
    ("classic", "Classic Ledger"),
    ("executive", "Executive Black"),
    ("modern", "Modern Accent"),
    ("minimal", "Minimal Clean"),
    ("service", "Service Pro"),
]

INVOICE_STATUS_CHOICES = [
    ("draft", "Draft"),
    ("sent", "Sent"),
    ("paid", "Paid"),
    ("overdue", "Overdue"),
]

MARKET_CHOICES = [
    ("IN", "India"),
    ("US", "United States"),
]

ACCOUNT_TYPE_CHOICES = [
    ("asset", "Asset"),
    ("liability", "Liability"),
    ("equity", "Equity"),
    ("revenue", "Revenue"),
    ("expense", "Expense"),
]

NORMAL_BALANCE_CHOICES = [
    ("debit", "Debit"),
    ("credit", "Credit"),
]

PAYMENT_METHOD_CHOICES = [
    ("bank", "Bank transfer"),
    ("cash", "Cash"),
    ("upi", "UPI"),
    ("card", "Card"),
    ("check", "Check"),
    ("paypal", "PayPal"),
    ("stripe", "Stripe"),
    ("other", "Other"),
]

BILL_STATUS_CHOICES = [
    ("unpaid", "Unpaid"),
    ("paid", "Paid"),
]


class Lead(models.Model):
    market = models.CharField(max_length=2, choices=MARKET_CHOICES, default="IN", db_index=True)
    public_token = models.CharField(max_length=48, unique=True, default=public_token, editable=False)
    name = models.CharField(max_length=160)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40)
    phone_digits = models.CharField(max_length=20, blank=True, db_index=True)
    business_type = models.CharField(max_length=80)
    source = models.CharField(max_length=80, default="website")
    landing_path = models.CharField(max_length=300, blank=True)
    referrer = models.URLField(max_length=1000, blank=True)
    utm_source = models.CharField(max_length=120, blank=True)
    utm_medium = models.CharField(max_length=120, blank=True)
    utm_campaign = models.CharField(max_length=160, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    notification_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} - {self.phone}"


class Client(models.Model):
    market = models.CharField(max_length=2, choices=MARKET_CHOICES, default="IN", db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="clients",
    )
    owner_email = models.EmailField(db_index=True)
    name = models.CharField(max_length=180)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = ("market", "owner_email", "name")

    def __str__(self) -> str:
        return self.name


class BusinessProfile(models.Model):
    market = models.CharField(max_length=2, choices=MARKET_CHOICES, default="IN", db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="business_profiles",
    )
    owner_email = models.EmailField(db_index=True)
    business_name = models.CharField(max_length=180)
    business_logo = models.FileField(upload_to="business_logos/%Y/%m/", blank=True)
    business_phone = models.CharField(max_length=40, blank=True)
    business_address = models.TextField(blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    upi_link = models.TextField(blank=True)
    bank_details = models.TextField(blank=True)
    thank_you_note = models.TextField(blank=True)
    template = models.CharField(max_length=20, choices=INVOICE_TEMPLATE_CHOICES, default="classic")
    accent_color = models.CharField(max_length=7, default="#126b4f")
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["business_name"]
        unique_together = ("market", "owner_email")

    def __str__(self) -> str:
        return self.business_name


class Invoice(models.Model):
    TEMPLATE_CHOICES = INVOICE_TEMPLATE_CHOICES
    STATUS_CHOICES = INVOICE_STATUS_CHOICES

    public_token = models.CharField(max_length=48, unique=True, default=public_token, editable=False)
    market = models.CharField(max_length=2, choices=MARKET_CHOICES, default="IN", db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    owner_email = models.EmailField(blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="sent", db_index=True)
    template = models.CharField(max_length=20, choices=TEMPLATE_CHOICES, default="classic")
    accent_color = models.CharField(max_length=7, default="#126b4f")
    business_name = models.CharField(max_length=180)
    business_logo = models.FileField(upload_to="invoice_logos/%Y/%m/", blank=True)
    business_phone = models.CharField(max_length=40, blank=True)
    business_address = models.TextField(blank=True)
    client_name = models.CharField(max_length=180)
    client_phone = models.CharField(max_length=40, blank=True)
    client_address = models.TextField(blank=True)
    client_gstin = models.CharField(max_length=20, blank=True)
    service_name = models.CharField(max_length=240)
    include_gst = models.BooleanField(default=True)
    amount_before_gst = models.DecimalField(max_digits=12, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    tax_label = models.CharField(max_length=40, default="GST")
    currency_symbol = models.CharField(max_length=8, default="\u20b9")
    due_days = models.PositiveIntegerField(default=0)
    total_text = models.CharField(max_length=80)
    upi_link = models.TextField()
    bank_details = models.TextField(blank=True)
    thank_you_note = models.TextField(blank=True)
    invoice_text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.business_name} to {self.client_name}"


class InvoiceLineItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="line_items")
    description = models.CharField(max_length=240)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    @property
    def amount(self):
        return self.quantity * self.rate

    def __str__(self) -> str:
        return f"{self.description} x {self.quantity}"


class AffiliateClick(models.Model):
    offer_name = models.CharField(max_length=160)
    destination_url = models.URLField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.offer_name


class Account(models.Model):
    market = models.CharField(max_length=2, choices=MARKET_CHOICES, default="IN", db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="accounts",
    )
    owner_email = models.EmailField(db_index=True)
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=180)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES)
    normal_balance = models.CharField(max_length=10, choices=NORMAL_BALANCE_CHOICES)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]
        unique_together = ("market", "owner_email", "code")

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class JournalEntry(models.Model):
    market = models.CharField(max_length=2, choices=MARKET_CHOICES, default="IN", db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="journal_entries",
    )
    owner_email = models.EmailField(db_index=True)
    entry_date = models.DateField(default=timezone.localdate)
    memo = models.CharField(max_length=240)
    source = models.CharField(max_length=40, default="manual")
    total_debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_posted = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-entry_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.entry_date} - {self.memo}"

    @property
    def is_balanced(self) -> bool:
        return self.total_debit == self.total_credit


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="journal_lines")
    description = models.CharField(max_length=240, blank=True)
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        amount = self.debit if self.debit else self.credit
        side = "Dr" if self.debit else "Cr"
        return f"{self.account.code} {side} {amount}"


class PaymentReceipt(models.Model):
    market = models.CharField(max_length=2, choices=MARKET_CHOICES, default="IN", db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="payment_receipts",
    )
    owner_email = models.EmailField(db_index=True)
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="payment_receipts")
    payment_date = models.DateField(default=timezone.localdate)
    payer_name = models.CharField(max_length=180)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default="bank")
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.payer_name} - {self.amount}"


class VendorBill(models.Model):
    market = models.CharField(max_length=2, choices=MARKET_CHOICES, default="IN", db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="vendor_bills",
    )
    owner_email = models.EmailField(db_index=True)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="vendor_bills")
    bill_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    vendor_name = models.CharField(max_length=180)
    category = models.CharField(max_length=180, default="Office expenses")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=BILL_STATUS_CHOICES, default="unpaid", db_index=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default="bank")
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-bill_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.vendor_name} - {self.amount}"


class AuditLog(models.Model):
    market = models.CharField(max_length=2, choices=MARKET_CHOICES, default="IN", db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    owner_email = models.EmailField(db_index=True)
    action = models.CharField(max_length=80, db_index=True)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80, blank=True)
    summary = models.CharField(max_length=240)
    ip_address = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.action} - {self.summary}"


class PlanSubscription(models.Model):
    PLAN_CHOICES = [
        ("free", "Free"),
        ("pro", "Pro"),
        ("business", "Business"),
    ]

    STATUS_CHOICES = [
        ("free", "Free"),
        ("requested", "Requested"),
        ("active", "Active"),
        ("paused", "Paused"),
        ("cancelled", "Cancelled"),
    ]

    market = models.CharField(max_length=2, choices=MARKET_CHOICES, default="IN", db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    owner_email = models.EmailField(db_index=True)
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default="free")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="free")
    requested_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    admin_note = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["owner_email"]
        unique_together = ("market", "owner_email")

    def __str__(self) -> str:
        return f"{self.owner_email} - {self.market} - {self.plan} ({self.status})"

    @property
    def is_pro_active(self) -> bool:
        if self.plan != "pro" or self.status != "active":
            return False
        return self.expires_at is None or self.expires_at > timezone.now()


class PaymentGatewayConfig(models.Model):
    GATEWAY_CHOICES = [
        ("razorpay", "Razorpay"),
        ("stripe", "Stripe"),
        ("paypal", "PayPal"),
    ]

    MODE_CHOICES = [
        ("test", "Test"),
        ("live", "Live"),
    ]

    market = models.CharField(max_length=2, choices=MARKET_CHOICES, default="IN", db_index=True)
    gateway = models.CharField(max_length=30, choices=GATEWAY_CHOICES, default="razorpay")
    enabled = models.BooleanField(default=False)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default="test")
    encrypted_key_id = models.TextField(blank=True)
    encrypted_key_secret = models.TextField(blank=True)
    encrypted_webhook_secret = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["market", "gateway"]
        unique_together = ("market", "gateway")

    def __str__(self) -> str:
        status = "enabled" if self.enabled else "disabled"
        return f"{self.get_gateway_display()} {self.get_mode_display()} ({status})"

    @classmethod
    def for_gateway(cls, gateway: str, market: str) -> "PaymentGatewayConfig | None":
        return cls.objects.filter(gateway=gateway, market=market).first()

    @classmethod
    def active_gateway(cls, gateway: str, market: str) -> "PaymentGatewayConfig | None":
        config = cls.for_gateway(gateway, market)
        if config and config.enabled and config.is_configured:
            return config
        return None

    @classmethod
    def razorpay(cls) -> "PaymentGatewayConfig | None":
        return cls.for_gateway("razorpay", "IN")

    @classmethod
    def active_razorpay(cls) -> "PaymentGatewayConfig | None":
        return cls.active_gateway("razorpay", "IN")

    @property
    def key_id(self) -> str:
        return decrypt_secret(self.encrypted_key_id)

    @key_id.setter
    def key_id(self, value: str) -> None:
        self.encrypted_key_id = encrypt_secret(value)

    @property
    def key_secret(self) -> str:
        return decrypt_secret(self.encrypted_key_secret)

    @key_secret.setter
    def key_secret(self, value: str) -> None:
        self.encrypted_key_secret = encrypt_secret(value)

    @property
    def webhook_secret(self) -> str:
        return decrypt_secret(self.encrypted_webhook_secret)

    @webhook_secret.setter
    def webhook_secret(self, value: str) -> None:
        self.encrypted_webhook_secret = encrypt_secret(value)

    @property
    def is_configured(self) -> bool:
        return bool(self.key_id and self.key_secret)

    @property
    def masked_key_id(self) -> str:
        return mask_secret(self.key_id)

    @property
    def masked_key_secret(self) -> str:
        return mask_secret(self.key_secret)

    @property
    def masked_webhook_secret(self) -> str:
        return mask_secret(self.webhook_secret)
