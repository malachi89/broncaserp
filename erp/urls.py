from django.urls import path

from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("sin-negocio/", views.no_business, name="no_business"),
    path("pos/", views.pos, name="pos"),
    path("pos/ventas/<int:sale_id>/ticket/", views.pos_sale_note, name="pos_sale_note"),
    path("inventario/", views.inventory, name="inventory"),
    path("inventario/ajustar/", views.adjust_inventory_view, name="adjust_inventory"),
    path("clientes/", views.clients, name="clients"),
    path("ventas/", views.sales, name="sales"),
    path("ventas/nueva/", views.new_sale, name="new_sale"),
    path("ventas/<int:sale_id>/", views.sale_detail, name="sale_detail"),
    path("ventas/<int:sale_id>/nota/", views.sale_note, name="sale_note"),
    path("ventas/<int:sale_id>/cancelar/", views.cancel_sale_view, name="cancel_sale"),
    path("creditos/", views.credits, name="credits"),
    path("creditos/abono/", views.credit_payment, name="credit_payment"),
    path("caja/", views.cash, name="cash"),
    path("caja/abrir/", views.open_cash, name="open_cash"),
    path("caja/cerrar/", views.close_cash, name="close_cash"),
    path("caja/movimiento/", views.cash_movement, name="cash_movement"),
    path("reportes/", views.reports, name="reports"),
    path("cambiar-contrasena/", views.password_change, name="password_change"),
    path("configuracion/", views.settings_view, name="settings"),
    path("proveedor/", views.provider_dashboard, name="provider_dashboard"),
    path("proveedor/clientes/", views.provider_businesses, name="provider_businesses"),
]
