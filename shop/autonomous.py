"""Scoped executable tools and durable, customer-authorized purchase journeys."""
import uuid
from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from .services import ShopError, quote, create_order

CUSTOMER_AGENTS = ('stylist', 'cart', 'support', 'payments', 'returns', 'voice')
ADMIN_AGENTS = ('pending', 'admin', 'inventory', 'shipping', 'content', 'business', 'forecast',
                'suppliers', 'pricing', 'marketing', 'retention', 'fraud', 'return_investigator')
ROUTINE = {'add_to_cart', 'remove_from_cart', 'apply_coupon', 'update_description', 'save_seo', 'draft_campaign'}


class PurchaseJourney(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    cart = models.JSONField()
    coupon = models.CharField(max_length=30, blank=True)
    delivery = models.JSONField()
    totals = models.JSONField()
    state = models.CharField(max_length=20, default='awaiting_approval', choices=[('awaiting_approval', 'Awaiting approval'), ('creating', 'Creating'), ('awaiting_payment', 'Awaiting payment'), ('paid', 'Paid'), ('cancelled', 'Cancelled'), ('refunded', 'Refunded'), ('review', 'Review')])
    order = models.OneToOneField('shop.Order', null=True, blank=True, on_delete=models.PROTECT)
    created = models.DateTimeField(auto_now_add=True)
    expires = models.DateTimeField()


def totals(data):
    return {key: str(data[key]) for key in ('subtotal', 'discount', 'shipping', 'total')}


def prepare_purchase(request, items, address_id=0):
    from .models import Address
    from .forms import CheckoutForm
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise ShopError('Select between 1 and 20 product variants.')
    cart = {}
    for item in items:
        try:
            variant, quantity = str(int(item['variant_id'])), int(item['quantity'])
        except (KeyError, TypeError, ValueError):
            raise ShopError('Choose a valid variant and quantity.')
        cart[variant] = cart.get(variant, 0) + quantity
        if not 1 <= quantity <= 10 or not 1 <= cart[variant] <= 10:
            raise ShopError('Quantity must be between 1 and 10 per variant.')
    address = Address.objects.filter(user=request.user)
    address = address.filter(pk=address_id).first() if address_id else address.order_by('pk').first()
    if not address:
        raise ShopError('Save a delivery address in My Account before preparing checkout: /addresses/')
    fields = ('name', 'phone', 'line1', 'line2', 'city', 'state', 'postcode')
    delivery = {key: getattr(address, key) for key in fields}
    delivery['email'] = request.user.email
    form = CheckoutForm(delivery)
    if not form.is_valid():
        raise ShopError('Your saved contact/address is incomplete. Update it at /addresses/.')
    data = quote(cart, request.session.get('coupon', ''))
    if any(line['quantity'] > line['variant'].stock for line in data['lines']):
        raise ShopError('Requested quantity exceeds current stock. Choose another variant or quantity.')
    if len(data['lines']) != len(cart):
        raise ShopError('Some selected products are unavailable. Select again.')
    journey = PurchaseJourney.objects.create(user=request.user, cart=cart,
        coupon=request.session.get('coupon', ''), delivery=delivery, totals=totals(data),
        expires=timezone.now() + timedelta(minutes=15))
    from .automation import audit
    audit('Checkout Agent', 'checkout_prepared', str(journey.pk), actor=request.user)
    items = []
    for line in data["lines"]:
        product = line["variant"].product
        items.append({
            "name": product.name,
            "quantity": line["quantity"],
            "image": product.image.url if product.image else "",
        })
    return {'items': items, 'journey_id': str(journey.pk), 'review_url': reverse('journey_review', args=[journey.pk]),
        'total_inr': str(data['total']), 'state': journey.state,
        'message': 'Review the selected variants, saved delivery details and total. Approval creates a pending order; payment is authorized separately.'}


@transaction.atomic
def authorize_purchase(request, pk):
    journey = PurchaseJourney.objects.select_for_update().get(pk=pk, user=request.user)
    if journey.order_id:
        return journey.order
    if journey.state != 'awaiting_approval' or journey.expires <= timezone.now():
        raise ShopError('This checkout expired or was already processed. Prepare another checkout.')
    data = quote(journey.cart, journey.coupon)
    if totals(data) != journey.totals or len(data['lines']) != len(journey.cart):
        raise ShopError('Prices or availability changed. Prepare and review a new checkout.')
    # Conditional claim also protects databases where row locks are unavailable.
    if not PurchaseJourney.objects.filter(pk=pk, state='awaiting_approval', order__isnull=True).update(state='creating'):
        raise ShopError('Checkout is being processed. Refresh to see the existing order.')
    order = create_order(request.user, journey.cart, journey.coupon, journey.delivery)
    journey.order = order
    journey.state = 'awaiting_payment'
    journey.save(update_fields=['order', 'state'])
    from .automation import audit
    audit('Checkout Agent', 'checkout_authorized', order.number, actor=request.user)
    current_cart = dict(request.session.get('cart', {}))
    for variant, quantity in journey.cart.items():
        remaining = int(current_cart.get(variant, 0)) - int(quantity)
        if remaining > 0:
            current_cart[variant] = remaining
        else:
            current_cart.pop(variant, None)
    request.session['cart'] = current_cart
    if request.session.get('coupon', '') == journey.coupon:
        request.session.pop('coupon', None)
    request.session.save()
    if order.total == Decimal('0'):
        from .services import mark_paid
        mark_paid(order.pk, 'no-charge-' + str(order.pk))
    return order


def install(module):
    from .commerce_agents import ACTION_SCOPES
    schema = module.function_schema
    module.TOOL_SCHEMAS['execute_authorized_action'] = schema('execute_authorized_action',
        'Actually execute a routine action only when routine_updates_authorized is true for this turn. Financial, physical, price and campaign-send actions are excluded. Use propose_action for protected actions.',
        {'action': {'type': 'string', 'enum': sorted(ROUTINE)}, 'target': {'type': 'string'},
         'value': {'type': 'string'}, 'quantity': {'type': 'integer'}, 'extra': {'type': 'string'}})
    module.TOOL_SCHEMAS['prepare_purchase'] = schema('prepare_purchase',
        'Prepare a durable checkout from exact in-stock variant IDs. Read search/product details first; ask the customer when variant selection is ambiguous. Uses saved delivery details. Returns a customer review link; cannot authorize payment.',
        {'items': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'variant_id': {'type': 'integer'}, 'quantity': {'type': 'integer'}},
            'required': ['variant_id', 'quantity'], 'additionalProperties': False}},
         'address_id': {'type': 'integer'}})
    module.TOOL_SCHEMAS['saved_checkout_details'] = schema('saved_checkout_details',
        'List this customer’s saved address IDs, city, PIN and name for checkout selection; never other accounts.', {})
    for key, spec in list(module.AGENTS.items()):
        tools = list(spec.tools)
        if ACTION_SCOPES.get(key, set()) & ROUTINE:
            tools.append('execute_authorized_action')
        if key in ('stylist', 'cart', 'voice'):
            tools.extend(['saved_checkout_details', 'prepare_purchase'])
        module.AGENTS[key] = module.AgentSpec(spec.name, spec.instruction +
            ' When routine_updates_authorized is true, use execute_authorized_action for requested routine edits and report its actual result. For buying requests, select exact variants, read saved_checkout_details, then prepare_purchase and return the review link. Never treat model text as customer checkout approval.', tuple(tools))


def execute(name, arguments, user):
    from .commerce_agents import REQUEST_CONTEXT, ACTION_SCOPES
    from .agents import AgentError
    context = REQUEST_CONTEXT.get() or {}
    request, agent = context.get('request'), context.get('agent_id')
    if request is None or not user.is_authenticated or request.user.pk != user.pk:
        raise ShopError('Authenticated request context required.')
    if name == 'saved_checkout_details':
        from .models import Address
        return {'addresses': list(Address.objects.filter(user=user).values('id', 'name', 'city', 'postcode')),
                'has_email': bool(user.email)}
    if name == 'prepare_purchase':
        import json
        prepared = context.setdefault('prepared_checkout_calls', {})
        key = json.dumps(arguments, sort_keys=True)
        if key not in prepared:
            prepared[key] = prepare_purchase(request, arguments.get('items'), arguments.get('address_id', 0))
        return prepared[key]
    action = arguments.get('action')
    if not context.get('routine_updates_authorized'):
        raise ShopError('Routine updates are not authorized for this message. Use an action confirmation card.')
    if action not in ROUTINE or action not in ACTION_SCOPES.get(agent, set()):
        raise ShopError('This agent cannot execute that action. Protected actions require confirmation.')
    import json
    cache = context.setdefault('executed_routine_calls', {})
    cache_key = json.dumps(arguments, sort_keys=True)
    if cache_key in cache:
        return cache[cache_key]
    from .actions import propose, apply_action
    proposal = propose(request, action, arguments, agent=agent)
    result = apply_action(request, proposal['id'])
    output = {'executed': True, 'action': action, 'result': result, 'audit_action_id': proposal['id']}
    cache[cache_key] = output
    return output

class InvoiceDocument(models.Model):
    order = models.OneToOneField('shop.Order', on_delete=models.PROTECT, related_name='invoice_document')
    snapshot = models.JSONField()
    created = models.DateTimeField(auto_now_add=True)

class AgentRun(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    state = models.CharField(max_length=15, default='running')
    status_code = models.PositiveIntegerField(default=200)
    response = models.JSONField(default=dict)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'key'], name='unique_agent_turn_per_user')]
