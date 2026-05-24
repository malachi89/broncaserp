import json
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import CustomerForm, MembershipAccessForm, ProductForm, ProviderBusinessForm, SpecializedSaleForm, TenantUserForm
from .models import (
    Business,
    BusinessMembership,
    CashSession,
    CreditAccount,
    Customer,
    InventoryMovement,
    Plan,
    Product,
    ProviderPayment,
    Sale,
    SalePayment,
)
from .services import (
    adjust_inventory,
    cash_session_totals,
    close_cash_session,
    create_pos_sale,
    create_specialized_sale,
    current_cash_session,
    open_cash_session,
    record_credit_payment,
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


def build_membership_rows(request, memberships, can_manage_users, bound_form=None):
    rows = []
    for membership in memberships:
        form = None
        if can_manage_users and not membership.is_owner:
            if bound_form is not None and bound_form.instance.pk == membership.pk:
                form = bound_form
            else:
                form = MembershipAccessForm(
                    instance=membership,
                    prefix=f"member-{membership.id}",
                    acting_membership=request.membership,
                )
        rows.append(
            {
                "membership": membership,
                "form": form,
                "editable": form is not None,
            }
        )
    return rows


@login_required
def no_business(request):
    return render(request, "erp/no_business.html")


@tenant_required
def dashboard(request):
    business = request.business
    today = timezone.localdate()
    suggested_start = today.replace(day=1)
    panel = request.GET.get("panel", "").strip()
    overview_requested = panel == "overview"
    start_date_value = request.GET.get("start_date") or str(suggested_start)
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
            )
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
        Sale.objects.filter(business=business, origin=Sale.Origin.SPECIALIZED)
        .select_related("customer", "created_by")
        .prefetch_related("items", "payments")
        .order_by("-created_at")
    )


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
            )
            if sale.has_manual_price_override:
                messages.success(request, f"Venta #{sale.id} registrada por ${sale.total}. Se aplicó precio manual en una o más líneas.")
            else:
                messages.success(request, f"Venta #{sale.id} registrada por ${sale.total}.")
            return redirect("pos")
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))

    query = request.GET.get("q", "").strip()
    products = Product.objects.none()
    if query:
        products = (
            Product.objects.filter(business=business, is_active=True)
            .filter(Q(name__icontains=query) | Q(barcode__icontains=query) | Q(sku__icontains=query))
            .order_by("name")[:80]
        )
    customers = Customer.objects.filter(business=business, is_active=True).order_by("name")
    return render(
        request,
        "erp/pos.html",
        {
            "products": products,
            "customers": customers,
            "cash_session": current_cash_session(business, request.user),
            "query": query,
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
    query = request.GET.get("q", "").strip()
    sales_list = Sale.objects.none()
    if query:
        filters = (
            Q(customer__name__icontains=query)
            | Q(status__icontains=query)
            | Q(payments__method__icontains=query)
        )
        if query.upper().startswith("VT-"):
            numeric_part = query.split("-", 1)[1].lstrip("0")
            if numeric_part.isdigit():
                filters |= Q(id=int(numeric_part))
        elif query.isdigit():
            filters |= Q(id=int(query))

        sales_list = specialized_sales_queryset(business).filter(filters).distinct()[:80]
    return render(request, "erp/sales.html", {"sales": sales_list, "query": query})


@tenant_required
@module_access_required("can_access_sales_effective")
def new_sale(request):
    business = request.business
    query = request.GET.get("q", "").strip()
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
                    customer_note=form.cleaned_data["customer_note"],
                    internal_note=form.cleaned_data["internal_note"],
                )
                messages.success(request, f"Venta {sale.folio} registrada por ${sale.total}.")
                return redirect("sale_detail", sale_id=sale.id)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
    else:
        form = SpecializedSaleForm(business=business)

    return render(
        request,
        "erp/sale_form.html",
        {
            "form": form,
            "products": products,
            "customers": customers,
            "query": query,
        },
    )


@tenant_required
@module_access_required("can_access_sales_effective")
def sale_detail(request, sale_id):
    sale = get_object_or_404(
        specialized_sales_queryset(request.business),
        pk=sale_id,
    )
    return render(request, "erp/sale_detail.html", {"sale": sale})


@tenant_required
@module_access_required("can_access_sales_effective")
def sale_note(request, sale_id):
    sale = get_object_or_404(
        specialized_sales_queryset(request.business),
        pk=sale_id,
    )
    return render(
        request,
        "erp/sale_document.html",
        {
            "sale": sale,
            "document_title": "Nota de venta",
            "document_code": "NV",
            "document_legend": "Documento interno para control comercial.",
        },
    )


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

    accounts = CreditAccount.objects.filter(business=business).select_related("customer").order_by("customer__name")
    return render(request, "erp/credits.html", {"form": form, "accounts": accounts})


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
    recent_sessions = CashSession.objects.filter(business=request.business, opened_by=request.user)[:10]
    return render(request, "erp/cash.html", {"cash_session": session, "totals": totals, "recent_sessions": recent_sessions})


@tenant_required
@module_access_required("can_access_cash_effective")
@require_POST
def open_cash(request):
    try:
        open_cash_session(request.business, request.user, request.POST.get("opening_amount", "0"))
        messages.success(request, "Caja abierta.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("cash")


@tenant_required
@module_access_required("can_access_cash_effective")
@require_POST
def close_cash(request):
    session = current_cash_session(request.business, request.user)
    if not session:
        messages.error(request, "No hay caja abierta.")
        return redirect("cash")
    try:
        close_cash_session(session, request.user, request.POST.get("closing_amount", "0"))
        messages.success(request, "Caja cerrada.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("cash")


@tenant_required
@module_access_required("can_access_reports_effective")
def reports(request):
    messages.info(request, "Reportes ahora está integrado al dashboard.")
    return redirect("dashboard")


@tenant_required
def settings_view(request):
    can_manage_users = request.membership.can_manage_users
    memberships = request.business.memberships.select_related("user").order_by("-is_owner", "-is_admin", "user__username")
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
    totals = {
        "businesses": Business.objects.count(),
        "active": Business.objects.filter(status=Business.Status.ACTIVE).count(),
        "past_due": Business.objects.filter(status=Business.Status.PAST_DUE).count(),
        "suspended": Business.objects.filter(status=Business.Status.SUSPENDED).count(),
        "monthly_payments": ProviderPayment.objects.filter(paid_at__month=timezone.localdate().month).aggregate(total=Sum("amount"))["total"] or Decimal("0"),
    }
    recent_businesses = Business.objects.select_related("plan").order_by("-created_at")[:8]
    return render(request, "provider/dashboard.html", {"totals": totals, "recent_businesses": recent_businesses})


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
                messages.success(request, f"Cliente {business.name} creado con usuario {user.username}.")
                return redirect("provider_businesses")
        elif action == "update":
            business = get_object_or_404(Business, pk=request.POST.get("business_id"))
            business.status = request.POST.get("status", business.status)
            business.plan_id = request.POST.get("plan_id") or None
            business.service_expires_at = parse_date(request.POST.get("service_expires_at", "")) or None
            business.notes = request.POST.get("notes", "")
            business.save(update_fields=["status", "plan", "service_expires_at", "notes", "updated_at"])
            messages.success(request, f"Cuenta de {business.name} actualizada.")
            return redirect("provider_businesses")
        elif action == "payment":
            business = get_object_or_404(Business, pk=request.POST.get("business_id"))
            amount = Decimal(request.POST.get("amount") or "0").quantize(Decimal("0.01"))
            if amount <= 0:
                messages.error(request, "El pago debe ser mayor a cero.")
            else:
                ProviderPayment.objects.create(
                    business=business,
                    amount=amount,
                    method=request.POST.get("method", ""),
                    note=request.POST.get("note", ""),
                    created_by=request.user,
                )
                messages.success(request, f"Pago registrado para {business.name}.")
            return redirect("provider_businesses")
    else:
        form = ProviderBusinessForm()

    businesses = Business.objects.select_related("plan").annotate(user_count=Count("memberships")).order_by("name")
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
