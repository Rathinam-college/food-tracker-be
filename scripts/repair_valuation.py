import os
import django
import sys
from decimal import Decimal

# Add the project directory to sys.path
sys.path.append(os.path.join(os.getcwd(), 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Item, Inventory, Procurement, MaterialRequestItem, Department

def repair():
    print("Starting Valuation Repair...")
    procurement_dept = Department.objects.get(code='procurement')
    
    items = Item.objects.all()
    for item in items:
        # 1. Find last purchase price
        last_proc_price = Procurement.objects.filter(item=item).values_list('unit_price', flat=True).order_by('-id').first()
        if last_proc_price is None:
            continue
            
        avg_price = Decimal(str(last_proc_price))
        print(f"Item: {item.name} | Last Price: {avg_price}")
        
        # 2. Update Inventory
        inv, _ = Inventory.objects.get_or_create(item=item, department=procurement_dept)
        if inv.average_price == 0:
            inv.average_price = avg_price
            inv.inventory_value = (inv.current_quantity * avg_price).quantize(Decimal('0.00'))
            inv.save()
            print(f"  -> Updated Inventory: Qty {inv.current_quantity}, Val {inv.inventory_value}")
            
        # 3. Update Material Request Items (Backfill)
        mr_items = MaterialRequestItem.objects.filter(
            item=item, 
            material_request__status__in=['ACCEPTED', 'COMPLETED'],
            consumption_unit_cost=0
        )
        
        for mr_item in mr_items:
            mr_item.consumption_unit_cost = avg_price
            consumed = mr_item.quantity_issued - mr_item.quantity_returned
            mr_item.consumption_total_cost = (consumed * avg_price).quantize(Decimal('0.00'))
            mr_item.save()
            print(f"  -> Updated MR Item {mr_item.id} for MR {mr_item.material_request.mr_number}")

    print("Repair Complete.")

if __name__ == "__main__":
    repair()
