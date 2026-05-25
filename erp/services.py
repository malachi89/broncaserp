from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    AuditLog,
    CashMovement,
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
        raise BusinessLocked("La cuenta está vencida o suspendida. Contacta al proveedor.")


def normalize_money_amount(value, *, empty_default=Decimal("0.00")):
    if value in (None, ""):
        return empty_default
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("El monto debe ser un número decimal válido.") from exc


def normalize_payment_method(method, *, allow_credit=True):
    method = method or SalePayment.Method.CASH
    valid_methods = {choice for choice, _label in SalePayment.Method.choices}
    if not allow_credit:
        valid_methods.discard(SalePayment.Method.CREDIT)
    if method not in valid_methods:
        raise ValidationError("Método de pago no válido.")
    return method


def normalize_request_nonce(value):
    return str(value or "").strip()[:80]


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
    opening_amount = normalize_money_amount(opening_amount)
    return CashSession.objects.create(
        business=business,
        opened_by=user,
        opening_amount=opening_amount,
    )


@transaction.atomic
def close_cash_session(session, user, closing_amount):
    if session.status == CashSession.Status.CLOSED:
        return session
    closing_amount = normalize_money_amount(closing_amount)
    session.status = CashSession.Status.CLOSED
    session.closed_by = user
    session.closing_amount = closing_amount
    session.closed_at = timezone.now()
    session.save(update_fields=["status", "closed_by", "closing_amount", "closed_at", "updated_at"])
    return session


def _choice_label(choices, value):
    return dict(choices).get(value, value)


def cash_session_totals(session):
    if not session:
        return {
            "methods": [],
            "types": [],
            "total": Decimal("0.00"),
            "expected_cash": Decimal("0.00"),
        }

    movements = CashMovement.objects.filter(cash_session=session)
    method_rows = (
        movements.values("method")
        .annotate(total=Sum("amount"))
        .order_by("method")
    )
    type_rows = (
        movements.values("movement_type")
        .annotate(total=Sum("amount"))
        .order_by("movement_type")
    )
    method_totals = [
        {
            "method": row["method"],
            "label": _choice_label(SalePayment.Method.choices, row["method"]),
            "total": row["total"] or Decimal("0.00"),
        }
        for row in method_rows
    ]
    type_totals = [
        {
            "type": row["movement_type"],
            "label": _choice_label(CashMovement.Type.choices, row["movement_type"]),
            "total": row["total"] or Decimal("0.00"),
        }
        for row in type_rows
    ]
    total = sum((row["total"] for row in method_totals), Decimal("0.00"))
    cash_total = sum(
        (row["total"] for row in method_totals if row["method"] == SalePayment.Method.CASH),
        Decimal("0.00"),
    )
    return {
        "methods": method_totals,
        "types": type_totals,
        "total": total,
        "expected_cash": session.opening_amount + cash_total,
    }


def out_of_session_cash_summary(business, user=None):
    movements = CashMovement.objects.filter(business=business, is_out_of_session=True)
    if user is not None:
        movements = movements.filter(created_by=user)
    return {
        "total": movements.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"),
        "recent": movements.select_related("sale", "credit_transaction")[:10],
    }


def _record_cash_movement(
    *,
    business,
    user,
    movement_type,
    method,
    amount,
    cash_session=None,
    sale=None,
    sale_payment=None,
    credit_transaction=None,
    note="",
):
    return CashMovement.objects.create(
        business=business,
        cash_session=cash_session,
        sale=sale,
        sale_payment=sale_payment,
        credit_transaction=credit_transaction,
        movement_type=movement_type,
        method=method,
        amount=amount,
        is_out_of_session=cash_session is None,
        note=note,
        created_by=user,
    )


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
        clean_item = {"product_id": product_id, "quantity": quantity}
        if "unit_price" in item:
            clean_item["unit_price"] = item.get("unit_price")
        if "discount_amount" in item:
            clean_item["discount_amount"] = item.get("discount_amount")
        if "price_override_reason" in item:
            clean_item["price_override_reason"] = str(item.get("price_override_reason") or "").strip()
        clean_items.append(clean_item)
    if not clean_items:
        raise ValidationError("Agrega al menos un producto a la venta.")
    return clean_items


def _load_sale_products(business, clean_items):
    product_ids = [item["product_id"] for item in clean_items]
    return {
        product.id: product
        for product in Product.objects.select_for_update().filter(
            id__in=product_ids,
            business=business,
            is_active=True,
        )
    }


def _create_sale_with_items(
    *,
    business,
    user,
    customer,
    payment_method,
    prepared_items,
    subtotal,
    discount_total,
    shipping_address="",
    customer_note="",
    internal_note="",
    origin=Sale.Origin.POS,
    tendered_amount=None,
    require_cash_tender=False,
    request_nonce="",
):
    payment_method = normalize_payment_method(payment_method)
    line_discount_total = sum((line["line_discount"] for line in prepared_items), Decimal("0.00"))
    total = (subtotal - line_discount_total - discount_total).quantize(Decimal("0.01"))
    if total < 0:
        raise ValidationError("El descuento no puede ser mayor al subtotal de la venta.")

    is_credit = payment_method == SalePayment.Method.CREDIT
    if is_credit and not customer:
        raise ValidationError("Selecciona un cliente para vender a crédito.")

    tendered = Decimal("0.00")
    change = Decimal("0.00")
    if payment_method == SalePayment.Method.CASH:
        cash_default = Decimal("0.00") if require_cash_tender else total
        tendered = normalize_money_amount(tendered_amount, empty_default=cash_default)
        if require_cash_tender and tendered <= 0:
            raise ValidationError("Captura el monto recibido en efectivo.")
        if tendered < total:
            raise ValidationError("El monto recibido no cubre el total de la venta.")
        change = (tendered - total).quantize(Decimal("0.01"))

    cash_session = current_cash_session(business, user)
    sale = Sale.objects.create(
        business=business,
        cash_session=cash_session,
        customer=customer,
        origin=origin,
        status=Sale.Status.CREDIT if is_credit else Sale.Status.PAID,
        subtotal=subtotal,
        discount_total=discount_total,
        total=total,
        shipping_address=shipping_address,
        customer_note=customer_note,
        internal_note=internal_note,
        request_nonce=request_nonce,
        created_by=user,
    )

    for line in prepared_items:
        product = line["product"]
        quantity = line["quantity"]
        unit_price = line["unit_price"]
        line_discount = line["line_discount"]
        line_total = line["line_total"]
        SaleItem.objects.create(
            sale=sale,
            business=business,
            product=product,
            product_name=product.name,
            quantity=quantity,
            unit_price=unit_price,
            original_unit_price=line["original_unit_price"],
            has_manual_price_override=line["has_manual_price_override"],
            price_override_reason=line["price_override_reason"],
            discount_amount=line_discount,
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

    sale_payment = SalePayment.objects.create(
        sale=sale,
        business=business,
        cash_session=None if is_credit else cash_session,
        method=payment_method,
        amount=total,
        tendered_amount=tendered,
        change_amount=change,
        received_by=user,
    )

    if not is_credit:
        _record_cash_movement(
            business=business,
            user=user,
            movement_type=CashMovement.Type.SALE_PAYMENT,
            method=payment_method,
            amount=total,
            cash_session=cash_session,
            sale=sale,
            sale_payment=sale_payment,
            note=f"Venta {sale.folio}",
        )

    if is_credit:
        account, _created = CreditAccount.objects.select_for_update().get_or_create(
            business=business,
            customer=customer,
            defaults={"credit_limit": Decimal("0")},
        )
        if not account.can_charge(total):
            raise ValidationError("La venta excede el límite de crédito del cliente.")
        account.balance += total
        account.save(update_fields=["balance", "updated_at"])
        CreditTransaction.objects.create(
            business=business,
            account=account,
            transaction_type=CreditTransaction.Type.SALE,
            sale=sale,
            amount=total,
            balance_after=account.balance,
            note=f"Venta a crédito #{sale.id}",
            created_by=user,
        )

    price_override_lines = [
        {
            "product_id": str(line["product"].id),
            "product_name": line["product"].name,
            "original_unit_price": str(line["original_unit_price"]),
            "unit_price": str(line["unit_price"]),
            "quantity": str(line["quantity"]),
            "reason": line["price_override_reason"],
        }
        for line in prepared_items
        if line["has_manual_price_override"]
    ]

    AuditLog.objects.create(
        business=business,
        actor=user,
        action="sale.created",
        object_type="Sale",
        object_id=str(sale.id),
        detail={
            "origin": origin,
            "payment_method": payment_method,
            "total": str(total),
            "has_manual_price_override": bool(price_override_lines),
            "price_override_lines": price_override_lines,
        },
    )
    return sale


@transaction.atomic
def create_pos_sale(
    business,
    user,
    items,
    payment_method,
    customer_id=None,
    tendered_amount=None,
    request_nonce="",
):
    ensure_business_can_operate(business)
    request_nonce = normalize_request_nonce(request_nonce)
    if request_nonce:
        existing_sale = Sale.objects.filter(business=business, request_nonce=request_nonce).first()
        if existing_sale:
            return existing_sale

    payment_method = normalize_payment_method(payment_method)
    clean_items = _normalize_cart_items(items)
    customer = None
    if customer_id:
        customer = Customer.objects.get(pk=customer_id, business=business, is_active=True)

    products = _load_sale_products(business, clean_items)

    subtotal = Decimal("0")
    prepared_items = []
    for item in clean_items:
        product = products.get(item["product_id"])
        if not product:
            raise ValidationError("Producto no encontrado.")
        quantity = item["quantity"]
        list_price = normalize_money_amount(product.sale_price)
        unit_price = normalize_money_amount(item.get("unit_price", list_price))
        if unit_price <= 0:
            raise ValidationError(f"El precio de {product.name} debe ser mayor a cero.")
        has_manual_price_override = unit_price != list_price
        price_override_reason = (item.get("price_override_reason") or "").strip()
        if has_manual_price_override and not price_override_reason:
            raise ValidationError(f"Captura el motivo del ajuste de precio para {product.name}.")
        if not has_manual_price_override:
            price_override_reason = ""

        line_total = (unit_price * quantity).quantize(Decimal("0.01"))
        subtotal += line_total
        prepared_items.append(
            {
                "product": product,
                "quantity": quantity,
                "unit_price": unit_price,
                "original_unit_price": list_price,
                "has_manual_price_override": has_manual_price_override,
                "price_override_reason": price_override_reason,
                "line_discount": Decimal("0.00"),
                "line_total": line_total,
            }
        )

    return _create_sale_with_items(
        business=business,
        user=user,
        customer=customer,
        payment_method=payment_method,
        prepared_items=prepared_items,
        subtotal=subtotal.quantize(Decimal("0.01")),
        discount_total=Decimal("0.00"),
        origin=Sale.Origin.POS,
        tendered_amount=tendered_amount,
        require_cash_tender=True,
        request_nonce=request_nonce,
    )

@transaction.atomic
def create_specialized_sale(
    *,
    business,
    user,
    customer_id,
    items,
    payment_method,
    discount_total=Decimal("0"),
    shipping_address="",
    customer_note="",
    internal_note="",
):
    ensure_business_can_operate(business)
    payment_method = normalize_payment_method(payment_method)
    clean_items = _normalize_cart_items(items)
    customer = Customer.objects.get(pk=customer_id, business=business, is_active=True)
    products = _load_sale_products(business, clean_items)
    sale_discount = normalize_money_amount(discount_total)

    subtotal = Decimal("0")
    prepared_items = []
    for item in clean_items:
        product = products.get(item["product_id"])
        if not product:
            raise ValidationError("Producto no encontrado.")

        quantity = item["quantity"]
        list_price = normalize_money_amount(product.sale_price)
        unit_price = normalize_money_amount(item.get("unit_price", list_price))
        if unit_price <= 0:
            raise ValidationError(f"El precio de {product.name} debe ser mayor a cero.")
        has_manual_price_override = unit_price != list_price
        price_override_reason = (item.get("price_override_reason") or "").strip()
        if not has_manual_price_override:
            price_override_reason = ""
        line_subtotal = (unit_price * quantity).quantize(Decimal("0.01"))
        line_discount = normalize_money_amount(item.get("discount_amount", "0"))
        if line_discount > line_subtotal:
            raise ValidationError(f"El descuento de {product.name} no puede exceder el importe de la línea.")
        line_total = (line_subtotal - line_discount).quantize(Decimal("0.01"))
        subtotal += line_subtotal
        prepared_items.append(
            {
                "product": product,
                "quantity": quantity,
                "unit_price": unit_price,
                "original_unit_price": list_price,
                "has_manual_price_override": has_manual_price_override,
                "price_override_reason": price_override_reason,
                "line_discount": line_discount,
                "line_total": line_total,
            }
        )

    return _create_sale_with_items(
        business=business,
        user=user,
        customer=customer,
        payment_method=payment_method,
        prepared_items=prepared_items,
        subtotal=subtotal.quantize(Decimal("0.01")),
        discount_total=sale_discount,
        shipping_address=shipping_address,
        customer_note=customer_note,
        internal_note=internal_note,
        origin=Sale.Origin.SPECIALIZED,
    )


@transaction.atomic
def record_credit_payment(account, amount, method, user, note=""):
    method = normalize_payment_method(method, allow_credit=False)
    amount = normalize_money_amount(amount)
    if amount <= 0:
        raise ValidationError("El abono debe ser mayor a cero.")
    account = CreditAccount.objects.select_for_update().get(pk=account.pk, business=account.business)
    if amount > account.balance:
        raise ValidationError("El abono no puede ser mayor al saldo del cliente.")
    account.balance -= amount
    account.save(update_fields=["balance", "updated_at"])
    transaction_record = CreditTransaction.objects.create(
        business=account.business,
        account=account,
        transaction_type=CreditTransaction.Type.PAYMENT,
        amount=-amount,
        balance_after=account.balance,
        note=note or f"Abono por {method}",
        created_by=user,
    )
    _record_cash_movement(
        business=account.business,
        user=user,
        movement_type=CashMovement.Type.CREDIT_PAYMENT,
        method=method,
        amount=amount,
        cash_session=current_cash_session(account.business, user),
        credit_transaction=transaction_record,
        note=note or f"Abono de {account.customer.name}",
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


@transaction.atomic
def record_manual_cash_movement(business, user, movement_type, amount, method=SalePayment.Method.CASH, note=""):
    ensure_business_can_operate(business)
    if movement_type not in {CashMovement.Type.MANUAL_IN, CashMovement.Type.MANUAL_OUT}:
        raise ValidationError("Tipo de movimiento de caja no válido.")
    method = normalize_payment_method(method, allow_credit=False)
    amount = normalize_money_amount(amount)
    if amount <= 0:
        raise ValidationError("El monto debe ser mayor a cero.")
    signed_amount = -amount if movement_type == CashMovement.Type.MANUAL_OUT else amount
    movement = _record_cash_movement(
        business=business,
        user=user,
        movement_type=movement_type,
        method=method,
        amount=signed_amount,
        cash_session=current_cash_session(business, user),
        note=note,
    )
    AuditLog.objects.create(
        business=business,
        actor=user,
        action="cash.movement",
        object_type="CashMovement",
        object_id=str(movement.id),
        detail={"movement_type": movement_type, "method": method, "amount": str(signed_amount)},
    )
    return movement


@transaction.atomic
def cancel_sale(sale, user, reason):
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("Captura el motivo de cancelación.")

    sale = (
        Sale.objects.select_for_update()
        .select_related("business", "customer")
        .prefetch_related("items", "payments")
        .get(pk=sale.pk, business=sale.business)
    )
    if sale.status == Sale.Status.CANCELLED:
        raise ValidationError("La venta ya está cancelada.")

    original_status = sale.status
    for item in sale.items.all():
        if not item.product_id:
            continue
        product = Product.objects.select_for_update().get(pk=item.product_id, business=sale.business)
        product.stock_quantity += item.quantity
        product.save(update_fields=["stock_quantity", "updated_at"])
        InventoryMovement.objects.create(
            business=sale.business,
            product=product,
            movement_type=InventoryMovement.Type.RETURN,
            quantity=item.quantity,
            stock_after=product.stock_quantity,
            note=f"Cancelación {sale.folio}",
            created_by=user,
        )

    if original_status == Sale.Status.CREDIT and sale.customer_id:
        account = CreditAccount.objects.select_for_update().get(business=sale.business, customer=sale.customer)
        if account.balance < sale.total:
            raise ValidationError("No se puede cancelar: el saldo actual del cliente es menor al total de la venta.")
        account.balance -= sale.total
        account.save(update_fields=["balance", "updated_at"])
        CreditTransaction.objects.create(
            business=sale.business,
            account=account,
            transaction_type=CreditTransaction.Type.ADJUSTMENT,
            sale=sale,
            amount=-sale.total,
            balance_after=account.balance,
            note=f"Cancelación {sale.folio}",
            created_by=user,
        )
    else:
        sale_payment = sale.payments.exclude(method=SalePayment.Method.CREDIT).order_by("created_at").first()
        if sale_payment:
            _record_cash_movement(
                business=sale.business,
                user=user,
                movement_type=CashMovement.Type.REFUND,
                method=sale_payment.method,
                amount=-sale_payment.amount,
                cash_session=current_cash_session(sale.business, user),
                sale=sale,
                sale_payment=sale_payment,
                note=f"Cancelación {sale.folio}",
            )

    sale.status = Sale.Status.CANCELLED
    sale.cancelled_by = user
    sale.cancelled_at = timezone.now()
    sale.cancel_reason = reason
    sale.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancel_reason", "updated_at"])

    AuditLog.objects.create(
        business=sale.business,
        actor=user,
        action="sale.cancelled",
        object_type="Sale",
        object_id=str(sale.id),
        detail={"reason": reason, "total": str(sale.total), "original_status": original_status},
    )
    return sale
