from django.apps import AppConfig


class ApiConfig(AppConfig):
    """REST API for the Aarohi mobile app (Expo / React Native).

    Purely additive: it reuses the shop models and services and never imports
    any AI / agent / automation module. The website keeps working unchanged.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "api"
