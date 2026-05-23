from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    AuditLog,
    CashSession,
    CreditAccount,
    CreditTransaction,
    Customer,
    InventoryMovement,
    Product,
    Sale,
    SaleItem,
    SalePayment,
)


class BusinessLocked(ValidationError):
    pass


def ensure_business_can_operate(business):
    if not business.can_operate:
        raise BusinessLocked("La cuenta esta vencida o suspendida. Contacta al proveedor.")


def current_cash_session(business, user):
    return (
        CashSession.objects.filter(
            business=business,
            opened_by=user,
            status=CashSession.Status.OPEN,
        )
        .order_by("-opened_at")
        .first()
    )


@transaction.atomic
def open_cash_session(business, user, opening_amount=Decimal("0")):
    ensure_business_can_operate(business)
    session = current_cash_session(business, user)
    if session:
        return session
    return CashSession.objects.create(
        business=business,
        opened_by=user,
        opening_amount=opening_amount or Decimal("0"),
    )


@transaction.atomic
def close_cash_session(session, user, closing_amount):
    if session.status == CashSession.Status.CLOSED:
        return session
    session.status = CashSession.Status.CLOSED
    session.closed_by = user
    session.closing_amount = closing_amount
    session.closed_at = timezone.now()
    session.save(update_fields=["status", "closed_by", "closing_amount", "closed_at", "updated_at"])
    return session


def cash_session_totals(session):
    payments = (
        SalePayment.objects.filter(sale__cash_session=session, sale__status=Sale.Status.PAID)
        .values("method")
        .annotate(total=Sum("amount"))
        .order_by("method")
    )
    return {row["method"]: row["total"] for row in payments}


@transaction.atomic
def adjust_inventory(product, quantity_delta, movement_type, user=None, note=""):
    product = Product.objects.select_for_update().get(pk=product.pk, business=product.business)
    product.stock_quantity += Decimal(str(quantity_delta))
    if product.stock_quantity < 0:
        raise ValidationError("El inventario no puede quedar negativo.")
    product.save(update_fields=["stock_quantity", "updated_at"])
    InventoryMovement.objects.create(
        business=product.business,
        product=product,
        movement_type=movement_type,
        quantity=Decimal(str(quantity_delta)),
        stock_after=product.stock_quantity,
        note=note,
        created_by=user,
    )
    return product


def _normalize_cart_items(items):
    clean_items = []
    for item in items:
        product_id = int(item["product_id"])
        quantity = Decimal(str(item.get("quantity", "1")))
        if quantity <= 0:
            raise ValidationError("La cantidad debe ser mayor a cero.")
        clean_items.append({"product_id": product_id, "quantity": quantity})
    if not clean_items:
        raise ValidationError("Agrega al menos un producto a la venta.")
    return clean_items


@transaction.atomic
def create_pos_sale(business, user, items, payment_method, customer_id=None):
    ensure_business_can_operate(business)
    clean_items = _normalize_cart_items(items)
    customer = None
    if customer_id:
        customer = Customer.objects.get(pk=customer_id, business=business, is_active=True)

    product_ids = [item["product_id"] for item in clean_items]
    products = {
        product.id: product
        for product in Product.objects.select_for_update().filter(
            id__in=product_ids,
            business=business,
            is_active=True,
        )
    }

    subtotal = Decimal("0")
    prepared_items = []
    for item in clean_items:
        product = products.get(item["product_id"])
        if not product:
            raise ValidationError("Producto no encontrado.")
        if product.stock_quantity < item["quantity"]:
            raise ValidationError(f"Stock insuficiente para {product.name}.")
        line_total = (product.sale_price * item["quantity"]).quantize(Decimal("0.01"))
        subtotal += line_total
        prepared_items.append((product, item["quantity"], line_total))

    total = subtotal.quantize(Decimal("0.01"))
    is_credit = payment_method == SalePayment.Method.CREDIT
    if is_credit and not customer:
        raise ValidationError("Selecciona un cliente para vender a credito.")

    cash_session = current_cash_session(business, user)
    sale = Sale.objects.create(
        business=business,
        cash_session=cash_session,
        customer=customer,
        status=Sale.Status.CREDIT if is_credit else Sale.Status.PAID,
        subtotal=subtotal,
        total=total,
        created_by=user,
    )

    for product, quantity, line_total in prepared_items:
        SaleItem.objects.create(
            sale=sale,
            business=business,
            product=product,
            product_name=product.name,
            quantity=quantity,
            unit_price=product.sale_price,
            line_total=line_total,
        )
        product.stock_quantity -= quantity
        product.save(update_fields=["stock_quantity", "updated_at"])
        InventoryMovement.objects.create(
            business=business,
            product=product,
            movement_type=InventoryMovement.Type.SALE,
            quantity=-quantity,
            stock_after=product.stock_quantity,
            note=f"Venta #{sale.id}",
            created_by=user,
        )

    SalePayment.objects.create(
        sale=sale,
        business=business,
        method=payment_method,
        amount=total,
        received_by=user,
    )

    if is_credit:
        account, _created = CreditAccount.objects.select_for_update().get_or_create(
            business=business,
            customer=customer,
            defaults={"credit_limit": Decimal("0")},
        )
        if not account.can_charge(total):
            raise ValidationError("La venta excede el limite de credito del cliente.")
        account.balance += total
        account.save(update_fields=["balance", "updated_at"])
        CreditTransaction.objects.create(
            business=business,
            account=account,
            transaction_type=CreditTransaction.Type.SALE,
            sale=sale,
            amount=total,
            balance_after=account.balance,
            note=f"Venta a credito #{sale.id}",
            created_by=user,
        )

    AuditLog.objects.create(
        business=business,
        actor=user,
        action="sale.created",
        object_type="Sale",
        object_id=str(sale.id),
        detail={"payment_method": payment_method, "total": str(total)},
    )
    return sale


@transaction.atomic
def record_credit_payment(account, amount, method, user, note=""):
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValidationError("El abono debe ser mayor a cero.")
    account = CreditAccount.objects.select_for_update().get(pk=account.pk, business=account.business)
    account.balance = max(Decimal("0"), account.balance - amount)
    account.save(update_fields=["balance", "updated_at"])
    CreditTransaction.objects.create(
        business=account.business,
        account=account,
        transaction_type=CreditTransaction.Type.PAYMENT,
        amount=-amount,
        balance_after=account.balance,
        note=note or f"Abono por {method}",
        created_by=user,
    )
    AuditLog.objects.create(
        business=account.business,
        actor=user,
        action="credit.payment",
        object_type="CreditAccount",
        object_id=str(account.id),
        detail={"amount": str(amount), "method": method},
    )
    return account

