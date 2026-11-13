from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from .autonomous import PurchaseJourney, authorize_purchase
from .services import quote, ShopError

@login_required
@require_http_methods(['GET', 'POST'])
def review(request, pk):
    journey = get_object_or_404(PurchaseJourney, pk=pk, user=request.user)
    if request.method == 'POST':
        if request.POST.get('authorize') != 'yes':
            messages.error(request, 'Confirm the checkout summary to continue.')
        else:
            try:
                order = authorize_purchase(request, pk)
                return redirect('order' if order.total == 0 else 'payment', pk=order.pk)
            except ShopError as exc:
                messages.error(request, str(exc))
    try:
        data = quote(journey.cart, journey.coupon)
    except ShopError as exc:
        data = {}
        messages.error(request, str(exc))
    return render(request, 'shop/journey.html', {'journey': journey, **data})
