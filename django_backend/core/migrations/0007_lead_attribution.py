from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_payment_gateway_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="landing_path",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name="lead",
            name="referrer",
            field=models.URLField(blank=True, max_length=1000),
        ),
        migrations.AddField(
            model_name="lead",
            name="utm_campaign",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="lead",
            name="utm_medium",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="lead",
            name="utm_source",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
