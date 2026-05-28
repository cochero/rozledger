from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_clients_invoice_status_subscription"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentGatewayConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("gateway", models.CharField(choices=[("razorpay", "Razorpay")], default="razorpay", max_length=30, unique=True)),
                ("enabled", models.BooleanField(default=False)),
                ("mode", models.CharField(choices=[("test", "Test"), ("live", "Live")], default="test", max_length=10)),
                ("encrypted_key_id", models.TextField(blank=True)),
                ("encrypted_key_secret", models.TextField(blank=True)),
                ("encrypted_webhook_secret", models.TextField(blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["gateway"],
            },
        ),
    ]
