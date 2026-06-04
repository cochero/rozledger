from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_invoice_currency_symbol_invoice_tax_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessprofile",
            name="business_phone",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="invoice",
            name="business_phone",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="invoice",
            name="client_phone",
            field=models.CharField(blank=True, max_length=40),
        ),
    ]
