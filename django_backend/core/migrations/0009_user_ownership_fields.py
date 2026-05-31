from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_owner_fields(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Client = apps.get_model("core", "Client")
    Invoice = apps.get_model("core", "Invoice")
    PlanSubscription = apps.get_model("core", "PlanSubscription")

    users_by_email = {
        (user.email or user.username).lower(): user
        for user in User.objects.all().only("id", "email", "username")
    }

    for model in (Client, Invoice, PlanSubscription):
        for obj in model.objects.filter(owner__isnull=True).exclude(owner_email="").iterator():
            user = users_by_email.get(obj.owner_email.lower())
            if user:
                obj.owner = user
                obj.save(update_fields=["owner"])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0008_subscription_approval_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="clients",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="invoices",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="plansubscription",
            name="owner",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="subscription",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(backfill_owner_fields, migrations.RunPython.noop),
    ]
