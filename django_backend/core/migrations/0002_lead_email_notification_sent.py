from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="lead",
            name="notification_sent",
            field=models.BooleanField(default=False),
        ),
    ]
