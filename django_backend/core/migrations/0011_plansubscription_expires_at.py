from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_lead_request_hardening_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="plansubscription",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
