import json
import secrets
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    CustomerForm,
    MembershipAccessForm,
    ProductForm,
    ProviderBusinessForm,
    ProviderBusinessUpdateForm,
    ProviderPaymentForm,
    SpecializedSaleForm,
    TenantUserForm,
)
from .models import (
    Business,
    BusinessMembership,
    CashMovement,
    CashSession,
    AuditLog,
    CreditAccount,
    Customer,
    InventoryMovement,
    Plan,
    Product,
    ProviderPayment,
    Sale,
    SalePayment,
    UserSecurity,
)
from .services import (
    adjust_inventory,
    cancel_sale,
    cash_session_totals,
    close_cash_session,
    create_pos_sale,
    create_specialized_sale,
    current_cash_session,
    open_cash_session,
    out_of_session_cash_summary,
    record_credit_payment,
    record_manual_cash_movement,
)


def is_provider(user):
    return user.is_authenticated and user.is_superuser


def tenant_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        if request.user.is_superuser:
            return redirect("provider_dashboard")
        if not request.business:
            return redirect("no_business")
        return view_func(request, *args, **kwargs)

    return wrapped


def module_access_required(permission_attr, denied_message="No tienes acceso a este módulo."):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            membership = getattr(request, "membership", None)
            if not membership or not getattr(membership, permission_attr, False):
                messages.error(request, denied_message)
                return redirect("dashboard")
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


def can_manage_cash_from_request(request):
    membership = getattr(request, "membership", None)
    return bool(
        membership
        and membership.is_active
        and (
            membership.has_full_access
            or membership.can_access_cash
            or membership.can_access_pos
        )
    )


def preferred_landing_url_name(membership):
    if not membership:
        return "dashboard"
    return membership.preferred_landing_url_name


def build_membership_rows(request, memberships, can_manage_users, bound_form=None):
    rows = []
    for membership in memberships:
        password_security = getattr(membership.user, "password_security", None)
        form = None
        if can_manage_users and not membership.is_owner:
            if bound_form is not None and bound_form.instance.pk == membership.pk:
                form = bound_form
            else:
                form = MembershipAccessForm(
                    instance=membership,
                    prefix=f"member-{membership.id}",
                    acting_membership=getattr(request, "membership", None),
                )
        rows.append(
            {
                "membership": membership,
                "form": form,
                "editable": form is not None,
                "must_change_password": bool(password_security and password_security.must_change_password),
            }
        )
    return rows


@login_required
def no_business(request):
    return render(request, "erp/no_business.html")


@login_required
def password_change(request):
    user_security, _created = UserSecurity.objects.get_or_create(user=request.user)
    requires_current_password = not user_security.must_change_password
    form_class = PasswordChangeForm if requires_current_password else SetPasswordForm
    form = form_class(user=request.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        active_business_id = request.session.get("business_id")
        form.save()
        update_session_auth_hash(request, request.user)
        if active_business_id:
            request.session["business_id"] = active_business_id
        user_security.clear_password_expiry()
        AuditLog.objects.create(
            business=getattr(request, "business", None),
            actor=request.user,
            action="user.password_changed",
            object_type="User",
            object_id=str(request.user.id),
            detail={"username": request.user.username},
        )
        messages.success(request, "Contraseña actualizada.")
        return redirect(preferred_landing_url_name(getattr(request, "membership", None)))

    return render(
        request,
        "registration/password_change.html",
        {
            "form": form,
            "requires_current_password": requires_current_password,
        },
    )


@tenant_required
def dashboard(request):
    if not request.membership.can_manage_users:
        return redirect(preferred_landing_url_name(request.membership))

    business = request.business
    today = timezone.localdate()
    panel = request.GET.get("panel", "").strip()
    overview_requested = panel == "overview"
    start_date_value = request.GET.get("start_date") or str(today)
    end_date_value = request.GET.get("end_date") or str(today)

    sales_total = None
    low_stock_count = None
    credit_balance = None
    sales = None

    if overview_requested:
        start_date = parse_date(start_date_value)
        end_date = parse_date(end_date_value)
        if not start_date:
            messages.error(request, "La fecha inicial no es válida.")
        elif not end_date:
            messages.error(request, "La fecha final no es válida.")
        elif start_date > end_date:
            messages.error(request, "La fecha inicial no puede ser mayor que la final.")
        else:
            sales_queryset = Sale.objects.filter(
                business=business,
                created_at__date__gte=start_date,
                created_at__date__lte=end_date,
            ).exclude(status=Sale.Status.CANCELLED)
            sales_total = sales_queryset.aggregate(total=Sum("total"), count=Count("id"))
            low_stock_count = Product.objects.filter(
                business=business,
                is_active=True,
                stock_quantity__lte=models_min_stock(),
            ).count()
            credit_balance = CreditAccount.objects.filter(business=business).aggregate(total=Sum("balance"))["total"] or Decimal("0")
            sales = sales_queryset.select_related("customer", "created_by").order_by("-created_at")[:100]

    return render(
        request,
        "erp/dashboard.html",
        {
            "overview_requested": overview_requested,
            "start_date": start_date_value,
            "end_date": end_date_value,
            "sales_total": sales_total,
            "low_stock_count": low_stock_count,
            "credit_balance": credit_balance,
            "sales": sales,
        },
    )


def models_min_stock():
    from django.db.models import F

    return F("min_stock")


def specialized_sales_queryset(business):
    return (
        business_sales_queryset(business)
        .filter(origin=Sale.Origin.SPECIALIZED)
    )


def business_sales_queryset(business):
    return (
        Sale.objects.filter(business=business)
        .select_related("customer", "created_by")
        .prefetch_related("items", "payments")
        .order_by("-created_at")
    )


def build_sale_cart_state(raw_cart_json, business):
    try:
        raw_items = json.loads(raw_cart_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        raw_items = []

    if not isinstance(raw_items, list):
        raw_items = []

    product_ids = []
    for item in raw_items:
        try:
            product_ids.append(int(item.get("product_id")))
        except (TypeError, ValueError, AttributeError):
            continue

    products = Product.objects.filter(business=business, pk__in=product_ids)
    product_map = {product.id: product for product in products}
    initial_cart_items = []
    persisted_cart_items = []

    for item in raw_items:
        try:
            product_id = int(item.get("product_id"))
            quantity = float(item.get("quantity") or 0)
        except (TypeError, ValueError, AttributeError):
            continue

        if quantity <= 0:
            continue

        product = product_map.get(product_id)
        if product is None:
            continue

        try:
            unit_price = float(item.get("unit_price") or product.sale_price)
        except (TypeError, ValueError):
            unit_price = float(product.sale_price)

        try:
            discount_amount = max(0.0, float(item.get("discount_amount") or 0))
        except (TypeError, ValueError):
            discount_amount = 0.0

        initial_cart_items.append(
            {
                "product_id": product.id,
                "name": product.name,
                "barcode": product.barcode,
                "sku": product.sku,
                "price": unit_price,
                "stock": float(product.stock_quantity),
                "quantity": quantity,
                "discount": discount_amount,
            }
        )
        persisted_cart_items.append(
            {
                "product_id": product.id,
                "quantity": quantity,
                "unit_price": unit_price,
                "discount_amount": discount_amount,
            }
        )

    return initial_cart_items, json.dumps(persisted_cart_items)


@tenant_required
@module_access_required("can_access_pos_effective")
def pos(request):
    business = request.business
    if request.method == "POST":
        try:
            cart = json.loads(request.POST.get("cart_json", "[]"))
            sale = create_pos_sale(
                business=business,
                user=request.user,
                items=cart,
                payment_method=request.POST.get("payment_method", SalePayment.Method.CASH),
                customer_id=request.POST.get("customer_id") or None,
                tendered_amount=request.POST.get("tendered_amount"),
                request_nonce=request.POST.get("request_nonce"),
            )
            if sale.has_manual_price_override:
                messages.success(request, f"Venta {sale.folio} registrada por ${sale.total}. Se aplicó precio manual en una o más líneas.")
            else:
                messages.success(request, f"Venta {sale.folio} registrada por ${sale.total}.")
            return redirect(f"{reverse('pos')}?sale_saved=1&last_sale={sale.id}")
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))

    query = request.GET.get("q", "").strip()
    last_sale = None
    last_sale_id = request.GET.get("last_sale")
    if last_sale_id:
        last_sale = (
            business_sales_queryset(business)
            .filter(origin=Sale.Origin.POS, pk=last_sale_id)
            .first()
        )
    products = Product.objects.none()
    if query:
        products = (
            Product.objects.filter(business=business, is_active=True)
            .filter(Q(name__icontains=query) | Q(barcode__icontains=query) | Q(sku__icontains=query))
            .order_by("name")[:80]
        )
    return render(
        request,
        "erp/pos.html",
        {
            "products": products,
            "cash_session": current_cash_session(business, request.user),
            "request_nonce": secrets.token_urlsafe(24),
            "query": query,
            "sale_saved": request.GET.get("sale_saved") == "1",
            "last_sale": last_sale,
        },
    )


@tenant_required
@module_access_required("can_access_inventory_effective")
def inventory(request):
    business = request.business
    if request.method == "POST":
        form = ProductForm(request.POST, business=business)
        if form.is_valid():
            product = form.save()
            if product.stock_quantity:
                InventoryMovement.objects.create(
                    business=business,
                    product=product,
                    movement_type=InventoryMovement.Type.INITIAL,
                    quantity=product.stock_quantity,
                    stock_after=product.stock_quantity,
                    note="Alta inicial",
                    created_by=request.user,
                )
            messages.success(request, "Producto guardado.")
            return redirect("inventory")
    else:
        form = ProductForm(business=business)

    query = request.GET.get("q", "").strip()
    products = Product.objects.none()
    if query:
        products = (
            Product.objects.filter(business=business)
            .select_related("category")
            .filter(Q(name__icontains=query) | Q(barcode__icontains=query) | Q(sku__icontains=query))
            .order_by("name")[:300]
        )
    return render(request, "erp/inventory.html", {"form": form, "products": products, "query": query})


@tenant_required
@module_access_required("can_access_sales_effective")
def clients(request):
    business = request.business
    editing_customer = None

    def resolve_editing_customer():
        customer_id = (request.POST.get("customer_id") or request.GET.get("edit") or "").strip()
        if not customer_id:
            return None
        return get_object_or_404(Customer, pk=customer_id, business=business)

    if request.method == "POST":
        editing_customer = resolve_editing_customer()
        form = CustomerForm(request.POST, business=business, instance=editing_customer)
        if form.is_valid():
            customer = form.save()
            messages.success(request, f"Cliente {customer.name} guardado.")
            return redirect("clients")
    else:
        editing_customer = resolve_editing_customer()
        form = CustomerForm(business=business, instance=editing_customer)

    query = request.GET.get("q", "").strip()
    customers = Customer.objects.none()
    if query:
        customers = (
            Customer.objects.filter(business=business)
            .select_related("credit_account")
            .filter(
                Q(name__icontains=query)
                | Q(contact_name__icontains=query)
                | Q(phone__icontains=query)
                | Q(email__icontains=query)
                | Q(tax_id__icontains=query)
            )
            .order_by("name")[:300]
        )
    elif editing_customer:
        customers = Customer.objects.filter(business=business, pk=editing_customer.pk).select_related("credit_account")
    return render(
        request,
        "erp/clients.html",
        {
            "form": form,
            "customers": customers,
            "editing_customer": editing_customer,
            "query": query,
        },
    )


@tenant_required
@module_access_required("can_access_sales_effective")
def sales(request):
    business = request.business
    search_query = request.GET.get("q", "").strip()
    legacy_folio_query = request.GET.get("folio", "").strip()
    legacy_customer_query = request.GET.get("cliente", "").strip()
    status_filter = request.GET.get("estado", "").strip()
    method_filter = request.GET.get("metodo", "").strip()
    sort = request.GET.get("sort", "fecha").strip().lower()
    sort_dir = request.GET.get("dir", "desc").strip().lower()

    status_choices = Sale.Status.choices
    method_choices = SalePayment.Method.choices
    valid_statuses = {value for value, _label in status_choices}
    valid_methods = {value for value, _label in method_choices}
    if status_filter not in valid_statuses:
        status_filter = ""
    if method_filter not in valid_methods:
        method_filter = ""

    sort_field_map = {
        "folio": "id",
        "cliente": "customer__name",
        "estado": "status",
        "metodo": "payments__method",
        "total": "total",
        "fecha": "created_at",
    }
    if sort not in sort_field_map:
        sort = "fecha"
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"

    has_filters = bool(search_query or legacy_folio_query or legacy_customer_query or status_filter or method_filter)
    sales_list = Sale.objects.none()
    if has_filters:
        queryset = specialized_sales_queryset(business)
        if search_query:
            filters = (
                Q(customer__name__icontains=search_query)
            )
            if search_query.upper().startswith("VT-"):
                numeric_part = search_query.split("-", 1)[1].lstrip("0")
                if numeric_part.isdigit():
                    filters |= Q(id=int(numeric_part))
            elif search_query.isdigit():
                filters |= Q(id=int(search_query))
            queryset = queryset.filter(filters)
        else:
            if legacy_folio_query:
                folio_digits = "".join(character for character in legacy_folio_query if character.isdigit())
                if folio_digits:
                    queryset = queryset.filter(id=int(folio_digits))
                else:
                    queryset = queryset.none()
            if legacy_customer_query:
                queryset = queryset.filter(customer__name__icontains=legacy_customer_query)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if method_filter:
            queryset = queryset.filter(payments__method=method_filter)

        order_field = sort_field_map[sort]
        if sort_dir == "desc":
            order_field = f"-{order_field}"
        sales_list = queryset.order_by(order_field, "-id").distinct()[:80]

    def next_sort_dir(column):
        if sort == column and sort_dir == "asc":
            return "desc"
        return "asc"

    return render(
        request,
        "erp/sales.html",
        {
            "sales": sales_list,
            "has_filters": has_filters,
            "search_query": search_query,
            "status_filter": status_filter,
            "method_filter": method_filter,
            "status_choices": status_choices,
            "method_choices": method_choices,
            "sort": sort,
            "sort_dir": sort_dir,
            "next_dir_folio": next_sort_dir("folio"),
            "next_dir_cliente": next_sort_dir("cliente"),
            "next_dir_estado": next_sort_dir("estado"),
            "next_dir_metodo": next_sort_dir("metodo"),
            "next_dir_total": next_sort_dir("total"),
            "next_dir_fecha": next_sort_dir("fecha"),
        },
    )


@tenant_required
@module_access_required("can_access_sales_effective")
def new_sale(request):
    business = request.business
    query = request.GET.get("q", "").strip()
    request_data = request.POST if request.method == "POST" else request.GET
    sale_state = {
        "customer_id": (request_data.get("customer_id") or "").strip(),
        "customer_name": (request_data.get("customer_name") or "").strip(),
        "shipping_address": (request_data.get("shipping_address") or "").strip(),
        "payment_method": (request_data.get("payment_method") or SalePayment.Method.CASH).strip(),
        "notes": (request_data.get("notes") or "").strip(),
        "discount_total": (request_data.get("discount_total") or "0.00").strip() or "0.00",
    }
    initial_cart_items, sale_state["cart_json"] = build_sale_cart_state(request_data.get("cart_json", "[]"), business)

    valid_payment_methods = {value for value, _label in SalePayment.Method.choices}
    if sale_state["payment_method"] not in valid_payment_methods:
        sale_state["payment_method"] = SalePayment.Method.CASH

    products = Product.objects.none()
    if query:
        products = (
            Product.objects.filter(business=business, is_active=True)
            .filter(Q(name__icontains=query) | Q(barcode__icontains=query) | Q(sku__icontains=query))
            .order_by("name")[:80]
        )
    customers = Customer.objects.filter(business=business, is_active=True).order_by("name")

    if request.method == "POST":
        form = SpecializedSaleForm(request.POST, business=business)
        if form.is_valid():
            try:
                cart = json.loads(request.POST.get("cart_json", "[]"))
                sale = create_specialized_sale(
                    business=business,
                    user=request.user,
                    customer_id=form.cleaned_data["customer_id"].id,
                    items=cart,
                    payment_method=form.cleaned_data["payment_method"],
                    discount_total=form.cleaned_data["discount_total"],
                    shipping_address=form.cleaned_data["shipping_address"],
                    customer_note=form.cleaned_data["notes"],
                    internal_note="",
                )
                messages.success(request, f"Venta {sale.folio} registrada por ${sale.total}.")
                return redirect("sale_detail", sale_id=sale.id)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
    else:
        form = SpecializedSaleForm(
            business=business,
            initial={
                "customer_id": sale_state["customer_id"] or None,
                "payment_method": sale_state["payment_method"],
                "shipping_address": sale_state["shipping_address"],
                "discount_total": sale_state["discount_total"],
                "notes": sale_state["notes"],
            },
        )

    return render(
        request,
        "erp/sale_form.html",
        {
            "form": form,
            "products": products,
            "customers": customers,
            "query": query,
            "sale_state": sale_state,
            "initial_cart_items": initial_cart_items,
        },
    )


@tenant_required
@module_access_required("can_access_sales_effective")
def sale_detail(request, sale_id):
    sale = get_object_or_404(
        business_sales_queryset(request.business),
        pk=sale_id,
    )
    return render(request, "erp/sale_detail.html", {"sale": sale})


@tenant_required
@module_access_required("can_access_sales_effective")
def sale_note(request, sale_id):
    sale = get_object_or_404(
        business_sales_queryset(request.business),
        pk=sale_id,
    )
    return render(
        request,
        "erp/sale_document.html",
        {
            "sale": sale,
            "document_title": "Nota de venta",
            "document_code": "",
            "document_legend": "Documento interno para control comercial.",
        },
    )


@tenant_required
@module_access_required("can_access_pos_effective")
def pos_sale_note(request, sale_id):
    sale = get_object_or_404(
        business_sales_queryset(request.business).filter(origin=Sale.Origin.POS),
        pk=sale_id,
    )
    return render(
        request,
        "erp/sale_document.html",
        {
            "sale": sale,
            "document_title": "Ticket de venta",
            "document_code": "",
            "document_legend": "Comprobante de mostrador.",
        },
    )


@tenant_required
@module_access_required("can_access_sales_effective")
@require_POST
def cancel_sale_view(request, sale_id):
    sale = get_object_or_404(
        business_sales_queryset(request.business),
        pk=sale_id,
    )
    try:
        cancel_sale(sale, request.user, request.POST.get("cancel_reason", ""))
        messages.success(request, f"Venta {sale.folio} cancelada.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
    return redirect("sale_detail", sale_id=sale.id)


@tenant_required
@module_access_required("can_access_inventory_effective")
@require_POST
def adjust_inventory_view(request):
    product = get_object_or_404(Product, pk=request.POST.get("product_id"), business=request.business)
    try:
        adjust_inventory(
            product=product,
            quantity_delta=request.POST.get("quantity_delta", "0"),
            movement_type=InventoryMovement.Type.ADJUSTMENT,
            user=request.user,
            note=request.POST.get("note", ""),
        )
        messages.success(request, "Inventario ajustado.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("inventory")


@tenant_required
@module_access_required("can_access_credits_effective")
def credits(request):
    business = request.business
    if request.method == "POST":
        form = CustomerForm(request.POST, business=business)
        if form.is_valid():
            form.save()
            messages.success(request, "Cliente guardado.")
            return redirect("credits")
    else:
        form = CustomerForm(business=business)

    query = request.GET.get("q", "").strip()
    accounts = CreditAccount.objects.none()
    if query:
        accounts = (
            CreditAccount.objects.filter(business=business)
            .select_related("customer")
            .filter(
                Q(customer__name__icontains=query)
                | Q(customer__contact_name__icontains=query)
                | Q(customer__phone__icontains=query)
                | Q(customer__email__icontains=query)
                | Q(customer__tax_id__icontains=query)
            )
            .order_by("customer__name")[:300]
        )
    return render(request, "erp/credits.html", {"form": form, "accounts": accounts, "query": query})


@tenant_required
@module_access_required("can_access_credits_effective")
@require_POST
def credit_payment(request):
    account = get_object_or_404(CreditAccount, pk=request.POST.get("account_id"), business=request.business)
    try:
        record_credit_payment(
            account=account,
            amount=request.POST.get("amount", "0"),
            method=request.POST.get("method", "cash"),
            user=request.user,
            note=request.POST.get("note", ""),
        )
        messages.success(request, "Abono registrado.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("credits")


@tenant_required
@module_access_required("can_access_cash_effective")
def cash(request):
    session = current_cash_session(request.business, request.user)
    totals = cash_session_totals(session) if session else {}
    out_of_session = out_of_session_cash_summary(request.business, request.user)
    recent_sessions = CashSession.objects.filter(business=request.business, opened_by=request.user)[:10]
    method_choices = [
        (value, label)
        for value, label in SalePayment.Method.choices
        if value != SalePayment.Method.CREDIT
    ]
    return render(
        request,
        "erp/cash.html",
        {
            "cash_session": session,
            "totals": totals,
            "out_of_session": out_of_session,
            "recent_sessions": recent_sessions,
            "manual_movement_choices": [
                (CashMovement.Type.MANUAL_IN, "Entrada"),
                (CashMovement.Type.MANUAL_OUT, "Salida"),
            ],
            "method_choices": method_choices,
        },
    )


@tenant_required
@require_POST
def open_cash(request):
    if not can_manage_cash_from_request(request):
        messages.error(request, "No tienes acceso a este módulo.")
        return redirect("dashboard")
    try:
        open_cash_session(request.business, request.user, request.POST.get("opening_amount", "0"))
        messages.success(request, "Caja abierta.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    next_url = request.POST.get("next") or reverse("cash")
    if next_url not in {reverse("cash"), reverse("pos")}:
        next_url = reverse("cash")
    return redirect(next_url)


@tenant_required
@require_POST
def close_cash(request):
    if not can_manage_cash_from_request(request):
        messages.error(request, "No tienes acceso a este módulo.")
        return redirect("dashboard")

    session = current_cash_session(request.business, request.user)
    next_url = request.POST.get("next") or reverse("cash")
    if next_url not in {reverse("cash"), reverse("pos")}:
        next_url = reverse("cash")

    if not session:
        messages.error(request, "No hay caja abierta.")
        return redirect(next_url)

    try:
        close_cash_session(session, request.user, request.POST.get("closing_amount", "0"))
        messages.success(request, "Caja cerrada.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect(next_url)


@tenant_required
@module_access_required("can_access_cash_effective")
@require_POST
def cash_movement(request):
    try:
        record_manual_cash_movement(
            request.business,
            request.user,
            request.POST.get("movement_type", CashMovement.Type.MANUAL_IN),
            request.POST.get("amount", "0"),
            method=request.POST.get("method", SalePayment.Method.CASH),
            note=request.POST.get("note", ""),
        )
        messages.success(request, "Movimiento de caja registrado.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
    return redirect("cash")


@tenant_required
@module_access_required("can_access_reports_effective")
def reports(request):
    messages.info(request, "Reportes ahora está integrado al dashboard.")
    return redirect("dashboard")


@tenant_required
def settings_view(request):
    can_manage_users = request.membership.can_manage_users
    if not can_manage_users:
        return redirect(preferred_landing_url_name(request.membership))

    memberships = request.business.memberships.select_related("user", "user__password_security").order_by("-is_owner", "-is_admin", "user__username")
    create_user_form = TenantUserForm(business=request.business, prefix="create")
    membership_form = None
    action = ""

    if request.method == "POST":
        if not can_manage_users:
            messages.error(request, "No tienes permiso para administrar usuarios.")
            return redirect("settings")

        action = request.POST.get("action", "")
        if action == "create_user":
            create_user_form = TenantUserForm(request.POST, business=request.business, prefix="create")
            if create_user_form.is_valid():
                membership = create_user_form.save()
                messages.success(request, f"Usuario {membership.user.username} agregado al negocio.")
                return redirect("settings")
            first_non_field_error = next(iter(create_user_form.non_field_errors()), None)
            if first_non_field_error:
                messages.error(request, first_non_field_error)
            else:
                for field_errors in create_user_form.errors.values():
                    if field_errors:
                        messages.error(request, field_errors[0])
                        break
        elif action == "expire_password":
            membership = get_object_or_404(
                BusinessMembership.objects.select_related("user", "user__password_security"),
                pk=request.POST.get("membership_id"),
                business=request.business,
            )
            user_security, _created = UserSecurity.objects.get_or_create(user=membership.user)
            user_security.expire_password()
            AuditLog.objects.create(
                business=request.business,
                actor=request.user,
                action="user.password_expired",
                object_type="User",
                object_id=str(membership.user.id),
                detail={
                    "membership_id": membership.id,
                    "username": membership.user.username,
                },
            )
            messages.success(request, f"La contraseña de {membership.user.username} fue caducada.")
            return redirect("settings")
        elif action == "update_membership":
            membership = get_object_or_404(
                BusinessMembership.objects.select_related("user"),
                pk=request.POST.get("membership_id"),
                business=request.business,
            )
            if membership.is_owner:
                messages.error(request, "El usuario propietario no se edita desde esta pantalla.")
                return redirect("settings")

            membership_form = MembershipAccessForm(
                request.POST,
                instance=membership,
                prefix=f"member-{membership.id}",
                acting_membership=request.membership,
            )
            if membership_form.is_valid():
                updated_membership = membership_form.save()
                messages.success(request, f"Accesos actualizados para {updated_membership.user.username}.")
                return redirect("settings")
        else:
            messages.error(request, "Acción no válida.")
            return redirect("settings")

    membership_rows = build_membership_rows(
        request,
        memberships,
        can_manage_users,
        bound_form=membership_form if action == "update_membership" else None,
    )
    return render(
        request,
        "erp/settings.html",
        {
            "can_manage_users": can_manage_users,
            "create_user_form": create_user_form,
            "membership_rows": membership_rows,
        },
    )


@user_passes_test(is_provider)
def provider_dashboard(request):
    today = timezone.localdate()
    totals = {
        "businesses": Business.objects.count(),
        "active": Business.objects.filter(status=Business.Status.ACTIVE).count(),
        "past_due": Business.objects.filter(status=Business.Status.PAST_DUE).count(),
        "suspended": Business.objects.filter(status=Business.Status.SUSPENDED).count(),
        "monthly_payments": ProviderPayment.objects.filter(paid_at__year=today.year, paid_at__month=today.month).aggregate(total=Sum("amount"))["total"] or Decimal("0"),
    }
    recent_businesses = Business.objects.select_related("plan").order_by("-created_at")[:8]
    return render(request, "provider/dashboard.html", {"totals": totals, "recent_businesses": recent_businesses})


def provider_audit(request, business, action, object_type, object_id="", detail=None):
    AuditLog.objects.create(
        business=business,
        actor=request.user,
        action=action,
        object_type=object_type,
        object_id=str(object_id or ""),
        detail=detail or {},
    )


@user_passes_test(is_provider)
def provider_businesses(request):
    if not Plan.objects.exists():
        Plan.objects.create(name="Basico", monthly_price=Decimal("399.00"), max_users=5, max_products=1000)

    if request.method == "POST":
        action = request.POST.get("action", "create")
        if action == "create":
            form = ProviderBusinessForm(request.POST)
            if form.is_valid():
                business, user = form.save(created_by=request.user)
                provider_audit(
                    request,
                    business,
                    "provider.business_created",
                    "Business",
                    business.id,
                    {"owner_user_id": user.id, "owner_username": user.username},
                )
                messages.success(request, f"Cliente {business.name} creado con usuario {user.username}.")
                return redirect("provider_business_detail", business_id=business.id)
        elif action == "update":
            business = get_object_or_404(Business, pk=request.POST.get("business_id"))
            business.status = request.POST.get("status", business.status)
            business.plan_id = request.POST.get("plan_id") or None
            business.service_expires_at = parse_date(request.POST.get("service_expires_at", "")) or None
            business.notes = request.POST.get("notes", "")
            business.save(update_fields=["status", "plan", "service_expires_at", "notes", "updated_at"])
            provider_audit(
                request,
                business,
                "provider.business_account_updated",
                "Business",
                business.id,
                {
                    "status": business.status,
                    "plan_id": business.plan_id,
                    "service_expires_at": str(business.service_expires_at or ""),
                },
            )
            messages.success(request, f"Cuenta de {business.name} actualizada.")
            return redirect("provider_businesses")
        elif action == "payment":
            business = get_object_or_404(Business, pk=request.POST.get("business_id"))
            try:
                amount = Decimal(request.POST.get("amount") or "0").quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                amount = Decimal("0.00")
            if amount <= 0:
                messages.error(request, "El pago debe ser mayor a cero.")
            else:
                payment = ProviderPayment.objects.create(
                    business=business,
                    amount=amount,
                    method=request.POST.get("method", ""),
                    note=request.POST.get("note", ""),
                    created_by=request.user,
                )
                provider_audit(
                    request,
                    business,
                    "provider.payment_created",
                    "ProviderPayment",
                    payment.id,
                    {"amount": str(payment.amount), "method": payment.method, "paid_at": str(payment.paid_at)},
                )
                messages.success(request, f"Pago registrado para {business.name}.")
            return redirect("provider_businesses")
    else:
        form = ProviderBusinessForm()

    businesses = list(
        Business.objects.select_related("plan")
        .annotate(user_count=Count("memberships", distinct=True))
        .order_by("name")
    )
    payment_totals = {
        row["business_id"]: row["total"]
        for row in ProviderPayment.objects.filter(business__in=businesses)
        .values("business_id")
        .annotate(total=Sum("amount"))
    }
    for business in businesses:
        business.payment_total = payment_totals.get(business.id, Decimal("0.00"))
    plans = Plan.objects.filter(is_active=True).order_by("monthly_price", "name")
    return render(
        request,
        "provider/businesses.html",
        {
            "form": form,
            "businesses": businesses,
            "plans": plans,
            "status_choices": Business.Status.choices,
        },
    )


@user_passes_test(is_provider)
def provider_business_detail(request, business_id):
    business = get_object_or_404(Business.objects.select_related("plan"), pk=business_id)
    business_form = ProviderBusinessUpdateForm(instance=business, prefix="business")
    payment_form = ProviderPaymentForm(prefix="payment")
    create_user_form = TenantUserForm(business=business, prefix="create")
    membership_form = None
    action = ""

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "update_business":
            business_form = ProviderBusinessUpdateForm(request.POST, instance=business, prefix="business")
            if business_form.is_valid():
                updated_business = business_form.save()
                provider_audit(
                    request,
                    updated_business,
                    "provider.business_updated",
                    "Business",
                    updated_business.id,
                    {
                        "status": updated_business.status,
                        "plan_id": updated_business.plan_id,
                        "service_expires_at": str(updated_business.service_expires_at or ""),
                    },
                )
                messages.success(request, f"Datos de {updated_business.name} actualizados.")
                return redirect("provider_business_detail", business_id=updated_business.id)
        elif action == "payment":
            payment_form = ProviderPaymentForm(request.POST, prefix="payment")
            if payment_form.is_valid():
                payment = payment_form.save(commit=False)
                payment.business = business
                payment.created_by = request.user
                payment.save()
                provider_audit(
                    request,
                    business,
                    "provider.payment_created",
                    "ProviderPayment",
                    payment.id,
                    {"amount": str(payment.amount), "method": payment.method, "paid_at": str(payment.paid_at)},
                )
                messages.success(request, f"Pago registrado para {business.name}.")
                return redirect("provider_business_detail", business_id=business.id)
        elif action == "create_user":
            create_user_form = TenantUserForm(request.POST, business=business, prefix="create")
            if create_user_form.is_valid():
                membership = create_user_form.save()
                provider_audit(
                    request,
                    business,
                    "provider.user_created",
                    "BusinessMembership",
                    membership.id,
                    {"user_id": membership.user_id, "username": membership.user.username},
                )
                messages.success(request, f"Usuario {membership.user.username} agregado a {business.name}.")
                return redirect("provider_business_detail", business_id=business.id)
        elif action == "expire_password":
            membership = get_object_or_404(
                BusinessMembership.objects.select_related("user", "user__password_security"),
                pk=request.POST.get("membership_id"),
                business=business,
            )
            user_security, _created = UserSecurity.objects.get_or_create(user=membership.user)
            user_security.expire_password()
            provider_audit(
                request,
                business,
                "provider.user_password_expired",
                "User",
                membership.user_id,
                {"membership_id": membership.id, "username": membership.user.username},
            )
            messages.success(request, f"La contraseña de {membership.user.username} fue caducada.")
            return redirect("provider_business_detail", business_id=business.id)
        elif action == "update_membership":
            membership = get_object_or_404(
                BusinessMembership.objects.select_related("user"),
                pk=request.POST.get("membership_id"),
                business=business,
            )
            if membership.is_owner:
                messages.error(request, "El usuario propietario no se edita desde Administración.")
                return redirect("provider_business_detail", business_id=business.id)

            membership_form = MembershipAccessForm(
                request.POST,
                instance=membership,
                prefix=f"member-{membership.id}",
                acting_membership=None,
            )
            if membership_form.is_valid():
                updated_membership = membership_form.save()
                provider_audit(
                    request,
                    business,
                    "provider.membership_updated",
                    "BusinessMembership",
                    updated_membership.id,
                    {"user_id": updated_membership.user_id, "username": updated_membership.user.username},
                )
                messages.success(request, f"Accesos actualizados para {updated_membership.user.username}.")
                return redirect("provider_business_detail", business_id=business.id)
        else:
            messages.error(request, "Acción no válida.")
            return redirect("provider_business_detail", business_id=business.id)

    memberships = business.memberships.select_related("user", "user__password_security").order_by("-is_owner", "-is_admin", "user__username")
    membership_rows = build_membership_rows(
        request,
        memberships,
        True,
        bound_form=membership_form if action == "update_membership" else None,
    )
    payments = business.provider_payments.select_related("created_by")[:10]

    return render(
        request,
        "provider/business_detail.html",
        {
            "business": business,
            "business_form": business_form,
            "payment_form": payment_form,
            "create_user_form": create_user_form,
            "membership_rows": membership_rows,
            "payments": payments,
        },
    )
