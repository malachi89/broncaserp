from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from .models import Business, BusinessMembership, Category, Customer, Plan, Product, SalePayment


User = get_user_model()


class ProductForm(forms.ModelForm):
    category_name = forms.CharField(label="Categoría", required=False, max_length=120)

    class Meta:
        model = Product
        fields = [
            "name",
            "sku",
            "barcode",
            "category_name",
            "unit",
            "cost_price",
            "sale_price",
            "stock_quantity",
            "min_stock",
            "is_active",
        ]
        labels = {
            "name": "Nombre",
            "sku": "SKU",
            "barcode": "Código de barras",
            "unit": "Unidad",
            "cost_price": "Costo",
            "sale_price": "Precio de venta",
            "stock_quantity": "Existencia",
            "min_stock": "Existencia mínima",
            "is_active": "Activo",
        }

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)
        self.fields["stock_quantity"].widget.attrs["step"] = "any"
        self.fields["stock_quantity"].widget.attrs["data-step-one"] = "true"
        self.fields["min_stock"].widget.attrs["step"] = "any"
        self.fields["min_stock"].widget.attrs["data-step-one"] = "true"

    def save(self, commit=True):
        product = super().save(commit=False)
        product.business = self.business
        category_name = self.cleaned_data.get("category_name", "").strip()
        if category_name:
            category, _created = Category.objects.get_or_create(
                business=self.business,
                name=category_name,
            )
            product.category = category
        if commit:
            product.save()
        return product


class CustomerForm(forms.ModelForm):
    credit_limit = forms.DecimalField(label="Límite de crédito", required=False, min_value=0, initial=0)

    class Meta:
        model = Customer
        fields = ["name", "contact_name", "phone", "email", "tax_id", "address", "is_active", "credit_limit"]
        labels = {
            "name": "Nombre",
            "contact_name": "Contacto",
            "phone": "Teléfono",
            "email": "Correo electrónico",
            "tax_id": "RFC",
            "address": "Dirección",
            "is_active": "Activo",
        }

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["credit_limit"].initial = self.instance.credit_account.credit_limit

    def save(self, commit=True):
        customer = super().save(commit=False)
        customer.business = self.business
        if commit:
            customer.save()
            customer.credit_account.credit_limit = self.cleaned_data.get("credit_limit") or 0
            customer.credit_account.save(update_fields=["credit_limit", "updated_at"])
        return customer


class ProviderBusinessForm(forms.Form):
    business_name = forms.CharField(label="Negocio", max_length=140)
    slug = forms.SlugField(label="Slug", max_length=60, required=False)
    owner_name = forms.CharField(label="Responsable", max_length=140, required=False)
    phone = forms.CharField(label="Teléfono", max_length=40, required=False)
    email = forms.EmailField(label="Correo del negocio", required=False)
    plan = forms.ModelChoiceField(label="Plan", queryset=Plan.objects.filter(is_active=True), required=False)
    service_expires_at = forms.DateField(
        label="Vence",
        required=False,
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    owner_username = forms.CharField(label="Usuario del dueño", max_length=150)
    owner_email = forms.EmailField(label="Correo del dueño", required=False)
    owner_password = forms.CharField(label="Contraseña temporal", widget=forms.PasswordInput)

    def clean_slug(self):
        slug = self.cleaned_data.get("slug") or slugify(self.cleaned_data.get("business_name", ""))
        if Business.objects.filter(slug=slug).exists():
            raise forms.ValidationError("Ese slug ya existe.")
        return slug

    def clean_owner_username(self):
        username = self.cleaned_data["owner_username"]
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Ese usuario ya existe.")
        return username

    def save(self, created_by):
        business = Business.objects.create(
            name=self.cleaned_data["business_name"],
            slug=self.cleaned_data["slug"],
            owner_name=self.cleaned_data.get("owner_name", ""),
            phone=self.cleaned_data.get("phone", ""),
            email=self.cleaned_data.get("email", ""),
            plan=self.cleaned_data.get("plan"),
            service_expires_at=self.cleaned_data.get("service_expires_at"),
            status=Business.Status.ACTIVE,
        )
        user = User.objects.create_user(
            username=self.cleaned_data["owner_username"],
            email=self.cleaned_data.get("owner_email", ""),
            password=self.cleaned_data["owner_password"],
        )
        BusinessMembership.objects.create(
            business=business,
            user=user,
            role=BusinessMembership.Role.OWNER,
            is_owner=True,
            is_admin=True,
            can_access_pos=True,
            can_access_inventory=True,
            can_access_credits=True,
            can_access_cash=True,
            can_access_reports=True,
        )
        return business, user


class TenantUserForm(forms.Form):
    username = forms.CharField(label="Usuario", max_length=150)
    email = forms.EmailField(label="Correo electrónico", required=False)
    password = forms.CharField(label="Contraseña temporal", required=False, widget=forms.PasswordInput)
    is_admin = forms.BooleanField(label="Administrador", required=False)
    can_access_pos = forms.BooleanField(label="Punto de Venta", required=False)
    can_access_inventory = forms.BooleanField(label="Inventario", required=False)
    can_access_credits = forms.BooleanField(label="Créditos", required=False)
    can_access_sales = forms.BooleanField(label="Ventas", required=False)
    can_access_cash = forms.BooleanField(label="Caja", required=False)
    can_access_reports = forms.BooleanField(label="Reportes", required=False)
    is_active = forms.BooleanField(label="Activo", required=False, initial=True)

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)

    def clean_username(self):
        return self.cleaned_data["username"].strip()

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get("username")
        password = cleaned_data.get("password")
        is_admin = cleaned_data.get("is_admin")
        selected_access = any(
            cleaned_data.get(field_name)
            for field_name in BusinessMembership.all_module_access_fields()
        )

        if not is_admin and not selected_access:
            raise forms.ValidationError("Selecciona al menos un acceso o marca Administrador.")

        if username:
            user = User.objects.filter(username=username).first()
            if user is None and not password:
                self.add_error("password", "La contraseña es obligatoria cuando el usuario no existe.")
            if user and BusinessMembership.objects.filter(business=self.business, user=user).exists():
                self.add_error("username", "Ese usuario ya pertenece a este negocio.")

        return cleaned_data

    def save(self):
        username = self.cleaned_data["username"]
        email = self.cleaned_data.get("email", "")
        password = self.cleaned_data.get("password")
        user = User.objects.filter(username=username).first()

        if user is None:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
            )
        else:
            if email and not user.email:
                user.email = email
                user.save(update_fields=["email"])
            if password:
                user.set_password(password)
                user.save(update_fields=["password"])

        membership = BusinessMembership.objects.create(
            business=self.business,
            user=user,
            role=BusinessMembership.Role.CASHIER,
            is_admin=self.cleaned_data.get("is_admin", False),
            can_access_pos=self.cleaned_data.get("can_access_pos", False),
            can_access_inventory=self.cleaned_data.get("can_access_inventory", False),
            can_access_credits=self.cleaned_data.get("can_access_credits", False),
            can_access_sales=self.cleaned_data.get("can_access_sales", False),
            can_access_cash=self.cleaned_data.get("can_access_cash", False),
            can_access_reports=self.cleaned_data.get("can_access_reports", False),
            is_active=self.cleaned_data.get("is_active", True),
        )
        return membership


class MembershipAccessForm(forms.ModelForm):
    class Meta:
        model = BusinessMembership
        fields = [
            "is_admin",
            "can_access_pos",
            "can_access_inventory",
            "can_access_credits",
            "can_access_sales",
            "can_access_cash",
            "can_access_reports",
            "is_active",
        ]
        labels = {
            "is_admin": "Administrador",
            "can_access_pos": "Punto de Venta",
            "can_access_inventory": "Inventario",
            "can_access_credits": "Créditos",
            "can_access_sales": "Ventas",
            "can_access_cash": "Caja",
            "can_access_reports": "Reportes",
            "is_active": "Activo",
        }

    def __init__(self, *args, acting_membership=None, **kwargs):
        self.acting_membership = acting_membership
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()

        if self.instance.is_owner:
            raise forms.ValidationError("El usuario propietario no se edita desde esta pantalla.")

        is_active = cleaned_data.get("is_active", self.instance.is_active)
        is_admin = cleaned_data.get("is_admin", self.instance.is_admin)
        selected_access = any(
            cleaned_data.get(field_name)
            for field_name in BusinessMembership.all_module_access_fields()
        )

        if is_active and not is_admin and not selected_access:
            raise forms.ValidationError("Selecciona al menos un acceso o marca Administrador.")

        keeps_management = is_active and is_admin
        other_managers = BusinessMembership.objects.filter(
            business=self.instance.business,
            is_active=True,
        ).exclude(pk=self.instance.pk).filter(Q(is_owner=True) | Q(is_admin=True))

        if not keeps_management and not other_managers.exists():
            raise forms.ValidationError("Debe quedar al menos un usuario propietario o administrador activo.")

        return cleaned_data


class SpecializedSaleForm(forms.Form):
    customer_id = forms.ModelChoiceField(label="Cliente", queryset=Customer.objects.none())
    payment_method = forms.ChoiceField(label="Método de pago", choices=SalePayment.Method.choices)
    shipping_address = forms.CharField(label="Dirección de envío", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    discount_total = forms.DecimalField(label="Descuento general", required=False, min_value=0, initial=0, decimal_places=2, max_digits=12)
    notes = forms.CharField(label="Notas", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)
        self.fields["customer_id"].queryset = Customer.objects.filter(business=business, is_active=True).order_by("name")

    def clean_discount_total(self):
        return self.cleaned_data.get("discount_total") or 0
