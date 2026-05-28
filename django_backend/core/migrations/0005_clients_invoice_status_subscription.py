from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_invoice_owner_email"),
    ]

    operations = [
        migrations.CreateModel(
            name="Client",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("owner_email", models.EmailField(db_index=True, max_length=254)),
                ("name", models.CharField(max_length=180)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=40)),
                ("gstin", models.CharField(blank=True, max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["name"],
                "unique_together": {("owner_email", "name")},
            },
        ),
        migrations.CreateModel(
            name="PlanSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("owner_email", models.EmailField(max_length=254, unique=True)),
                ("plan", models.CharField(choices=[("free", "Free"), ("pro", "Pro"), ("business", "Business")], default="free", max_length=20)),
                ("status", models.CharField(choices=[("free", "Free"), ("requested", "Requested"), ("active", "Active"), ("paused", "Paused"), ("cancelled", "Cancelled")], default="free", max_length=20)),
                ("requested_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["owner_email"],
            },
        ),
        migrations.AddField(
            model_name="invoice",
            name="status",
            field=models.CharField(choices=[("draft", "Draft"), ("sent", "Sent"), ("paid", "Paid"), ("overdue", "Overdue")], db_index=True, default="sent", max_length=20),
        ),
        migrations.AddField(
            model_name="invoice",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
