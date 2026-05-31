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


class Lead(models.Model):
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
    gstin = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = ("owner_email", "name")

    def __str__(self) -> str:
        return self.name


class Invoice(models.Model):
    TEMPLATE_CHOICES = [
        ("classic", "Classic Ledger"),
        ("executive", "Executive Black"),
        ("modern", "Modern Accent"),
        ("minimal", "Minimal Clean"),
        ("service", "Service Pro"),
    ]

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("sent", "Sent"),
        ("paid", "Paid"),
        ("overdue", "Overdue"),
    ]

    public_token = models.CharField(max_length=48, unique=True, default=public_token, editable=False)
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
    business_address = models.TextField(blank=True)
    client_name = models.CharField(max_length=180)
    client_address = models.TextField(blank=True)
    client_gstin = models.CharField(max_length=20, blank=True)
    service_name = models.CharField(max_length=240)
    include_gst = models.BooleanField(default=True)
    amount_before_gst = models.DecimalField(max_digits=12, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
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


class AffiliateClick(models.Model):
    offer_name = models.CharField(max_length=160)
    destination_url = models.URLField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.offer_name


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

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    owner_email = models.EmailField(unique=True)
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

    def __str__(self) -> str:
        return f"{self.owner_email} - {self.plan} ({self.status})"

    @property
    def is_pro_active(self) -> bool:
        if self.plan != "pro" or self.status != "active":
            return False
        return self.expires_at is None or self.expires_at > timezone.now()


class PaymentGatewayConfig(models.Model):
    GATEWAY_CHOICES = [
        ("razorpay", "Razorpay"),
    ]

    MODE_CHOICES = [
        ("test", "Test"),
        ("live", "Live"),
    ]

    gateway = models.CharField(max_length=30, choices=GATEWAY_CHOICES, unique=True, default="razorpay")
    enabled = models.BooleanField(default=False)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default="test")
    encrypted_key_id = models.TextField(blank=True)
    encrypted_key_secret = models.TextField(blank=True)
    encrypted_webhook_secret = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["gateway"]

    def __str__(self) -> str:
        status = "enabled" if self.enabled else "disabled"
        return f"{self.get_gateway_display()} {self.get_mode_display()} ({status})"

    @classmethod
    def razorpay(cls) -> "PaymentGatewayConfig | None":
        return cls.objects.filter(gateway="razorpay").first()

    @classmethod
    def active_razorpay(cls) -> "PaymentGatewayConfig | None":
        config = cls.razorpay()
        if config and config.enabled and config.is_configured:
            return config
        return None

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
