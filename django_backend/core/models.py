import secrets

from django.db import models


def public_token() -> str:
    return secrets.token_urlsafe(18)


class Lead(models.Model):
    public_token = models.CharField(max_length=48, unique=True, default=public_token, editable=False)
    name = models.CharField(max_length=160)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40)
    business_type = models.CharField(max_length=80)
    source = models.CharField(max_length=80, default="website")
    notification_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} - {self.phone}"


class Client(models.Model):
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
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("sent", "Sent"),
        ("paid", "Paid"),
        ("overdue", "Overdue"),
    ]

    public_token = models.CharField(max_length=48, unique=True, default=public_token, editable=False)
    owner_email = models.EmailField(blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="sent", db_index=True)
    business_name = models.CharField(max_length=180)
    client_name = models.CharField(max_length=180)
    service_name = models.CharField(max_length=240)
    amount_before_gst = models.DecimalField(max_digits=12, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    due_days = models.PositiveIntegerField(default=0)
    total_text = models.CharField(max_length=80)
    upi_link = models.TextField()
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

    owner_email = models.EmailField(unique=True)
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default="free")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="free")
    requested_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["owner_email"]

    def __str__(self) -> str:
        return f"{self.owner_email} - {self.plan} ({self.status})"
