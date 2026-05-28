from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_lead_attribution"),
    ]

    operations = [
        migrations.AddField(
            model_name="plansubscription",
            name="activated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="plansubscription",
            name="admin_note",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="plansubscription",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="plansubscription",
            name="paused_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
