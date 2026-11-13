import json
from datetime import timedelta
from unittest.mock import Mock, patch
from django.contrib.auth.models import User
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from langchain_core.messages import AIMessage
from .models import Address, Category, Product, Variant, StoreSettings, Order, AgentAction, InvoiceDocument
from .autonomous import prepare_purchase, authorize_purchase, PurchaseJourney, CUSTOMER_AGENTS, ADMIN_AGENTS
from .commerce_agents import REQUEST_CONTEXT
from .agents import AGENTS, execute_tool
from .services import ShopError, mark_paid
from .automation import run_jobs

@override_settings(AI_MODE='local', PAYMENT_MODE='demo', SHIPPING_MODE='demo', NOTIFICATION_MODE='demo')
class AutonomousTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('journey-buyer', 'buyer@example.com', 'Secret-12345')
        self.other = User.objects.create_user('other', 'other@example.com', 'Secret-12345')
        self.staff = User.objects.create_superuser('journey-admin', 'admin@example.com', 'Secret-12345')
        StoreSettings.objects.create()
        category = Category.objects.create(name='Sarees', slug='sarees')
        self.product = Product.objects.create(category=category, name='Silk Saree', slug='silk', fabric='Silk', price=2000)
        self.variant = Variant.objects.create(product=self.product, size='Free', colour='Plum', sku='SILK-001', stock=4)
        self.address = Address.objects.create(user=self.user, name='Buyer', phone='9876543210', line1='1 Test Road', city='Tirunelveli', state='Tamil Nadu', postcode='627001')
        self.client.force_login(self.user)

    def request(self, user=None, consent=True, agent='cart'):
        from django.test import RequestFactory
        from django.contrib.sessions.middleware import SessionMiddleware
        request = RequestFactory().post('/assistant/chat/')
        request.user = user or self.user
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        return request

    def prepare(self, request=None):
        return prepare_purchase(request or self.request(), [{'variant_id': self.variant.pk, 'quantity': 2}], self.address.pk)

    def test_exact_public_roster_and_customer_permissions(self):
        self.assertEqual((len(ADMIN_AGENTS), len(CUSTOMER_AGENTS)), (13, 6))
        response = self.client.get('/assistant/')
        for agent in ADMIN_AGENTS:
            self.assertNotContains(response, 'value="' + agent + '"')
        for agent in ADMIN_AGENTS:
            result = self.client.post('/assistant/chat/', json.dumps({'agent': agent, 'message': 'Show data'}), content_type='application/json')
            self.assertEqual(result.status_code, 403)

    def test_one_message_adds_items_and_prepares_one_checkout(self):
        from uuid import uuid4

        key = str(uuid4())
        payload = {
            "agent": "auto",
            "message": "Buy 2 Silk Sarees",
            "authorize_routine_updates": True,
        }
        def send():
            return self.client.post(
                "/assistant/chat/", payload, content_type="application/json",
                headers={"Idempotency-Key": key},
            )

        first = send()
        self.assertEqual(first.status_code, 200)
        data = first.json()
        self.assertEqual(data["journeys"][0]["items"][0]["quantity"], 2)
        self.assertEqual(self.client.get(data["journeys"][0]["review_url"]).status_code, 200)
        self.assertEqual(send().json(), data)
        self.assertEqual(self.client.session["cart"], {str(self.variant.pk): 2})
        self.assertEqual(PurchaseJourney.objects.count(), 1)
        self.assertFalse(Order.objects.exists())

    def test_failed_checkout_preparation_leaves_bag_intact(self):
        self.address.delete()
        response = self.client.post(
            "/assistant/chat/",
            {"message": "Buy 2 Silk Sarees", "authorize_routine_updates": True},
            content_type="application/json",
        )
        self.assertIn("delivery address", response.json()["answer"])
        self.assertFalse(self.client.session.get("cart"))
        self.assertFalse(PurchaseJourney.objects.exists())

    def test_prepare_does_not_reserve_stock_or_create_order(self):
        self.prepare()
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 4)
        self.assertEqual(Order.objects.count(), 0)

    def test_cross_customer_address_rejected(self):
        with self.assertRaises(ShopError):
            self.prepare(self.request(self.other))

    def test_stock_and_quantity_checked_at_preparation(self):
        for quantity in (0, 5, 11):
            with self.assertRaises(ShopError):
                prepare_purchase(self.request(), [{'variant_id': self.variant.pk, 'quantity': quantity}])

    def test_authorization_creates_one_pending_order_on_repeat(self):
        journey = self.prepare()
        request = self.request()
        first = authorize_purchase(request, journey['journey_id'])
        second = authorize_purchase(request, journey['journey_id'])
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(first.payment_status, 'pending')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 2)

    def test_changed_price_or_stock_rolls_back_checkout(self):
        journey = self.prepare()
        Product.objects.filter(pk=self.product.pk).update(price=2300)
        with self.assertRaises(ShopError):
            authorize_purchase(self.request(), journey['journey_id'])
        self.assertFalse(Order.objects.exists())
        Product.objects.filter(pk=self.product.pk).update(price=2000)
        Variant.objects.filter(pk=self.variant.pk).update(stock=1)
        with self.assertRaises(ShopError):
            authorize_purchase(self.request(), journey['journey_id'])
        self.assertEqual(PurchaseJourney.objects.get().state, 'awaiting_approval')

    def test_expired_checkout_rejected(self):
        self.prepare()
        PurchaseJourney.objects.update(expires=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(ShopError):
            authorize_purchase(self.request(), PurchaseJourney.objects.get().pk)

    def test_review_requires_owner_csrf_and_explicit_approval(self):
        journey = self.prepare()
        url = journey['review_url']
        self.client.get(url)
        self.client.post(url, {})
        self.assertFalse(Order.objects.exists())
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        self.assertEqual(protected.post(url, {'authorize':'yes'}).status_code, 403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {'authorize':'yes'}).status_code, 404)

    def test_routine_execution_requires_turn_consent_and_scope(self):
        request = self.request()
        args = {'action':'add_to_cart', 'target':str(self.variant.pk), 'quantity':2, 'value':'', 'extra':''}
        token = REQUEST_CONTEXT.set({'request':request,'agent_id':'cart'})
        try:
            self.assertIn('error', execute_tool('execute_authorized_action',args,self.user,AGENTS['cart'].tools))
        finally:
            REQUEST_CONTEXT.reset(token)
        token = REQUEST_CONTEXT.set({'request':request,'agent_id':'cart','routine_updates_authorized':True})
        try:
            result = execute_tool('execute_authorized_action',args,self.user,AGENTS['cart'].tools)
            self.assertTrue(result['executed'])
            self.assertEqual(request.session['cart'][str(self.variant.pk)], 2)
            self.assertEqual(AgentAction.objects.get().state,'applied')
            for kind in ('refund_order','update_price','mark_packed','approve_campaign','receive_stock'):
                self.assertIn('error',execute_tool('execute_authorized_action',{**args,'action':kind},self.user,AGENTS['cart'].tools))
        finally:
            REQUEST_CONTEXT.reset(token)

    def test_staff_routine_write_and_financial_boundary(self):
        request = self.request(self.staff)
        token = REQUEST_CONTEXT.set({'request':request,'agent_id':'content','routine_updates_authorized':True})
        try:
            args = {'action':'update_description','target':str(self.product.pk),'quantity':1,'value':'Silk saree in plum.','extra':''}
            result = execute_tool('execute_authorized_action',args,self.staff,AGENTS['content'].tools)
            self.assertTrue(result['executed'])
            self.product.refresh_from_db()
            self.assertEqual(self.product.description,'Silk saree in plum.')
        finally:
            REQUEST_CONTEXT.reset(token)

    @override_settings(AI_MODE='gemini')
    @patch('shop.llm_graph.make_model')
    def test_multistep_langgraph_executes_tools_and_prepares_checkout(self, make_model):
        def call(name, args, ident):
            return AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':ident,'type':'tool_call'}])
        model = Mock()
        make_model.return_value.bind_tools.return_value = model
        model.invoke.side_effect = [
            call('get_product_details', {'product_id':self.product.pk}, 'details'),
            call('execute_authorized_action', {'action':'add_to_cart','target':str(self.variant.pk),'quantity':2,'value':'','extra':''}, 'bag'),
            call('saved_checkout_details', {}, 'address'),
            call('prepare_purchase', {'items':[{'variant_id':self.variant.pk,'quantity':2}],'address_id':self.address.pk}, 'purchase'),
            AIMessage(content='Your selected silk sarees are ready. Review the checkout summary.')]
        response = self.client.post('/assistant/chat/',json.dumps({'agent':'cart','message':'Order 2 plum silk sarees, use my saved address','authorize_routine_updates':True}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(len(response.json()['journeys']),1)
        self.assertEqual(model.invoke.call_count,5)
        self.assertEqual(self.client.session['cart'][str(self.variant.pk)],2)
        self.assertFalse(Order.objects.exists())

    def test_verified_payment_worker_generates_single_invoice_and_pack_task(self):
        from .models import WorkTask
        journey = self.prepare()
        order = authorize_purchase(self.request(),journey['journey_id'])
        self.assertEqual(self.client.get(f'/orders/{order.pk}/invoice/?format=pdf').status_code,403)
        mark_paid(order.pk,'demo-verified')
        run_jobs(limit=30)
        self.assertEqual(InvoiceDocument.objects.filter(order=order).count(),1)
        self.assertTrue(WorkTask.objects.filter(order=order,kind='pack').exists())
        response = self.client.get(f'/orders/{order.pk}/invoice/?format=pdf')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.content.startswith(b'%PDF'))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/orders/{order.pk}/invoice/?format=pdf').status_code,404)

    def test_identical_client_retry_is_not_executed_twice(self):
        import uuid
        key = str(uuid.uuid4())
        payload = json.dumps({'agent':'cart','message':f'add {self.variant.pk} 1','authorize_routine_updates':True})
        for _ in range(2):
            response = self.client.post('/assistant/chat/',payload,content_type='application/json',HTTP_IDEMPOTENCY_KEY=key)
            self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.client.session['cart'][str(self.variant.pk)],1)
        response = self.client.post('/assistant/chat/',json.dumps({'agent':'cart','message':'another message'}),content_type='application/json',HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(response.status_code,409)
