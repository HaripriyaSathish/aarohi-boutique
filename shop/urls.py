from django.contrib.auth import views as auth_views
from django.urls import path
from . import views, agent_views, automation_views, intelligence_views, journey_views

urlpatterns = [
    path("assistant/checkout/<uuid:pk>/", journey_views.review, name="journey_review"),
    path("agents/", intelligence_views.agent_hub, name="agent_hub"),
    path("intelligence/", intelligence_views.intelligence, name="intelligence"),
    path("ai-studio/", intelligence_views.ai_studio, name="ai_studio"),
    path("ai-studio/tryon/", intelligence_views.start_tryon, name="start_tryon"),
    path(
        "ai-studio/tryon/<int:pk>/refresh/",
        intelligence_views.poll_tryon,
        name="poll_tryon",
    ),
    path(
        "ai-studio/tryon/<int:pk>/image/",
        intelligence_views.tryon_image,
        name="tryon_image",
    ),
    path(
        "ai-studio/tryon/<int:pk>/delete/",
        intelligence_views.delete_tryon,
        name="delete_tryon",
    ),
    path("product-preview/<slug:slug>/", views.product_preview, name="product_preview"),
    path("operations/", automation_views.operations, name="operations"),
    path("operations/run/", automation_views.run_cycle, name="run_cycle"),
    path("admin-tasks/", automation_views.pending_board, name="pending_board"),
    path("admin-tasks/briefing/", automation_views.pending_briefing, name="pending_briefing"),
    path("notifications/", automation_views.notifications, name="notifications"),
    path(
        "assistant/actions/propose/",
        automation_views.propose_action,
        name="propose_action",
    ),
    path(
        "assistant/actions/<uuid:pk>/",
        automation_views.action_review,
        name="action_review",
    ),
    path(
        "assistant/actions/<uuid:pk>/confirm/",
        automation_views.confirm_action,
        name="action_confirm",
    ),
    path(
        "assistant/actions/<uuid:pk>/reject/",
        automation_views.reject_action,
        name="action_reject",
    ),
    path("assistant/", agent_views.assistant_page, name="assistant"),
    path("assistant/chat/", agent_views.assistant_chat, name="assistant_chat"),
    path("assistant/clear/", agent_views.clear_chat, name="assistant_clear"),
    path("", views.home, name="home"),
    path("shop/", views.shop, name="shop"),
    path("product/<slug:slug>/", views.product, name="product"),
    path("cart/", views.cart, name="cart"),
    path("cart/add/", views.add_product, name="add_product"),
    path("checkout/", views.checkout, name="checkout"),
    path("payment/<uuid:pk>/", views.payment, name="payment"),
    path("payment/<uuid:pk>/demo/", views.demo_payment, name="demo_payment"),
    path("payment/<uuid:pk>/verify/", views.payment_verify, name="payment_verify"),
    path("payments/webhook/", views.webhook, name="webhook"),
    path("payment/<uuid:pk>/cashfree/return/", views.cashfree_return, name="cashfree_return"),
    path("payments/cashfree/webhook/", views.cashfree_webhook, name="cashfree_webhook"),
    path("account/", views.account, name="account"),
    path("orders/<uuid:pk>/", views.order_detail, name="order"),
    path("orders/<uuid:pk>/cancel/", views.cancel, name="cancel"),
    path("orders/<uuid:pk>/invoice/", views.invoice, name="invoice"),
    path("wishlist/", views.wishlist, name="wishlist"),
    path("wishlist/<int:pk>/toggle/", views.wishlist_toggle, name="wishlist_toggle"),
    path("product/<int:pk>/review/", views.review, name="review"),
    path("addresses/", views.addresses, name="addresses"),
    path("addresses/<int:pk>/delete/", views.delete_address, name="delete_address"),
    path("returns/<int:pk>/", views.return_request, name="return"),
    path("register/", views.register, name="register"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="shop/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "password/change/",
        auth_views.PasswordChangeView.as_view(
            template_name="shop/password_change.html", success_url="/account/"
        ),
        name="password_change",
    ),
    path(
        "password/reset/",
        auth_views.PasswordResetView.as_view(
            template_name="shop/password_reset.html",
            email_template_name="registration/password_reset_email.txt",
        ),
        name="password_reset",
    ),
    path(
        "password/reset/sent/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="shop/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "password/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="shop/password_reset_confirm.html"
        ),
        name="password_reset_confirm",
    ),
    path(
        "password/reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="shop/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
    path("contact/", views.contact, name="contact"),
    path("info/<slug:page>/", views.information, name="information"),
    path("delivery-check/", views.delivery_check, name="delivery_check"),
]
