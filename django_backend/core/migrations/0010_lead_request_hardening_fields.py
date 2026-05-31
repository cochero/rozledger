import re

from django.db import migrations, models


def backfill_phone_digits(apps, schema_editor):
    Lead = apps.get_model("core", "Lead")
    for lead in Lead.objects.filter(phone_digits="").exclude(phone="").iterator():
        lead.phone_digits = re.sub(r"\D", "", lead.phone or "")[:20]
        lead.save(update_fields=["phone_digits"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_user_ownership_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="phone_digits",
            field=models.CharField(blank=True, db_index=True, max_length=20),
        ),
        migrations.AddField(
            model_name="lead",
            name="ip_address",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lead",
            name="user_agent",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.RunPython(backfill_phone_digits, migrations.RunPython.noop),
    ]
