import os, sys
import django
sys.path.append('d:/FoodDepartment/backend')
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
django.setup()

from core.models import StockMovement, Item
from django.db.models import Sum

print("=== ADJUSTMENTS IN DATABASE ===")
adjustments = StockMovement.objects.filter(
    type__in=['WASTAGE', 'BREAKAGE', 'EXPIRED', 'AUDIT']
).values('item__name', 'type').annotate(total=Sum('quantity'))

db_totals = {}
for adj in adjustments:
    print(f"Item: {adj['item__name']}, Type: {adj['type']}, Total: {adj['total']}")
    db_totals[adj['item__name']] = db_totals.get(adj['item__name'], 0) + float(adj['total'])

for item_name, total in db_totals.items():
    print(f"Total for {item_name}: {total}")

print("\n=== ADJUSTMENTS IN STOCK BALANCE REPORT ===")
from django.test import RequestFactory
from core.views import InventoryViewSet
factory = RequestFactory()
request = factory.get('/stock_balance_report/')
view = InventoryViewSet.as_view({'get': 'stock_balance_report'})
response = view(request)

report_totals = {}
for item in response.data:
    if float(item.get('total_adjustments', 0)) > 0:
        print(f"Report Output -> Item: {item['item_name']}, Adjustments: {item['total_adjustments']}")
        report_totals[item['item_name']] = float(item['total_adjustments'])

print("\n=== COMPARISON ===")
for item_name, db_total in db_totals.items():
    report_total = report_totals.get(item_name, 0)
    match = "YES" if db_total == report_total else "NO"
    print(f"{item_name}: DB = {db_total}, Report = {report_total} -> MATCH: {match}")
