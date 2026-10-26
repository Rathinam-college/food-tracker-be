import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()
from django.test import RequestFactory
from rest_framework.test import force_authenticate
from core.views import InventoryViewSet
from django.contrib.auth import get_user_model
from core.models import Inventory, Department

User = get_user_model()
user = User.objects.first()

factory = RequestFactory()
request = factory.get('/stock_balance_report/')
force_authenticate(request, user=user)

view = InventoryViewSet.as_view({'get': 'stock_balance_report'})
response = view(request)

proc_dept = Department.objects.get(code='procurement')
invs = {inv.item_id: inv.current_quantity for inv in Inventory.objects.filter(department=proc_dept)}

discrepancies = []
if hasattr(response, 'data'):
    for x in response.data:
        item_id = x['item_id']
        api_bal = x['current_balance']
        db_bal = invs.get(item_id, 0)
        if float(api_bal) != float(db_bal):
            discrepancies.append(f"Mismatch for {x['item_name']}: API={api_bal} DB={db_bal}")

if discrepancies:
    for d in discrepancies:
        print(d)
else:
    print("All balances match perfectly!")
