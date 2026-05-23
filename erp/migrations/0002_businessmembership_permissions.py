from django.db import migrations, models


def map_legacy_roles_to_permissions(apps, schema_editor):
    BusinessMembership = apps.get_model("erp", "BusinessMembership")

    for membership in BusinessMembership.objects.all():
        membership.is_owner = membership.role == "owner"
        membership.is_admin = membership.role == "admin"
        membership.can_access_pos = membership.role in {"owner", "admin", "cashier"}
        membership.can_access_inventory = membership.role in {"owner", "admin", "inventory"}
        membership.can_access_credits = membership.role in {"owner", "admin", "credit_manager"}
        membership.can_access_cash = membership.role in {"owner", "admin", "cashier"}
        membership.can_access_reports = membership.role in {"owner", "admin"}
        membership.save(
            update_fields=[
                "is_owner",
                "is_admin",
                "can_access_pos",
                "can_access_inventory",
                "can_access_credits",
                "can_access_cash",
                "can_access_reports",
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("erp", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessmembership",
            name="is_owner",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="businessmembership",
            name="is_admin",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="businessmembership",
            name="can_access_pos",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="businessmembership",
            name="can_access_inventory",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="businessmembership",
            name="can_access_credits",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="businessmembership",
            name="can_access_cash",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="businessmembership",
            name="can_access_reports",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(map_legacy_roles_to_permissions, migrations.RunPython.noop),
    ]
