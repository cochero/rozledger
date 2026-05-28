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


class Invoice(models.Model):
    public_token = models.CharField(max_length=48, unique=True, default=public_token, editable=False)
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
