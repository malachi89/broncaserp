from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Business,
    BusinessMembership,
    CreditAccount,
    Customer,
    InventoryMovement,
    Plan,
    Product,
    Sale,
    SalePayment,
)
from .services import create_pos_sale


User = get_user_model()


class ErpDomainTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(name="Basico", monthly_price=399)
        self.business = Business.objects.create(
            name="Abarrotes Lupita",
            slug="lupita",
            status=Business.Status.ACTIVE,
            service_expires_at=timezone.localdate().replace(year=timezone.localdate().year + 1),
            plan=self.plan,
        )
        self.other_business = Business.objects.create(
            name="Ferreteria Norte",
            slug="norte",
            status=Business.Status.ACTIVE,
            service_expires_at=timezone.localdate().replace(year=timezone.localdate().year + 1),
            plan=self.plan,
        )
        self.user = User.objects.create_user(username="cajero", password="secret123")
        self.other_user = User.objects.create_user(username="otro", password="secret123")
        BusinessMembership.objects.create(
            business=self.business,
            user=self.user,
            role=BusinessMembership.Role.OWNER,
        )
        BusinessMembership.objects.create(
            business=self.other_business,
            user=self.other_user,
            role=BusinessMembership.Role.OWNER,
        )
        self.product = Product.objects.create(
            business=self.business,
            name="Refresco",
            barcode="750100",
            sku="REF-1",
            sale_price=Decimal("18.00"),
            cost_price=Decimal("12.00"),
            stock_quantity=Decimal("10"),
        )
        Product.objects.create(
            business=self.other_business,
            name="Martillo",
            barcode="999",
            sku="MART",
            sale_price=Decimal("120.00"),
            stock_quantity=Decimal("5"),
        )

    def test_paid_pos_sale_decrements_stock_and_records_payment(self):
        sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[{"product_id": self.product.id, "quantity": "2"}],
            payment_method=SalePayment.Method.CASH,
        )

        self.product.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.PAID)
        self.assertEqual(sale.total, Decimal("36.00"))
        self.assertEqual(self.product.stock_quantity, Decimal("8.000"))
        self.assertEqual(sale.payments.get().amount, Decimal("36.00"))
        self.assertTrue(
            InventoryMovement.objects.filter(
                business=self.business,
                product=self.product,
                movement_type=InventoryMovement.Type.SALE,
            ).exists()
        )

    def test_credit_sale_creates_balance_and_respects_limit(self):
        customer = Customer.objects.create(business=self.business, name="Don Chema")
        account = customer.credit_account
        account.credit_limit = Decimal("50.00")
        account.save()

        sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[{"product_id": self.product.id, "quantity": "2"}],
            payment_method=SalePayment.Method.CREDIT,
            customer_id=customer.id,
        )
        account.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.CREDIT)
        self.assertEqual(account.balance, Decimal("36.00"))

        with self.assertRaises(ValidationError):
            create_pos_sale(
                business=self.business,
                user=self.user,
                items=[{"product_id": self.product.id, "quantity": "1"}],
                payment_method=SalePayment.Method.CREDIT,
                customer_id=customer.id,
            )

    def test_inventory_view_is_scoped_to_current_business(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("inventory"))

        self.assertContains(response, "Refresco")
        self.assertNotContains(response, "Martillo")

    def test_suspended_business_cannot_register_sale(self):
        self.business.status = Business.Status.SUSPENDED
        self.business.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            create_pos_sale(
                business=self.business,
                user=self.user,
                items=[{"product_id": self.product.id, "quantity": "1"}],
                payment_method=SalePayment.Method.CASH,
            )

