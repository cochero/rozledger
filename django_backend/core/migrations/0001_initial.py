from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="AffiliateClick",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("offer_name", models.CharField(max_length=160)),
                ("destination_url", models.URLField(blank=True, max_length=1000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="Invoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("business_name", models.CharField(max_length=180)),
                ("client_name", models.CharField(max_length=180)),
                ("service_name", models.CharField(max_length=240)),
                ("amount_before_gst", models.DecimalField(decimal_places=2, max_digits=12)),
                ("gst_rate", models.DecimalField(decimal_places=2, max_digits=5)),
                ("due_days", models.PositiveIntegerField(default=0)),
                ("total_text", models.CharField(max_length=80)),
                ("upi_link", models.TextField()),
                ("invoice_text", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="Lead",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=160)),
                ("phone", models.CharField(max_length=40)),
                ("business_type", models.CharField(max_length=80)),
                ("source", models.CharField(default="website", max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
