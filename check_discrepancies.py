import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Item, Inventory, StockBatch, StockMovement
from django.db.models import Sum

items = Item.objects.all()
print("Discrepancies Analysis:")
print(f"{'Item Name':<30} | {'Inv Qty':<10} | {'Batch Qty':<10} | {'Diff':<10} | {'Kitchen Returns':<15}")
print("-" * 80)

for item in items:
    inv = Inventory.objects.filter(item=item, department__code='procurement').first()
    inv_qty = inv.current_quantity if inv else 0
    
    batch_qs = StockBatch.objects.filter(item=item, department__code='procurement').aggregate(Sum('remaining_quantity'))
    batch_qty = batch_qs['remaining_quantity__sum'] or 0
    
    diff = inv_qty - batch_qty
    if diff != 0:
        kr_qs = StockMovement.objects.filter(item=item, type='KITCHEN_TO_STORE').aggregate(Sum('quantity'))
        kr = kr_qs['quantity__sum'] or 0
        
        print(f"{item.name[:28]:<30} | {float(inv_qty):<10.3f} | {float(batch_qty):<10.3f} | {float(diff):<10.3f} | {float(kr):<15.3f}")
