from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_plansubscription_expires_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="business_address",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="client_address",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="client_gstin",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="invoice",
            name="include_gst",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="bank_details",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="thank_you_note",
            field=models.TextField(blank=True),
        ),
    ]
