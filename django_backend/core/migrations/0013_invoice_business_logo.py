from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_invoice_address_payment_note_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="business_logo",
            field=models.FileField(blank=True, upload_to="invoice_logos/%Y/%m/"),
        ),
    ]
