from django import forms
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from django.utils import timezone

from .models import Business, BusinessMembership, Category, Customer, Plan, Product


User = get_user_model()


class ProductForm(forms.ModelForm):
    category_name = forms.CharField(label="Categoria", required=False, max_length=120)

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

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)

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
    credit_limit = forms.DecimalField(label="Limite de credito", required=False, min_value=0, initial=0)

    class Meta:
        model = Customer
        fields = ["name", "phone", "email", "address", "is_active", "credit_limit"]

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)

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
    owner_name = forms.CharField(label="Dueno", max_length=140, required=False)
    phone = forms.CharField(label="Telefono", max_length=40, required=False)
    email = forms.EmailField(label="Email del negocio", required=False)
    plan = forms.ModelChoiceField(queryset=Plan.objects.filter(is_active=True), required=False)
    service_expires_at = forms.DateField(
        label="Vence",
        required=False,
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    owner_username = forms.CharField(label="Usuario dueno", max_length=150)
    owner_email = forms.EmailField(label="Email dueno", required=False)
    owner_password = forms.CharField(label="Password temporal", widget=forms.PasswordInput)

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
        )
        return business, user
