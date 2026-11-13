class CartSnapshotMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            request.user.is_authenticated
            and request.method == "POST"
            and request.path.startswith(("/cart/", "/checkout/", "/assistant/"))
        ):
            from .automation import remember_cart

            remember_cart(request)
        return response
