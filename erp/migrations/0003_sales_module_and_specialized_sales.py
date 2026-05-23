from django.db import migrations, models


def grant_sales_access_to_full_managers(apps, schema_editor):
    BusinessMembership = apps.get_model("erp", "BusinessMembership")

    BusinessMembership.objects.filter(is_owner=True).update(can_access_sales=True)
    BusinessMembership.objects.filter(is_admin=True).update(can_access_sales=True)


class Migration(migrations.Migration):

    dependencies = [
        ("erp", "0002_businessmembership_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessmembership",
            name="can_access_sales",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="customer",
            name="contact_name",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="customer",
            name="tax_id",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="sale",
            name="customer_note",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="sale",
            name="discount_total",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="sale",
            name="internal_note",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="sale",
            name="origin",
            field=models.CharField(choices=[("pos", "Punto de Venta"), ("specialized", "Venta especializada")], default="pos", max_length=20),
        ),
        migrations.AddField(
            model_name="sale",
            name="shipping_address",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="saleitem",
            name="discount_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.RunPython(grant_sales_access_to_full_managers, migrations.RunPython.noop),
    ]
