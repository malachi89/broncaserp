from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Business,
    BusinessMembership,
    CashSession,
    CreditAccount,
    Customer,
    InventoryMovement,
    Plan,
    Product,
    Sale,
    SalePayment,
)
from .services import create_pos_sale, create_specialized_sale


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
        self.owner_membership = BusinessMembership.objects.create(
            business=self.business,
            user=self.user,
            role=BusinessMembership.Role.OWNER,
            is_owner=True,
            is_admin=True,
            can_access_pos=True,
            can_access_inventory=True,
            can_access_credits=True,
            can_access_cash=True,
            can_access_reports=True,
        )
        self.other_owner_membership = BusinessMembership.objects.create(
            business=self.other_business,
            user=self.other_user,
            role=BusinessMembership.Role.OWNER,
            is_owner=True,
            is_admin=True,
            can_access_pos=True,
            can_access_inventory=True,
            can_access_credits=True,
            can_access_cash=True,
            can_access_reports=True,
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

    def test_pos_sale_allows_negative_stock_when_inventory_is_short(self):
        sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[{"product_id": self.product.id, "quantity": "12"}],
            payment_method=SalePayment.Method.CASH,
        )

        self.product.refresh_from_db()
        movement = InventoryMovement.objects.get(
            business=self.business,
            product=self.product,
            movement_type=InventoryMovement.Type.SALE,
        )
        self.assertEqual(sale.total, Decimal("216.00"))
        self.assertEqual(self.product.stock_quantity, Decimal("-2.000"))
        self.assertEqual(movement.stock_after, Decimal("-2.000"))

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

    def test_specialized_sale_applies_line_and_global_discounts(self):
        customer = Customer.objects.create(
            business=self.business,
            name="Restaurante Centro",
            contact_name="Laura",
            address="Av. Hidalgo 120",
        )

        sale = create_specialized_sale(
            business=self.business,
            user=self.user,
            customer_id=customer.id,
            items=[
                {
                    "product_id": self.product.id,
                    "quantity": "2",
                    "discount_amount": "5.00",
                }
            ],
            payment_method=SalePayment.Method.TRANSFER,
            discount_total="3.00",
            shipping_address="Sucursal Roma",
            customer_note="Entregar por la tarde",
            internal_note="Ruta 2",
        )

        self.product.refresh_from_db()
        item = sale.items.get()
        self.assertEqual(sale.origin, Sale.Origin.SPECIALIZED)
        self.assertEqual(sale.subtotal, Decimal("36.00"))
        self.assertEqual(sale.discount_total, Decimal("3.00"))
        self.assertEqual(sale.total, Decimal("28.00"))
        self.assertEqual(item.discount_amount, Decimal("5.00"))
        self.assertEqual(item.line_total, Decimal("31.00"))
        self.assertEqual(self.product.stock_quantity, Decimal("8.000"))

    def test_specialized_credit_sale_updates_account_balance(self):
        customer = Customer.objects.create(business=self.business, name="Hotel del Norte")
        account = customer.credit_account
        account.credit_limit = Decimal("40.00")
        account.save()

        sale = create_specialized_sale(
            business=self.business,
            user=self.user,
            customer_id=customer.id,
            items=[{"product_id": self.product.id, "quantity": "2", "discount_amount": "6.00"}],
            payment_method=SalePayment.Method.CREDIT,
        )

        account.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.CREDIT)
        self.assertEqual(sale.total, Decimal("30.00"))
        self.assertEqual(account.balance, Decimal("30.00"))

    def test_inventory_view_is_scoped_to_current_business(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("inventory"))

        self.assertContains(response, "Refresco")
        self.assertNotContains(response, "Martillo")

    def test_clients_edit_updates_existing_customer(self):
        customer = Customer.objects.create(
            business=self.business,
            name="Cliente Original",
            contact_name="Ana",
            phone="5511111111",
            email="original@example.com",
            tax_id="XAXX010101000",
            address="Direccion original",
        )
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.post(
            f"{reverse('clients')}?edit={customer.id}",
            {
                "name": "Cliente Actualizado",
                "contact_name": "Bea",
                "phone": "5522222222",
                "email": "actualizado@example.com",
                "tax_id": "COSC8001137NA",
                "address": "Direccion nueva",
                "is_active": "on",
                "credit_limit": "1500.00",
            },
            follow=True,
        )

        customer.refresh_from_db()
        customer.credit_account.refresh_from_db()
        self.assertContains(response, "Cliente Cliente Actualizado guardado.")
        self.assertEqual(Customer.objects.filter(business=self.business).count(), 1)
        self.assertEqual(customer.name, "Cliente Actualizado")
        self.assertEqual(customer.contact_name, "Bea")
        self.assertEqual(customer.phone, "5522222222")
        self.assertEqual(customer.email, "actualizado@example.com")
        self.assertEqual(customer.tax_id, "COSC8001137NA")
        self.assertEqual(customer.address, "Direccion nueva")
        self.assertEqual(customer.credit_account.credit_limit, Decimal("1500.00"))

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

    def test_pos_user_is_blocked_from_inventory_and_reports(self):
        pos_user = User.objects.create_user(username="pos", password="secret123")
        BusinessMembership.objects.create(
            business=self.business,
            user=pos_user,
            role=BusinessMembership.Role.CASHIER,
            can_access_pos=True,
            can_access_cash=True,
        )
        client = Client()
        self.assertTrue(client.login(username="pos", password="secret123"))

        pos_response = client.get(reverse("pos"))
        inventory_response = client.get(reverse("inventory"), follow=True)
        reports_response = client.get(reverse("reports"), follow=True)

        self.assertEqual(pos_response.status_code, 200)
        self.assertContains(inventory_response, "No tienes acceso a este módulo.")
        self.assertContains(reports_response, "No tienes acceso a este módulo.")

    def test_pos_page_renders_internal_feedback_container(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("pos"))

        self.assertContains(response, 'id="pos-feedback"', html=False)

    def test_dashboard_requires_manual_queries(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("dashboard"))

        self.assertContains(response, "Ver")
        self.assertContains(response, 'name="start_date"', html=False)
        self.assertContains(response, 'name="end_date"', html=False)
        self.assertNotContains(response, "<th>Ticket</th>", html=False)

    def test_dashboard_loads_sales_for_selected_date_range(self):
        sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CASH,
        )
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(
            reverse("dashboard"),
            {
                "panel": "overview",
                "start_date": timezone.localdate().isoformat(),
                "end_date": timezone.localdate().isoformat(),
            },
        )

        self.assertContains(response, "Ventas del periodo")
        self.assertContains(response, f"#{sale.id}")
        self.assertContains(response, "$18")

    def test_open_cash_defaults_blank_amount_to_zero(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.post(reverse("open_cash"), {"opening_amount": ""}, follow=True)

        session = CashSession.objects.get(business=self.business, opened_by=self.user, status=CashSession.Status.OPEN)
        self.assertContains(response, "Caja abierta.")
        self.assertEqual(session.opening_amount, Decimal("0.00"))

    def test_close_cash_defaults_blank_amount_to_zero(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))
        client.post(reverse("open_cash"), {"opening_amount": "100"}, follow=True)

        response = client.post(reverse("close_cash"), {"closing_amount": ""}, follow=True)

        session = CashSession.objects.get(business=self.business, opened_by=self.user)
        self.assertContains(response, "Caja cerrada.")
        self.assertEqual(session.status, CashSession.Status.CLOSED)
        self.assertEqual(session.closing_amount, Decimal("0.00"))

    def test_reports_route_redirects_to_dashboard_reports_panel(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("reports"), follow=True)

        self.assertRedirects(response, reverse("dashboard"))
        self.assertContains(response, "Reportes ahora está integrado al dashboard.")

    def test_inventory_user_can_adjust_inventory_and_cannot_access_credits(self):
        inventory_user = User.objects.create_user(username="bodega", password="secret123")
        BusinessMembership.objects.create(
            business=self.business,
            user=inventory_user,
            role=BusinessMembership.Role.INVENTORY,
            can_access_inventory=True,
        )
        client = Client()
        self.assertTrue(client.login(username="bodega", password="secret123"))

        response = client.post(
            reverse("adjust_inventory"),
            {
                "product_id": self.product.id,
                "quantity_delta": "1",
                "note": "Conteo",
            },
            follow=True,
        )
        denied_response = client.get(reverse("credits"), follow=True)

        self.product.refresh_from_db()
        self.assertContains(response, "Inventario ajustado.")
        self.assertEqual(self.product.stock_quantity, Decimal("11.000"))
        self.assertContains(denied_response, "No tienes acceso a este módulo.")

    def test_owner_can_create_admin_user_from_settings(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.post(
            reverse("settings"),
            {
                "action": "create_user",
                "create-username": "gerente",
                "create-email": "gerente@example.com",
                "create-password": "secret123",
                "create-is_admin": "on",
                "create-is_active": "on",
            },
            follow=True,
        )

        membership = BusinessMembership.objects.get(business=self.business, user__username="gerente")
        self.assertContains(response, "Usuario gerente agregado al negocio.")
        self.assertTrue(membership.is_admin)
        self.assertTrue(membership.can_access_reports)
        self.assertTrue(membership.can_access_sales)
        self.assertTrue(membership.can_manage_users)

    def test_dashboard_navigation_hides_disallowed_modules(self):
        pos_user = User.objects.create_user(username="solo-pos", password="secret123")
        BusinessMembership.objects.create(
            business=self.business,
            user=pos_user,
            role=BusinessMembership.Role.CASHIER,
            can_access_pos=True,
            can_access_cash=True,
        )
        client = Client()
        self.assertTrue(client.login(username="solo-pos", password="secret123"))

        response = client.get(reverse("dashboard"))

        self.assertContains(response, reverse("pos"))
        self.assertContains(response, reverse("cash"))
        self.assertNotContains(response, reverse("inventory"))
        self.assertNotContains(response, reverse("sales"))
        self.assertNotContains(response, reverse("clients"))
        self.assertNotContains(response, reverse("credits"))
        self.assertNotContains(response, reverse("reports"))

    def test_sales_user_can_access_sales_module_but_not_credits(self):
        sales_user = User.objects.create_user(username="ventas", password="secret123")
        BusinessMembership.objects.create(
            business=self.business,
            user=sales_user,
            role=BusinessMembership.Role.CASHIER,
            can_access_sales=True,
        )
        client = Client()
        self.assertTrue(client.login(username="ventas", password="secret123"))

        sales_response = client.get(reverse("sales"))
        clients_response = client.get(reverse("clients"))
        credits_response = client.get(reverse("credits"), follow=True)

        self.assertEqual(sales_response.status_code, 200)
        self.assertEqual(clients_response.status_code, 200)
        self.assertContains(credits_response, "No tienes acceso a este módulo.")

    def test_new_sale_page_includes_customer_search_input(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("new_sale"))

        self.assertContains(response, 'id="customer-search"', html=False)

    def test_specialized_sale_documents_render(self):
        customer = Customer.objects.create(business=self.business, name="Cafeteria Luna")
        sale = create_specialized_sale(
            business=self.business,
            user=self.user,
            customer_id=customer.id,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CASH,
            shipping_address="Centro",
        )
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        note_response = client.get(reverse("sale_note", args=[sale.id]))
        invoice_response = client.get(reverse("sale_invoice", args=[sale.id]))

        self.assertContains(note_response, "Nota de venta")
        self.assertContains(note_response, sale.folio)
        self.assertContains(invoice_response, "Factura no timbrada")

    def test_owner_membership_cannot_be_edited_from_settings(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.post(
            reverse("settings"),
            {
                "action": "update_membership",
                "membership_id": self.owner_membership.id,
                f"member-{self.owner_membership.id}-is_active": "",
            },
            follow=True,
        )

        self.owner_membership.refresh_from_db()
        self.assertContains(response, "El usuario propietario no se edita desde esta pantalla.")
        self.assertTrue(self.owner_membership.is_active)

    def test_admin_cannot_update_membership_from_another_business(self):
        admin_user = User.objects.create_user(username="adminlocal", password="secret123")
        BusinessMembership.objects.create(
            business=self.business,
            user=admin_user,
            role=BusinessMembership.Role.ADMIN,
            is_admin=True,
        )
        outsider = User.objects.create_user(username="externo", password="secret123")
        other_membership = BusinessMembership.objects.create(
            business=self.other_business,
            user=outsider,
            role=BusinessMembership.Role.CASHIER,
            can_access_pos=True,
            can_access_cash=True,
        )
        client = Client()
        self.assertTrue(client.login(username="adminlocal", password="secret123"))

        response = client.post(
            reverse("settings"),
            {
                "action": "update_membership",
                "membership_id": other_membership.id,
                f"member-{other_membership.id}-can_access_pos": "on",
                f"member-{other_membership.id}-is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 404)
