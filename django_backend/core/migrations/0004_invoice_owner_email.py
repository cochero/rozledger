from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_public_tokens"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="owner_email",
            field=models.EmailField(blank=True, db_index=True, max_length=254),
        ),
    ]
