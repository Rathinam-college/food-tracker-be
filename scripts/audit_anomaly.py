import os
import django
from decimal import Decimal

# Initialize Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
try:
    django.setup()
except Exception:
    django.setup()

from core.models import MaterialRequest, StockMovement, Item
from django.db.models import Sum

def audit_anomaly():
    mr_num = "MR-20260709-0004"
    item_name = "Cinnamon Stick"

    mr = MaterialRequest.objects.filter(mr_number=mr_num).first()
    if not mr:
        print(f"MR {mr_num} not found!")
        return

    item = Item.objects.filter(name=item_name).first()
    if not item:
        print(f"Item {item_name} not found!")
        return

    mr_item = mr.items.filter(item=item).first()
    if not mr_item:
        print(f"Item {item_name} not found in MR {mr_num}")
        return

    print("=== MR ITEM DETAILS ===")
    print(f"Requested Qty: {mr_item.quantity}")
    print(f"Issued Qty   : {mr_item.quantity_issued}")
    print(f"Returned Qty : {mr_item.quantity_returned}")
    print(f"Deficit Qty  : {mr_item.deficit_quantity}")
    
    print("\n=== STOCK MOVEMENT LEDGER (STORE TO KITCHEN) ===")
    movements = StockMovement.objects.filter(
        reference_id=mr_num,
        item=item,
        type='STORE_TO_KITCHEN'
    ).order_by('id')

    total_qty = Decimal('0.000')
    for m in movements:
        print(f"ID: {m.id} | Date: {m.movement_date.strftime('%Y-%m-%d %H:%M:%S')} | User: {m.user.email} | Qty: {m.quantity} | Remarks: {m.remarks}")
        total_qty += m.quantity
        
    print(f"\nTotal Pushed in Ledger: {total_qty}")

if __name__ == '__main__':
    audit_anomaly()
