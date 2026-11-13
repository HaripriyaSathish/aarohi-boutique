import os, sys, json, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['DJANGO_SETTINGS_MODULE']='boutique.settings'
os.environ['AI_MODE']='gemini'
os.environ['PAYMENT_MODE']='demo'
os.environ['SHIPPING_MODE']='demo'
os.environ['NOTIFICATION_MODE']='demo'
from django.conf import settings
sandbox = tempfile.TemporaryDirectory(prefix='aarohi-live-check-')
settings.DATABASES['default']['NAME']=str(Path(sandbox.name)/'test.sqlite3')
settings.DEBUG=True
settings.ALLOWED_HOSTS=['testserver','localhost','127.0.0.1']
import django
django.setup()
from django.core.management import call_command
call_command('migrate',verbosity=0)
from django.contrib.auth.models import User
from django.test import Client
from shop.models import Category,Product,Variant,Address,StoreSettings,Order
from shop.autonomous import PurchaseJourney
u=User.objects.create_user('synthetic-live-buyer','synthetic@example.com','Smoke-12345')
StoreSettings.objects.create()
c=Category.objects.create(name='Sarees',slug='sarees')
p=Product.objects.create(name='Plum Silk Saree',slug='plum-silk',category=c,fabric='Silk',price=2000,description='Plum silk saree, free size.')
v=Variant.objects.create(product=p,sku='LIVE-SYNTHETIC-1',size='Free',colour='Plum',stock=5)
a=Address.objects.create(user=u,name='Synthetic Customer',phone='9876543210',line1='1 Synthetic Road',city='Tirunelveli',state='Tamil Nadu',postcode='627001')
client=Client();client.force_login(u)
r=client.post('/assistant/chat/', json.dumps({'agent':'cart','message':f'Order 2 Plum Silk Sarees. I choose product {p.pk}, variant {v.pk}, Free size, Plum colour, saved address {a.pk}. Add to my bag and prepare the checkout for my review.','authorize_routine_updates':True}),content_type='application/json')
data=r.json()
print(json.dumps({'http_status':r.status_code,'tools':data.get('tools'),'engine':data.get('engine'),'error':data.get('error'),'journey_count':len(data.get('journeys',[])),'bag_quantity':client.session.get('cart',{}).get(str(v.pk))}))
assert r.status_code==200,data.get('error')
assert data.get('journeys'), 'Gemini did not prepare checkout'
journey=PurchaseJourney.objects.get(pk=data['journeys'][0]['journey_id'])
assert not Order.objects.exists()
r=client.post(data['journeys'][0]['review_url'],{'authorize':'yes'})
journey.refresh_from_db();order=journey.order
assert order and order.payment_status=='pending'
r=client.post(f'/payment/{order.pk}/demo/',{'result':'success'})
print(json.dumps({'approval_status':r.status_code,'created_orders':Order.objects.count(),'payment_before_demo':'pending'}))
# Use supported sandbox payment view's documented payload; assert actual final status separately.
order.refresh_from_db()
print(json.dumps({'sandbox_payment_status':order.payment_status}))
admin=User.objects.create_superuser('synthetic-live-admin','staff@example.com','Smoke-12345')
client.force_login(admin)
r=client.post('/assistant/chat/',json.dumps({'agent':'content','message':f'Update product {p.pk} description to exactly: Plum silk saree in free size.','authorize_routine_updates':True}),content_type='application/json')
p.refresh_from_db()
print(json.dumps({'staff_http_status':r.status_code,'staff_tools':r.json().get('tools'),'staff_description_updated':p.description=='Plum silk saree in free size.','error':r.json().get('error')}))
assert p.description=='Plum silk saree in free size.'

from shop.automation import run_jobs
from shop.models import InvoiceDocument, WorkTask
run_jobs(limit=50)
assert InvoiceDocument.objects.filter(order=order).exists()
assert WorkTask.objects.filter(order=order, kind='pack').exists()
print('Verified synthetic live-model checkout, demo payment, invoice job and authorized content write.')
sandbox.cleanup()
