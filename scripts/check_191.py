import os
import django
from decimal import Decimal
from django.db.models import Sum

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
try:
    django.setup()
except Exception:
    pass

from core.models import Item, PurchaseRequestItem, MaterialRequestItem, StockBatch, Inventory, StockMovement

def check_item_191():
    try:
        item = Item.objects.get(id=191)
        print(f"Item 191: {item.name} ({item.unit})")
        
        # 1. Total Purchased (IN)
        pr_items = PurchaseRequestItem.objects.filter(
            item=item,
            purchase_request__status__in=['RECEIVED', 'COMPLETED']
        )
        total_purchased = pr_items.aggregate(total=Sum('quantity_received'))['total'] or Decimal('0.00')
        print(f"Total Purchased (from PR): {total_purchased}")
        
        # 2. Total Issued (OUT)
        mr_items = MaterialRequestItem.objects.filter(
            item=item,
            material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED']
        )
        total_issued = mr_items.aggregate(total=Sum('quantity_issued'))['total'] or Decimal('0.00')
        print(f"Total Issued (from MR): {total_issued}")
        
        # 3. Expected True Balance
        expected_balance = total_purchased - total_issued
        print(f"Expected True Store Balance (PR - MR): {expected_balance}")
        
        # 4. Actual Inventory Table Balance
        store_inv = Inventory.objects.filter(item=item, department__code='procurement').first()
        store_qty = store_inv.current_quantity if store_inv else Decimal('0.00')
        print(f"Actual Inventory Table Store Balance: {store_qty}")
        
        # 5. Stock Movement Ledger Balance
        ledger_in = StockMovement.objects.filter(item=item, type='IN').aggregate(total=Sum('quantity'))['total'] or Decimal('0.00')
        ledger_out = StockMovement.objects.filter(item=item, type='OUT').aggregate(total=Sum('quantity'))['total'] or Decimal('0.00')
        print(f"Ledger Balance (IN - OUT): {ledger_in} - {ledger_out} = {ledger_in - ledger_out}")
        
        # 6. Batches Remaining
        batches = StockBatch.objects.filter(item=item, department__code='procurement')
        batch_total = batches.aggregate(total=Sum('remaining_quantity'))['total'] or Decimal('0.00')
        print(f"Sum of Remaining Batches: {batch_total}")
        
    except Item.DoesNotExist:
        print("Item 191 does not exist.")

if __name__ == '__main__':
    check_item_191()
