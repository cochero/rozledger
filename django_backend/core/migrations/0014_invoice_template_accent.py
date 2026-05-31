from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_invoice_business_logo"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="template",
            field=models.CharField(
                choices=[
                    ("classic", "Classic Ledger"),
                    ("executive", "Executive Black"),
                    ("modern", "Modern Accent"),
                    ("minimal", "Minimal Clean"),
                    ("service", "Service Pro"),
                ],
                default="classic",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="accent_color",
            field=models.CharField(default="#126b4f", max_length=7),
        ),
    ]
