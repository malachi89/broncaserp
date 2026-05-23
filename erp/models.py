from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q
from django.utils import timezone


User = get_user_model()


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Plan(TimeStampedModel):
    name = models.CharField(max_length=80, unique=True)
    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_users = models.PositiveIntegerField(default=5)
    max_products = models.PositiveIntegerField(default=1000)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Business(TimeStampedModel):
    class Status(models.TextChoices):
        TRIAL = "trial", "Prueba"
        ACTIVE = "active", "Activo"
        PAST_DUE = "past_due", "Vencido"
        SUSPENDED = "suspended", "Suspendido"

    name = models.CharField(max_length=140)
    slug = models.SlugField(unique=True)
    owner_name = models.CharField(max_length=140, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    tax_id = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TRIAL)
    currency = models.CharField(max_length=3, default="MXN")
    plan = models.ForeignKey(Plan, null=True, blank=True, on_delete=models.SET_NULL)
    service_expires_at = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_expired(self):
        return bool(self.service_expires_at and self.service_expires_at < timezone.localdate())

    @property
    def can_operate(self):
        return self.status in {self.Status.TRIAL, self.Status.ACTIVE} and not self.is_expired


class BusinessMembership(TimeStampedModel):
    class Role(models.TextChoices):
        OWNER = "owner", "Dueño"
        ADMIN = "admin", "Administrador"
        CASHIER = "cashier", "Cajero"
        INVENTORY = "inventory", "Inventario"
        CREDIT_MANAGER = "credit_manager", "Créditos"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="business_memberships")
    role = models.CharField(max_length=30, choices=Role.choices)
    is_owner = models.BooleanField(default=False)
    is_admin = models.BooleanField(default=False)
    can_access_pos = models.BooleanField(default=False)
    can_access_inventory = models.BooleanField(default=False)
    can_access_credits = models.BooleanField(default=False)
    can_access_sales = models.BooleanField(default=False)
    can_access_cash = models.BooleanField(default=False)
    can_access_reports = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "user"], name="unique_business_user_membership")
        ]

    def __str__(self):
        return f"{self.user} - {self.business} ({self.role})"

    @classmethod
    def all_module_access_fields(cls):
        return (
            "can_access_pos",
            "can_access_inventory",
            "can_access_credits",
            "can_access_sales",
            "can_access_cash",
            "can_access_reports",
        )

    @classmethod
    def legacy_role_for_permissions(
        cls,
        *,
        is_owner=False,
        is_admin=False,
        can_access_pos=False,
        can_access_inventory=False,
        can_access_credits=False,
        can_access_sales=False,
        can_access_cash=False,
        can_access_reports=False,
    ):
        if is_owner:
            return cls.Role.OWNER
        if is_admin:
            return cls.Role.ADMIN
        if can_access_inventory and not any([can_access_pos, can_access_credits, can_access_sales, can_access_cash, can_access_reports]):
            return cls.Role.INVENTORY
        if can_access_credits and not any([can_access_pos, can_access_inventory, can_access_sales, can_access_cash, can_access_reports]):
            return cls.Role.CREDIT_MANAGER
        return cls.Role.CASHIER

    @property
    def has_full_access(self):
        return self.is_owner or self.is_admin

    @property
    def can_manage_users(self):
        return self.is_active and self.has_full_access

    @property
    def can_access_pos_effective(self):
        return self.is_active and (self.has_full_access or self.can_access_pos)

    @property
    def can_access_inventory_effective(self):
        return self.is_active and (self.has_full_access or self.can_access_inventory)

    @property
    def can_access_credits_effective(self):
        return self.is_active and (self.has_full_access or self.can_access_credits)

    @property
    def can_access_sales_effective(self):
        return self.is_active and (self.has_full_access or self.can_access_sales)

    @property
    def can_access_cash_effective(self):
        return self.is_active and (self.has_full_access or self.can_access_cash)

    @property
    def can_access_reports_effective(self):
        return self.is_active and (self.has_full_access or self.can_access_reports)

    @property
    def access_labels(self):
        if self.is_owner:
            return ["Todos", "Propietario"]
        if self.is_admin:
            return ["Todos", "Administrador"]

        labels = []
        if self.can_access_pos:
            labels.append("Punto de Venta")
        if self.can_access_inventory:
            labels.append("Inventario")
        if self.can_access_credits:
            labels.append("Créditos")
        if self.can_access_sales:
            labels.append("Ventas")
        if self.can_access_cash:
            labels.append("Caja")
        if self.can_access_reports:
            labels.append("Reportes")
        return labels or ["Sin acceso"]

    def save(self, *args, **kwargs):
        if self.is_owner or self.is_admin:
            for field_name in self.all_module_access_fields():
                setattr(self, field_name, True)

        self.role = self.legacy_role_for_permissions(
            is_owner=self.is_owner,
            is_admin=self.is_admin,
            can_access_pos=self.can_access_pos,
            can_access_inventory=self.can_access_inventory,
            can_access_credits=self.can_access_credits,
            can_access_sales=self.can_access_sales,
            can_access_cash=self.can_access_cash,
            can_access_reports=self.can_access_reports,
        )
        super().save(*args, **kwargs)


class ProviderPayment(TimeStampedModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="provider_payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_at = models.DateField(default=timezone.localdate)
    method = models.CharField(max_length=40, blank=True)
    note = models.CharField(max_length=240, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-paid_at", "-created_at"]


class Location(TimeStampedModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField(max_length=120)
    is_default = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="unique_location_name_per_business")
        ]
        ordering = ["business", "name"]

    def __str__(self):
        return f"{self.business} - {self.name}"


class Category(TimeStampedModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=120)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="unique_category_name_per_business")
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(TimeStampedModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="products")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    default_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=180)
    sku = models.CharField(max_length=80, blank=True)
    barcode = models.CharField(max_length=80, blank=True)
    unit = models.CharField(max_length=30, default="pieza")
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sale_price = models.DecimalField(max_digits=10, decimal_places=2)
    stock_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    min_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["business", "name"]),
            models.Index(fields=["business", "barcode"]),
            models.Index(fields=["business", "sku"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "sku"],
                condition=~Q(sku=""),
                name="unique_product_sku_per_business",
            ),
            models.UniqueConstraint(
                fields=["business", "barcode"],
                condition=~Q(barcode=""),
                name="unique_product_barcode_per_business",
            ),
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def low_stock(self):
        return self.stock_quantity <= self.min_stock


class InventoryMovement(TimeStampedModel):
    class Type(models.TextChoices):
        INITIAL = "initial", "Inicial"
        SALE = "sale", "Venta"
        ADJUSTMENT = "adjustment", "Ajuste"
        RETURN = "return", "Devolucion"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="inventory_movements")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=Type.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    stock_after = models.DecimalField(max_digits=12, decimal_places=3)
    note = models.CharField(max_length=240, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [
            models.Index(fields=["business", "product", "created_at"]),
        ]
        ordering = ["-created_at"]


class Customer(TimeStampedModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="customers")
    name = models.CharField(max_length=160)
    contact_name = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    tax_id = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["business", "name"])]
        ordering = ["name"]

    def __str__(self):
        return self.name


class CashSession(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Abierta"
        CLOSED = "closed", "Cerrada"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="cash_sessions")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="opened_cash_sessions")
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="closed_cash_sessions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    opening_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    closing_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["business", "status", "opened_at"])]
        ordering = ["-opened_at"]


class Sale(TimeStampedModel):
    class Origin(models.TextChoices):
        POS = "pos", "Punto de Venta"
        SPECIALIZED = "specialized", "Venta especializada"

    class Status(models.TextChoices):
        PAID = "paid", "Pagada"
        CREDIT = "credit", "Crédito"
        CANCELLED = "cancelled", "Cancelada"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="sales")
    cash_session = models.ForeignKey(CashSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="sales")
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="sales")
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.POS)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PAID)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shipping_address = models.TextField(blank=True)
    customer_note = models.TextField(blank=True)
    internal_note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="sales_created")
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="sales_cancelled")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=240, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["business", "created_at"]),
            models.Index(fields=["business", "status", "created_at"]),
        ]
        ordering = ["-created_at"]

    @property
    def folio(self):
        prefix = "VT" if self.origin == self.Origin.SPECIALIZED else "PV"
        if not self.pk:
            return f"{prefix}-PENDIENTE"
        return f"{prefix}-{self.pk:06d}"

    @property
    def line_discount_total(self):
        if hasattr(self, "_prefetched_objects_cache") and "items" in self._prefetched_objects_cache:
            return sum((item.discount_amount for item in self.items.all()), Decimal("0.00"))
        return sum((item.discount_amount for item in self.items.all()), Decimal("0.00"))


class SaleItem(TimeStampedModel):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="sale_items")
    product = models.ForeignKey(Product, null=True, on_delete=models.SET_NULL)
    product_name = models.CharField(max_length=180)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        indexes = [models.Index(fields=["business", "sale"])]


class SalePayment(TimeStampedModel):
    class Method(models.TextChoices):
        CASH = "cash", "Efectivo"
        CARD = "card", "Tarjeta"
        TRANSFER = "transfer", "Transferencia"
        CREDIT = "credit", "Crédito"

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="payments")
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="sale_payments")
    method = models.CharField(max_length=20, choices=Method.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["business", "method", "created_at"])]


class CreditAccount(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Activa"
        BLOCKED = "blocked", "Bloqueada"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="credit_accounts")
    customer = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name="credit_account")
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        indexes = [models.Index(fields=["business", "status"])]

    @property
    def available_credit(self):
        return self.credit_limit - self.balance

    def can_charge(self, amount):
        if self.status != self.Status.ACTIVE:
            return False
        return self.credit_limit == Decimal("0") or self.balance + amount <= self.credit_limit


class CreditTransaction(TimeStampedModel):
    class Type(models.TextChoices):
        SALE = "sale", "Venta"
        PAYMENT = "payment", "Abono"
        ADJUSTMENT = "adjustment", "Ajuste"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="credit_transactions")
    account = models.ForeignKey(CreditAccount, on_delete=models.CASCADE, related_name="transactions")
    transaction_type = models.CharField(max_length=20, choices=Type.choices)
    sale = models.ForeignKey(Sale, null=True, blank=True, on_delete=models.SET_NULL)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=240, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["business", "account", "created_at"])]
        ordering = ["-created_at"]


class AuditLog(TimeStampedModel):
    business = models.ForeignKey(Business, null=True, blank=True, on_delete=models.SET_NULL)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["business", "action", "created_at"])]
        ordering = ["-created_at"]
