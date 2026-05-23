from django.contrib import admin

from .models import (
    AuditLog,
    Business,
    BusinessMembership,
    CashSession,
    Category,
    CreditAccount,
    CreditTransaction,
    Customer,
    InventoryMovement,
    Location,
    Plan,
    Product,
    ProviderPayment,
    Sale,
    SaleItem,
    SalePayment,
)


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "plan", "service_expires_at", "currency")
    search_fields = ("name", "slug", "owner_name", "email")
    list_filter = ("status", "plan")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "barcode", "sku", "sale_price", "stock_quantity", "is_active")
    search_fields = ("name", "barcode", "sku", "business__name")
    list_filter = ("business", "is_active")


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("id", "business", "status", "total", "created_by", "created_at")
    list_filter = ("business", "status")
    search_fields = ("id", "customer__name")


admin.site.register(Plan)
admin.site.register(BusinessMembership)
admin.site.register(ProviderPayment)
admin.site.register(Location)
admin.site.register(Category)
admin.site.register(InventoryMovement)
admin.site.register(Customer)
admin.site.register(CashSession)
admin.site.register(SaleItem)
admin.site.register(SalePayment)
admin.site.register(CreditAccount)
admin.site.register(CreditTransaction)
admin.site.register(AuditLog)

