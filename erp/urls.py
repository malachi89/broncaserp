from django.urls import path

from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("sin-negocio/", views.no_business, name="no_business"),
    path("pos/", views.pos, name="pos"),
    path("inventario/", views.inventory, name="inventory"),
    path("inventario/ajustar/", views.adjust_inventory_view, name="adjust_inventory"),
    path("creditos/", views.credits, name="credits"),
    path("creditos/abono/", views.credit_payment, name="credit_payment"),
    path("caja/", views.cash, name="cash"),
    path("caja/abrir/", views.open_cash, name="open_cash"),
    path("caja/cerrar/", views.close_cash, name="close_cash"),
    path("reportes/", views.reports, name="reports"),
    path("configuracion/", views.settings_view, name="settings"),
    path("proveedor/", views.provider_dashboard, name="provider_dashboard"),
    path("proveedor/clientes/", views.provider_businesses, name="provider_businesses"),
]

