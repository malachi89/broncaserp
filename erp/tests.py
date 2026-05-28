import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    AuditLog,
    Business,
    BusinessMembership,
    CashMovement,
    CashSession,
    CreditAccount,
    CreditTransaction,
    Customer,
    InventoryMovement,
    Plan,
    Product,
    ProviderPayment,
    Sale,
    SalePayment,
    UserSecurity,
)
from .services import cancel_sale, create_pos_sale, create_specialized_sale, record_credit_payment
from .templatetags.erp_extras import compact_quantity


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
            tendered_amount="40.00",
        )

        self.product.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.PAID)
        self.assertEqual(sale.total, Decimal("36.00"))
        self.assertEqual(self.product.stock_quantity, Decimal("8.000"))
        payment = sale.payments.get()
        movement = CashMovement.objects.get(sale=sale, movement_type=CashMovement.Type.SALE_PAYMENT)
        self.assertEqual(payment.amount, Decimal("36.00"))
        self.assertEqual(payment.tendered_amount, Decimal("40.00"))
        self.assertEqual(payment.change_amount, Decimal("4.00"))
        self.assertTrue(movement.is_out_of_session)
        self.assertIsNone(movement.cash_session)
        self.assertTrue(
            InventoryMovement.objects.filter(
                business=self.business,
                product=self.product,
                movement_type=InventoryMovement.Type.SALE,
            ).exists()
        )

    def test_cash_sale_with_open_session_appears_in_cash_cut(self):
        session = CashSession.objects.create(
            business=self.business,
            opened_by=self.user,
            opening_amount=Decimal("100.00"),
        )

        sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[{"product_id": self.product.id, "quantity": "2"}],
            payment_method=SalePayment.Method.CASH,
            tendered_amount="50.00",
        )

        payment = sale.payments.get()
        movement = CashMovement.objects.get(sale=sale, movement_type=CashMovement.Type.SALE_PAYMENT)
        self.assertEqual(sale.cash_session, session)
        self.assertEqual(payment.cash_session, session)
        self.assertEqual(payment.tendered_amount, Decimal("50.00"))
        self.assertEqual(payment.change_amount, Decimal("14.00"))
        self.assertEqual(movement.cash_session, session)
        self.assertFalse(movement.is_out_of_session)
        self.assertEqual(movement.amount, Decimal("36.00"))

    def test_pos_sale_allows_negative_stock_when_inventory_is_short(self):
        sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[{"product_id": self.product.id, "quantity": "12"}],
            payment_method=SalePayment.Method.CASH,
            tendered_amount="220.00",
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

    def test_pos_sale_nonce_prevents_duplicate_charge(self):
        first_sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CASH,
            tendered_amount="18.00",
            request_nonce="same-register-submit",
        )
        second_sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CASH,
            tendered_amount="18.00",
            request_nonce="same-register-submit",
        )

        self.product.refresh_from_db()
        self.assertEqual(first_sale.id, second_sale.id)
        self.assertEqual(Sale.objects.filter(business=self.business).count(), 1)
        self.assertEqual(CashMovement.objects.filter(sale=first_sale).count(), 1)
        self.assertEqual(self.product.stock_quantity, Decimal("9.000"))

    def test_pos_sale_allows_manual_price_override_with_trace(self):
        sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[
                {
                    "product_id": self.product.id,
                    "quantity": "2",
                    "unit_price": "15.50",
                    "price_override_reason": "Ajuste por empaque dañado",
                }
            ],
            payment_method=SalePayment.Method.CASH,
            tendered_amount="31.00",
        )

        item = sale.items.get()
        audit = AuditLog.objects.get(action="sale.created", object_id=str(sale.id))
        self.assertEqual(sale.total, Decimal("31.00"))
        self.assertTrue(sale.has_manual_price_override)
        self.assertTrue(item.has_manual_price_override)
        self.assertEqual(item.original_unit_price, Decimal("18.00"))
        self.assertEqual(item.unit_price, Decimal("15.50"))
        self.assertEqual(item.price_override_reason, "Ajuste por empaque dañado")
        self.assertTrue(audit.detail.get("has_manual_price_override"))
        self.assertEqual(len(audit.detail.get("price_override_lines", [])), 1)

    def test_pos_sale_rejects_manual_price_override_without_reason(self):
        with self.assertRaises(ValidationError):
            create_pos_sale(
                business=self.business,
                user=self.user,
                items=[{"product_id": self.product.id, "quantity": "1", "unit_price": "14.00"}],
                payment_method=SalePayment.Method.CASH,
                tendered_amount="14.00",
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

    def test_credit_payment_records_cash_movement_and_rejects_overpayment(self):
        customer = Customer.objects.create(business=self.business, name="Cliente Crédito")
        account = customer.credit_account
        account.credit_limit = Decimal("100.00")
        account.balance = Decimal("36.00")
        account.save()
        session = CashSession.objects.create(business=self.business, opened_by=self.user)

        record_credit_payment(account, "20.00", SalePayment.Method.CASH, self.user, note="Abono parcial")
        account.refresh_from_db()

        movement = CashMovement.objects.get(movement_type=CashMovement.Type.CREDIT_PAYMENT)
        self.assertEqual(account.balance, Decimal("16.00"))
        self.assertEqual(movement.cash_session, session)
        self.assertEqual(movement.amount, Decimal("20.00"))
        self.assertFalse(movement.is_out_of_session)

        with self.assertRaises(ValidationError):
            record_credit_payment(account, "20.00", SalePayment.Method.CASH, self.user)

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

    def test_cancel_paid_sale_restores_inventory_and_records_refund(self):
        CashSession.objects.create(business=self.business, opened_by=self.user)
        sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[{"product_id": self.product.id, "quantity": "2"}],
            payment_method=SalePayment.Method.CASH,
            tendered_amount="40.00",
        )

        cancel_sale(sale, self.user, "Error de captura")

        sale.refresh_from_db()
        self.product.refresh_from_db()
        refund = CashMovement.objects.get(sale=sale, movement_type=CashMovement.Type.REFUND)
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertEqual(sale.cancel_reason, "Error de captura")
        self.assertEqual(self.product.stock_quantity, Decimal("10.000"))
        self.assertEqual(refund.amount, Decimal("-36.00"))
        self.assertTrue(
            InventoryMovement.objects.filter(
                business=self.business,
                product=self.product,
                movement_type=InventoryMovement.Type.RETURN,
                quantity=Decimal("2.000"),
            ).exists()
        )

    def test_cancel_credit_sale_restores_inventory_and_reverts_credit(self):
        customer = Customer.objects.create(business=self.business, name="Cliente Cancelación")
        account = customer.credit_account
        account.credit_limit = Decimal("100.00")
        account.save()
        sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[{"product_id": self.product.id, "quantity": "2"}],
            payment_method=SalePayment.Method.CREDIT,
            customer_id=customer.id,
        )

        cancel_sale(sale, self.user, "Cliente cambió pedido")

        sale.refresh_from_db()
        account.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertEqual(account.balance, Decimal("0.00"))
        self.assertEqual(self.product.stock_quantity, Decimal("10.000"))
        self.assertTrue(
            CreditTransaction.objects.filter(
                business=self.business,
                account=account,
                transaction_type=CreditTransaction.Type.ADJUSTMENT,
                amount=Decimal("-36.00"),
            ).exists()
        )

    def test_inventory_view_is_scoped_to_current_business(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("inventory"), {"q": "Refresco"})

        self.assertContains(response, "Refresco")
        self.assertNotContains(response, "Martillo")

    def test_inventory_view_does_not_load_catalog_without_query(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("inventory"))

        self.assertContains(response, "Escribe una búsqueda para ver productos.")
        self.assertNotContains(response, "Refresco")

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

    def test_pos_user_can_reprint_pos_ticket_without_sales_access(self):
        pos_user = User.objects.create_user(username="pos-ticket", password="secret123")
        BusinessMembership.objects.create(
            business=self.business,
            user=pos_user,
            role=BusinessMembership.Role.CASHIER,
            can_access_pos=True,
        )
        sale = create_pos_sale(
            business=self.business,
            user=pos_user,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CASH,
            tendered_amount="20.00",
        )
        client = Client()
        self.assertTrue(client.login(username="pos-ticket", password="secret123"))

        ticket_response = client.get(reverse("pos_sale_note", args=[sale.id]))
        detail_response = client.get(reverse("sale_detail", args=[sale.id]), follow=True)

        self.assertEqual(ticket_response.status_code, 200)
        self.assertContains(ticket_response, "Ticket de venta")
        self.assertContains(ticket_response, sale.folio)
        self.assertContains(detail_response, "No tienes acceso a este módulo.")

    def test_pos_page_renders_internal_feedback_container(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        client.post(
            reverse("open_cash"),
            {
                "opening_amount": "10.00",
                "next": reverse("pos"),
            },
            follow=True,
        )

        response = client.get(reverse("pos"))

        self.assertContains(response, 'id="pos-feedback"', html=False)
        self.assertContains(response, 'aria-label="Cerrar caja"', html=False)
        self.assertNotContains(response, 'id="customer-id"', html=False)
        self.assertNotContains(response, "Últimos tickets")
        self.assertNotContains(response, "Selecciona cliente")
        self.assertNotContains(response, 'value="credit"', html=False)

    def test_sidebar_logout_button_is_in_sidebar_with_clear_label(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("pos"))

        self.assertContains(response, "Cerrar sesión")
        self.assertNotContains(response, ">Salir<", html=False)
        self.assertContains(response, 'class="sidebar-logout-button"', html=False)

    def test_dashboard_requires_manual_queries(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("dashboard"))

        self.assertContains(response, "Ver")
        self.assertContains(response, 'name="start_date"', html=False)
        self.assertContains(response, 'name="end_date"', html=False)
        today = timezone.localdate().isoformat()
        self.assertContains(response, f'value="{today}"', html=False)
        self.assertNotContains(response, "<th>Ticket</th>", html=False)

    def test_dashboard_loads_sales_for_selected_date_range(self):
        sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CASH,
            tendered_amount="18.00",
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

    def test_pos_blocks_sale_submission_when_cash_is_closed(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.post(
            reverse("pos"),
            {
                "cart_json": '[{"product_id": %s, "quantity": "1"}]' % self.product.id,
                "payment_method": SalePayment.Method.CASH,
                "tendered_amount": "20.00",
                "request_nonce": "pos-no-cash",
            },
            follow=True,
        )

        self.assertEqual(Sale.objects.filter(business=self.business, origin=Sale.Origin.POS).count(), 0)
        self.assertContains(response, "Debes abrir la caja antes de registrar ventas.")

    def test_pos_user_can_open_cash_from_pos_without_cash_module_access(self):
        pos_user = User.objects.create_user(username="pos-only", password="secret123")
        BusinessMembership.objects.create(
            business=self.business,
            user=pos_user,
            role=BusinessMembership.Role.CASHIER,
            can_access_pos=True,
        )
        client = Client()
        self.assertTrue(client.login(username="pos-only", password="secret123"))

        response = client.post(
            reverse("open_cash"),
            {
                "opening_amount": "125.50",
                "next": reverse("pos"),
            },
            follow=True,
        )

        session = CashSession.objects.get(business=self.business, opened_by=pos_user, status=CashSession.Status.OPEN)
        self.assertEqual(response.redirect_chain, [(reverse("pos"), 302)])
        self.assertContains(response, "Caja abierta.")
        self.assertEqual(session.opening_amount, Decimal("125.50"))

    def test_pos_user_can_close_cash_from_pos_without_cash_module_access(self):
        pos_user = User.objects.create_user(username="pos-close", password="secret123")
        BusinessMembership.objects.create(
            business=self.business,
            user=pos_user,
            role=BusinessMembership.Role.CASHIER,
            can_access_pos=True,
        )
        client = Client()
        self.assertTrue(client.login(username="pos-close", password="secret123"))
        client.post(
            reverse("open_cash"),
            {
                "opening_amount": "80.00",
                "next": reverse("pos"),
            },
            follow=True,
        )

        response = client.post(
            reverse("close_cash"),
            {
                "closing_amount": "",
                "next": reverse("pos"),
            },
            follow=True,
        )

        session = CashSession.objects.get(business=self.business, opened_by=pos_user)
        self.assertEqual(response.redirect_chain, [(reverse("pos"), 302)])
        self.assertContains(response, "Caja cerrada.")
        self.assertEqual(session.status, CashSession.Status.CLOSED)
        self.assertEqual(session.closing_amount, Decimal("0.00"))

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

    def test_settings_user_creation_updates_password_for_existing_user(self):
        existing_user = User.objects.create_user(username="u1", password="old-pass")
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.post(
            reverse("settings"),
            {
                "action": "create_user",
                "create-username": "u1",
                "create-email": "u1@1.com",
                "create-password": "123",
                "create-can_access_pos": "on",
                "create-is_active": "on",
            },
            follow=True,
        )

        existing_user.refresh_from_db()
        membership = BusinessMembership.objects.get(business=self.business, user=existing_user)
        self.assertContains(response, "Usuario u1 agregado al negocio.")
        self.assertTrue(existing_user.check_password("123"))
        self.assertEqual(existing_user.email, "u1@1.com")
        self.assertTrue(membership.can_access_pos)

    def test_settings_user_creation_shows_error_when_no_access_is_selected(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.post(
            reverse("settings"),
            {
                "action": "create_user",
                "create-username": "sin_acceso",
                "create-email": "sin_acceso@example.com",
                "create-password": "secret123",
                "create-is_active": "on",
            },
            follow=True,
        )

        self.assertContains(response, "Selecciona al menos un acceso o marca Administrador.")
        self.assertFalse(User.objects.filter(username="sin_acceso").exists())

    def test_settings_user_creation_can_grant_reports_access(self):
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.post(
            reverse("settings"),
            {
                "action": "create_user",
                "create-username": "reportero",
                "create-email": "reportero@example.com",
                "create-password": "secret123",
                "create-can_access_reports": "on",
                "create-is_active": "on",
            },
            follow=True,
        )

        membership = BusinessMembership.objects.get(business=self.business, user__username="reportero")
        self.assertContains(response, "Usuario reportero agregado al negocio.")
        self.assertTrue(membership.can_access_reports)
        self.assertFalse(membership.can_manage_users)

    def test_settings_can_expire_user_password_and_force_reset(self):
        target_user = User.objects.create_user(username="temporada", password="secret123")
        target_membership = BusinessMembership.objects.create(
            business=self.business,
            user=target_user,
            role=BusinessMembership.Role.CASHIER,
            can_access_pos=True,
        )
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.post(
            reverse("settings"),
            {
                "action": "expire_password",
                "membership_id": target_membership.id,
            },
            follow=True,
        )

        target_user.refresh_from_db()
        self.assertContains(response, "La contraseña de temporada fue caducada.")
        self.assertTrue(target_user.password_security.must_change_password)
        self.assertIsNotNone(target_user.password_security.password_expired_at)

        expired_client = Client()
        self.assertTrue(expired_client.login(username="temporada", password="secret123"))

        redirected = expired_client.get(reverse("dashboard"), follow=True)
        self.assertContains(redirected, "Cambiar contraseña")
        self.assertNotContains(redirected, "Contraseña actual")

        change_response = expired_client.post(
            reverse("password_change"),
            {
                "new_password1": "nueva12345",
                "new_password2": "nueva12345",
            },
            follow=True,
        )

        target_user.refresh_from_db()
        self.assertContains(change_response, "Contraseña actualizada.")
        self.assertFalse(target_user.password_security.must_change_password)
        self.assertTrue(target_user.check_password("nueva12345"))

    def test_password_change_preserves_active_business_context(self):
        hybrid_user = User.objects.create_user(username="multi", password="secret123")
        BusinessMembership.objects.create(
            business=self.other_business,
            user=hybrid_user,
            role=BusinessMembership.Role.OWNER,
            is_owner=True,
            is_admin=True,
            can_access_pos=True,
            can_access_inventory=True,
            can_access_credits=True,
            can_access_cash=True,
            can_access_reports=True,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=hybrid_user,
            role=BusinessMembership.Role.CASHIER,
            can_access_pos=True,
        )

        client = Client()
        self.assertTrue(client.login(username="multi", password="secret123"))
        session = client.session
        session["business_id"] = self.business.id
        session.save()

        response = client.post(
            reverse("password_change"),
            {
                "old_password": "secret123",
                "new_password1": "nueva12345",
                "new_password2": "nueva12345",
            },
            follow=True,
        )

        self.assertContains(response, self.business.name)
        self.assertNotContains(response, self.other_business.name)
        self.assertEqual(client.session.get("business_id"), self.business.id)

    def test_current_business_defaults_to_most_recent_membership(self):
        hybrid_user = User.objects.create_user(username="multi-default", password="secret123")
        BusinessMembership.objects.create(
            business=self.other_business,
            user=hybrid_user,
            role=BusinessMembership.Role.OWNER,
            is_owner=True,
            is_admin=True,
            can_access_pos=True,
            can_access_inventory=True,
            can_access_credits=True,
            can_access_cash=True,
            can_access_reports=True,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=hybrid_user,
            role=BusinessMembership.Role.CASHIER,
            can_access_pos=True,
            can_access_sales=True,
        )

        client = Client()
        self.assertTrue(client.login(username="multi-default", password="secret123"))

        response = client.get(reverse("pos"))

        self.assertContains(response, self.business.name)
        self.assertNotContains(response, self.other_business.name)
        self.assertEqual(client.session.get("business_id"), self.business.id)

    def test_operational_user_sidebar_hides_dashboard_and_settings_links(self):
        op_user = User.objects.create_user(username="solo-op", password="secret123")
        BusinessMembership.objects.create(
            business=self.business,
            user=op_user,
            role=BusinessMembership.Role.CASHIER,
            can_access_pos=True,
            can_access_sales=True,
        )
        client = Client()
        self.assertTrue(client.login(username="solo-op", password="secret123"))

        response = client.get(reverse("pos"))

        self.assertContains(response, '<a href="/pos/">Punto de Venta</a>', html=False)
        self.assertContains(response, '<a href="/ventas/">Ventas</a>', html=False)
        self.assertContains(response, '<a href="/clientes/">Clientes</a>', html=False)
        self.assertNotContains(response, '<a href="/">Dashboard</a>', html=False)
        self.assertNotContains(response, '<a href="/configuracion/">Configuración</a>', html=False)
        self.assertNotContains(response, '<a href="/inventario/">Inventario</a>', html=False)
        self.assertNotContains(response, '<a href="/creditos/">Créditos</a>', html=False)
        self.assertNotContains(response, '<a href="/caja/">Caja</a>', html=False)
        self.assertNotContains(response, '<a href="/reportes/">Reportes</a>', html=False)

    def test_operational_user_is_redirected_away_from_dashboard_and_settings(self):
        op_user = User.objects.create_user(username="solo-redirect", password="secret123")
        BusinessMembership.objects.create(
            business=self.business,
            user=op_user,
            role=BusinessMembership.Role.CASHIER,
            can_access_pos=True,
            can_access_sales=True,
        )
        client = Client()
        self.assertTrue(client.login(username="solo-redirect", password="secret123"))

        dashboard_response = client.get(reverse("dashboard"))
        settings_response = client.get(reverse("settings"))

        self.assertEqual(dashboard_response.status_code, 302)
        self.assertEqual(dashboard_response.url, reverse("pos"))
        self.assertEqual(settings_response.status_code, 302)
        self.assertEqual(settings_response.url, reverse("pos"))

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
        customer = Customer.objects.create(
            business=self.business,
            name="Cafeteria Luna",
            contact_name="Ana Perez",
            phone="5512345678",
            tax_id="COSC8001137NA",
            address="Calle 10 #123",
        )
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("new_sale"))

        self.assertContains(response, "Carrito")
        self.assertContains(response, "Cliente y pago")
        self.assertContains(response, "sale-form-column", html=False)
        self.assertContains(response, "sale-cart-panel", html=False)
        self.assertContains(response, "sale-customer-panel", html=False)
        self.assertContains(response, 'id="customer-search"', html=False)
        self.assertContains(response, 'id="customer-options"', html=False)
        self.assertContains(response, 'id="customer-selected-info"', html=False)
        self.assertContains(response, 'type="hidden" name="customer_id"', html=False)
        self.assertContains(response, f'data-phone="{customer.phone}"', html=False)
        self.assertContains(response, f'data-tax-id="{customer.tax_id}"', html=False)
        self.assertContains(response, "RFC: COSC8001137NA", html=False)
        self.assertContains(response, "Tel: 5512345678", html=False)
        self.assertContains(response, "Cantidad")
        self.assertContains(response, "Descuento")
        self.assertContains(response, "Notas")
        self.assertNotContains(response, "Nota para cliente")
        self.assertNotContains(response, "Nota interna")

    def test_new_sale_product_search_preserves_customer_and_cart_state(self):
        customer = Customer.objects.create(
            business=self.business,
            name="Cafeteria Luna",
            contact_name="Ana Perez",
            phone="5512345678",
            tax_id="COSC8001137NA",
            address="Calle 10 #123",
        )
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(
            reverse("new_sale"),
            {
                "q": self.product.name,
                "customer_id": str(customer.id),
                "customer_name": f"{customer.name} · {customer.phone} · {customer.tax_id}",
                "shipping_address": "Sucursal Centro",
                "payment_method": SalePayment.Method.TRANSFER,
                "notes": "Entregar hoy",
                "discount_total": "5.50",
                "cart_json": json.dumps(
                    [
                        {
                            "product_id": self.product.id,
                            "quantity": 2,
                            "unit_price": "14.00",
                            "discount_amount": "1.50",
                        }
                    ]
                ),
            },
        )

        self.assertContains(response, f'id="customer-id" value="{customer.id}"', html=False)
        self.assertContains(response, f'value="{customer.name} · {customer.phone} · {customer.tax_id}"', html=False)
        self.assertContains(response, 'id="sale-search-cart-json"', html=False)
        self.assertContains(response, 'id="sale-initial-cart"', html=False)
        self.assertContains(response, '"product_id": %s' % self.product.id, html=False)
        self.assertContains(response, '"quantity": 2.0', html=False)
        self.assertContains(response, '"discount": 1.5', html=False)
        self.assertContains(response, 'name="shipping_address"', html=False)
        self.assertContains(response, "Sucursal Centro")
        self.assertContains(response, 'name="notes"', html=False)
        self.assertContains(response, "Entregar hoy")
        self.assertContains(response, 'value="5.50"', html=False)
        self.assertContains(response, f'<option value="{SalePayment.Method.TRANSFER}" selected>', html=False)

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

        self.assertContains(note_response, "Nota de venta")
        self.assertContains(note_response, sale.folio)
        self.assertContains(note_response, "Notas")
        self.assertNotContains(note_response, "NV")

    def test_pos_sale_documents_render_for_dashboard_links(self):
        sale = create_pos_sale(
            business=self.business,
            user=self.user,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CASH,
            tendered_amount="20.00",
        )
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        detail_response = client.get(reverse("sale_detail", args=[sale.id]))
        note_response = client.get(reverse("sale_note", args=[sale.id]))

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(note_response.status_code, 200)
        self.assertContains(detail_response, sale.folio)
        self.assertContains(note_response, sale.folio)
        self.assertContains(detail_response, "Mostrador")
        self.assertContains(note_response, "Mostrador")

    def test_sales_page_shows_status_and_payment_method_columns(self):
        customer = Customer.objects.create(business=self.business, name="Cliente Metodo")
        create_specialized_sale(
            business=self.business,
            user=self.user,
            customer_id=customer.id,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CREDIT,
        )
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("sales"), {"q": "Cliente Metodo"})

        self.assertContains(response, "Estado")
        self.assertContains(response, "Método")
        self.assertContains(response, "Pendiente de pago")
        self.assertContains(response, "Crédito")
        self.assertContains(response, 'name="q"', html=False)
        self.assertContains(response, 'name="estado"', html=False)
        self.assertContains(response, 'name="metodo"', html=False)

    def test_sales_page_filters_by_status_and_method(self):
        customer = Customer.objects.create(business=self.business, name="Cliente Filtros")
        credit_sale = create_specialized_sale(
            business=self.business,
            user=self.user,
            customer_id=customer.id,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CREDIT,
        )
        paid_sale = create_specialized_sale(
            business=self.business,
            user=self.user,
            customer_id=customer.id,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CASH,
        )
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(
            reverse("sales"),
            {
                "estado": Sale.Status.CREDIT,
                "metodo": SalePayment.Method.CREDIT,
            },
        )

        self.assertContains(response, credit_sale.folio)
        self.assertNotContains(response, paid_sale.folio)

    def test_sales_page_filters_by_folio_with_unified_search(self):
        customer = Customer.objects.create(business=self.business, name="Cliente Folio")
        target_sale = create_specialized_sale(
            business=self.business,
            user=self.user,
            customer_id=customer.id,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CASH,
        )
        other_sale = create_specialized_sale(
            business=self.business,
            user=self.user,
            customer_id=customer.id,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CARD,
        )
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("sales"), {"q": target_sale.folio})

        self.assertContains(response, target_sale.folio)
        self.assertNotContains(response, other_sale.folio)

    def test_sales_page_orders_rows_by_selected_column(self):
        customer_a = Customer.objects.create(business=self.business, name="Cliente B")
        customer_b = Customer.objects.create(business=self.business, name="Cliente A")
        first_sale = create_specialized_sale(
            business=self.business,
            user=self.user,
            customer_id=customer_a.id,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CASH,
        )
        second_sale = create_specialized_sale(
            business=self.business,
            user=self.user,
            customer_id=customer_b.id,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CASH,
        )
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(
            reverse("sales"),
            {
                "q": "Cliente",
                "sort": "cliente",
                "dir": "asc",
            },
        )

        content = response.content.decode("utf-8")
        self.assertLess(content.find(second_sale.folio), content.find(first_sale.folio))

    def test_sales_page_does_not_load_list_without_query(self):
        customer = Customer.objects.create(business=self.business, name="Cliente Sin Lista")
        create_specialized_sale(
            business=self.business,
            user=self.user,
            customer_id=customer.id,
            items=[{"product_id": self.product.id, "quantity": "1"}],
            payment_method=SalePayment.Method.CASH,
        )
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("sales"))

        self.assertContains(response, "Escribe una búsqueda para ver ventas.")
        self.assertNotContains(response, "Cliente Sin Lista")

    def test_clients_page_does_not_load_list_without_query(self):
        Customer.objects.create(business=self.business, name="Cliente Busqueda")
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("clients"))

        self.assertContains(response, "Escribe una búsqueda para ver clientes.")
        self.assertNotContains(response, "Cliente Busqueda")

    def test_credits_page_does_not_load_list_without_query(self):
        Customer.objects.create(business=self.business, name="Credito Sin Busqueda")
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("credits"))

        self.assertContains(response, "Escribe una búsqueda para ver clientes con crédito.")
        self.assertNotContains(response, "Credito Sin Busqueda")

    def test_credits_page_filters_accounts_by_query(self):
        Customer.objects.create(business=self.business, name="Credito Uno")
        Customer.objects.create(business=self.business, name="Credito Dos")
        client = Client()
        self.assertTrue(client.login(username="cajero", password="secret123"))

        response = client.get(reverse("credits"), {"q": "Uno"})

        self.assertContains(response, "Credito Uno")
        self.assertNotContains(response, "Credito Dos")

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

    def test_provider_console_uses_administration_label(self):
        provider = User.objects.create_superuser(username="dueno", password="secret123")
        client = Client()
        self.assertTrue(client.login(username="dueno", password="secret123"))

        response = client.get(reverse("provider_dashboard"))

        self.assertContains(response, "Administración")
        self.assertNotContains(response, "Dashboard del Proveedor")

    def test_provider_business_detail_manages_account_and_audits_change(self):
        provider = User.objects.create_superuser(username="dueno", password="secret123")
        client = Client()
        self.assertTrue(client.login(username="dueno", password="secret123"))

        response = client.post(
            reverse("provider_business_detail", args=[self.business.id]),
            {
                "action": "update_business",
                "business-name": "Abarrotes Lupita Centro",
                "business-slug": "lupita-centro",
                "business-owner_name": "Lupita Garcia",
                "business-phone": "5511112222",
                "business-email": "lupita@example.com",
                "business-tax_id": "LUGA800101AA1",
                "business-address": "Calle Central 1",
                "business-currency": "MXN",
                "business-plan": str(self.plan.id),
                "business-status": Business.Status.PAST_DUE,
                "business-service_expires_at": str(timezone.localdate()),
                "business-notes": "Revisar renovación",
            },
        )

        self.assertRedirects(response, reverse("provider_business_detail", args=[self.business.id]))
        self.business.refresh_from_db()
        self.assertEqual(self.business.name, "Abarrotes Lupita Centro")
        self.assertEqual(self.business.status, Business.Status.PAST_DUE)
        self.assertTrue(AuditLog.objects.filter(action="provider.business_updated", business=self.business).exists())

    def test_provider_business_detail_can_create_user_expire_password_and_record_payment(self):
        provider = User.objects.create_superuser(username="dueno", password="secret123")
        client = Client()
        self.assertTrue(client.login(username="dueno", password="secret123"))

        create_response = client.post(
            reverse("provider_business_detail", args=[self.business.id]),
            {
                "action": "create_user",
                "create-username": "soporte-caja",
                "create-email": "soporte@example.com",
                "create-password": "temporal123",
                "create-can_access_pos": "on",
                "create-is_active": "on",
            },
        )

        self.assertRedirects(create_response, reverse("provider_business_detail", args=[self.business.id]))
        membership = BusinessMembership.objects.get(business=self.business, user__username="soporte-caja")
        self.assertTrue(membership.can_access_pos)
        self.assertTrue(AuditLog.objects.filter(action="provider.user_created", business=self.business).exists())

        expire_response = client.post(
            reverse("provider_business_detail", args=[self.business.id]),
            {
                "action": "expire_password",
                "membership_id": str(membership.id),
            },
        )

        self.assertRedirects(expire_response, reverse("provider_business_detail", args=[self.business.id]))
        membership.user.password_security.refresh_from_db()
        self.assertTrue(membership.user.password_security.must_change_password)
        self.assertTrue(AuditLog.objects.filter(action="provider.user_password_expired", business=self.business).exists())

        payment_response = client.post(
            reverse("provider_business_detail", args=[self.business.id]),
            {
                "action": "payment",
                "payment-amount": "399.00",
                "payment-paid_at": str(timezone.localdate()),
                "payment-method": "Transferencia",
                "payment-note": "Mensualidad",
            },
        )

        self.assertRedirects(payment_response, reverse("provider_business_detail", args=[self.business.id]))
        payment = ProviderPayment.objects.get(business=self.business)
        self.assertEqual(payment.amount, Decimal("399.00"))
        self.assertTrue(AuditLog.objects.filter(action="provider.payment_created", object_id=str(payment.id)).exists())

    def test_provider_business_detail_cannot_edit_owner_membership(self):
        provider = User.objects.create_superuser(username="dueno", password="secret123")
        client = Client()
        self.assertTrue(client.login(username="dueno", password="secret123"))

        response = client.post(
            reverse("provider_business_detail", args=[self.business.id]),
            {
                "action": "update_membership",
                "membership_id": str(self.owner_membership.id),
                f"member-{self.owner_membership.id}-can_access_pos": "on",
                f"member-{self.owner_membership.id}-is_active": "on",
            },
            follow=True,
        )

        self.assertContains(response, "El usuario propietario no se edita desde Administración.")


class QuantityFormatTemplateFilterTests(SimpleTestCase):
    def test_compact_quantity_hides_trailing_zeros_for_whole_numbers(self):
        self.assertEqual(compact_quantity(Decimal("5.000")), "5")

    def test_compact_quantity_keeps_significant_decimals(self):
        self.assertEqual(compact_quantity(Decimal("5.500")), "5.5")
