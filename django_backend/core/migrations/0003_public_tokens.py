import secrets

import core.models
from django.db import migrations, models


def add_tokens(apps, schema_editor):
    Lead = apps.get_model("core", "Lead")
    Invoice = apps.get_model("core", "Invoice")
    for model in (Lead, Invoice):
        for item in model.objects.filter(public_token=""):
            item.public_token = secrets.token_urlsafe(18)
            item.save(update_fields=["public_token"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_lead_email_notification_sent"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="public_token",
            field=models.CharField(default="", editable=False, max_length=48),
        ),
        migrations.AddField(
            model_name="lead",
            name="public_token",
            field=models.CharField(default="", editable=False, max_length=48),
        ),
        migrations.RunPython(add_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="invoice",
            name="public_token",
            field=models.CharField(default=core.models.public_token, editable=False, max_length=48, unique=True),
        ),
        migrations.AlterField(
            model_name="lead",
            name="public_token",
            field=models.CharField(default=core.models.public_token, editable=False, max_length=48, unique=True),
        ),
    ]
