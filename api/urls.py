from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

app_name = "api"

urlpatterns = [
    path("health/", views.health, name="health"),
    # Store & catalog
    path("store/", views.store, name="store"),
    path("home/", views.home, name="home"),
    path("categories/", views.categories, name="categories"),
    path("products/", views.products, name="products"),
    path("products/filters/", views.product_filters, name="product_filters"),
    path("products/<slug:slug>/", views.product_detail, name="product_detail"),
    path("products/<int:pk>/review/", views.product_review, name="product_review"),
    path("delivery-check/", views.delivery_check, name="delivery_check"),
    path("contact/", views.contact, name="contact"),
    # Auth
    path("auth/register/", views.register, name="register"),
    path("auth/login/", views.login, name="login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="refresh"),
    path("auth/me/", views.me, name="me"),
    # Cart & checkout
    path("cart/quote/", views.cart_quote, name="cart_quote"),
    path("checkout/", views.checkout, name="checkout"),
    # Orders & payment
    path("orders/", views.orders, name="orders"),
    path("orders/<uuid:pk>/", views.order_detail, name="order_detail"),
    path("orders/<uuid:pk>/cancel/", views.order_cancel, name="order_cancel"),
    path("orders/<uuid:pk>/pay/", views.order_pay, name="order_pay"),
    path("orders/<uuid:pk>/verify/", views.order_verify, name="order_verify"),
    path("orders/<uuid:pk>/demo-pay/", views.order_demo_pay, name="order_demo_pay"),
    path("orders/<uuid:pk>/invoice/", views.order_invoice_link, name="order_invoice"),
    path("invoice/<str:token>/", views.invoice_pdf, name="invoice_pdf"),
    path("pay/start/", views.pay_start, name="pay_start"),
    path("pay/return/<uuid:pk>/", views.pay_return, name="pay_return"),
    # Returns
    path("returns/", views.returns, name="returns"),
    path("order-items/<int:item_id>/return/", views.return_create, name="return_create"),
    # Addresses & wishlist
    path("addresses/", views.addresses, name="addresses"),
    path("addresses/<int:pk>/", views.address_detail, name="address_detail"),
    path("wishlist/", views.wishlist, name="wishlist"),
    path("wishlist/<int:pk>/toggle/", views.wishlist_toggle, name="wishlist_toggle"),
]
