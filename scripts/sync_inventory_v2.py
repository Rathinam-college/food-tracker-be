import os
import django
import sys
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Inventory, StockMovement, Department, Item
from django.db.models import Sum

procurement = Department.objects.get(code='procurement')
items = Item.objects.all()

for item in items:
    purchased = StockMovement.objects.filter(item=item, type='IN').aggregate(s=Sum('quantity'))['s'] or Decimal('0')
    issued = StockMovement.objects.filter(item=item, type='STORE_TO_KITCHEN').aggregate(s=Sum('quantity'))['s'] or Decimal('0')
    returns = StockMovement.objects.filter(item=item, type='KITCHEN_TO_STORE').aggregate(s=Sum('quantity'))['s'] or Decimal('0')
    wastage = StockMovement.objects.filter(item=item, type='WASTAGE').aggregate(s=Sum('quantity'))['s'] or Decimal('0')
    vendor_ret = StockMovement.objects.filter(item=item, type='VENDOR_RETURN').aggregate(s=Sum('quantity'))['s'] or Decimal('0')
    
    current_balance = purchased - issued + returns - wastage - vendor_ret
    inv, _ = Inventory.objects.get_or_create(item=item, department=procurement)
    inv.current_quantity = current_balance
    inv.save()
    
print("Successfully synced all Inventory.current_quantity values to the true mathematical Ledger math.")
