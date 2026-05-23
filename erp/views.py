import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import CustomerForm, ProductForm, ProviderBusinessForm
from .models import (
    Business,
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


@login_required
def no_business(request):
    return render(request, "erp/no_business.html")


@tenant_required
def dashboard(request):
    business = request.business
    today = timezone.localdate()
    today_sales = Sale.objects.filter(business=business, created_at__date=today).aggregate(total=Sum("total"), count=Count("id"))
    low_stock_count = Product.objects.filter(business=business, is_active=True, stock_quantity__lte=models_min_stock()).count()
    credit_balance = CreditAccount.objects.filter(business=business).aggregate(total=Sum("balance"))["total"] or Decimal("0")
    recent_sales = Sale.objects.filter(business=business).select_related("customer", "created_by")[:8]
    return render(
        request,
        "erp/dashboard.html",
        {
            "today_sales": today_sales,
            "low_stock_count": low_stock_count,
            "credit_balance": credit_balance,
            "recent_sales": recent_sales,
        },
    )


def models_min_stock():
    from django.db.models import F

    return F("min_stock")


@tenant_required
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
            messages.success(request, f"Venta #{sale.id} registrada por ${sale.total}.")
            return redirect("pos")
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))

    products = Product.objects.filter(business=business, is_active=True).order_by("name")[:500]
    customers = Customer.objects.filter(business=business, is_active=True).order_by("name")
    return render(
        request,
        "erp/pos.html",
        {
            "products": products,
            "customers": customers,
            "cash_session": current_cash_session(business, request.user),
        },
    )


@tenant_required
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
    products = Product.objects.filter(business=business).select_related("category")
    if query:
        products = products.filter(name__icontains=query) | Product.objects.filter(business=business, barcode__icontains=query)
    return render(request, "erp/inventory.html", {"form": form, "products": products.order_by("name")[:300], "query": query})


@tenant_required
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
def cash(request):
    session = current_cash_session(request.business, request.user)
    totals = cash_session_totals(session) if session else {}
    recent_sessions = CashSession.objects.filter(business=request.business, opened_by=request.user)[:10]
    return render(request, "erp/cash.html", {"cash_session": session, "totals": totals, "recent_sessions": recent_sessions})


@tenant_required
@require_POST
def open_cash(request):
    try:
        open_cash_session(request.business, request.user, request.POST.get("opening_amount", "0"))
        messages.success(request, "Caja abierta.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("cash")


@tenant_required
@require_POST
def close_cash(request):
    session = current_cash_session(request.business, request.user)
    if not session:
        messages.error(request, "No hay caja abierta.")
        return redirect("cash")
    close_cash_session(session, request.user, request.POST.get("closing_amount", "0"))
    messages.success(request, "Caja cerrada.")
    return redirect("cash")


@tenant_required
def reports(request):
    business = request.business
    sales_total = Sale.objects.filter(business=business).aggregate(total=Sum("total"), count=Count("id"))
    payments = SalePayment.objects.filter(business=business).values("method").annotate(total=Sum("amount")).order_by("method")
    return render(request, "erp/reports.html", {"sales_total": sales_total, "payments": payments})


@tenant_required
def settings_view(request):
    return render(request, "erp/settings.html")


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
