from core.models import Inventory, StockBatch, StockMovement, Department, MaterialRequest, MaterialRequestItem
from django.db import transaction
from django.db.models import Sum, Count
from decimal import Decimal

@transaction.atomic
def fix_financials():
    print("Starting financial and aggregate inventory fix...")
    
    try:
        procurement_dept = Department.objects.get(code='procurement')
        kitchen_dept = Department.objects.get(code='kitchen')
    except Department.DoesNotExist:
        print("Could not find procurement or kitchen departments.")
        return

    # 1. Fix Store (Procurement) Inventory Aggregate
    print("\n--- Fixing Store Inventory ---")
    store_inventories = Inventory.objects.filter(department=procurement_dept)
    store_updated_count = 0
    
    for inv in store_inventories:
        batches = StockBatch.objects.filter(item=inv.item, department=procurement_dept)
        
        true_quantity = Decimal('0.000')
        true_value = Decimal('0.00')
        
        for batch in batches:
            true_quantity += batch.remaining_quantity
            true_value += (batch.remaining_quantity * batch.unit_price)
            
        true_value = true_value.quantize(Decimal('0.00'))
        
        needs_update = False
        if inv.current_quantity != true_quantity:
            inv.current_quantity = true_quantity
            needs_update = True
        
        if inv.inventory_value != true_value:
            inv.inventory_value = true_value
            needs_update = True
            
        if true_quantity > 0:
            avg_price = (true_value / true_quantity).quantize(Decimal('0.00'))
        else:
            avg_price = Decimal('0.00')
            
        if inv.average_price != avg_price:
            inv.average_price = avg_price
            needs_update = True
            
        if needs_update:
            inv.save()
            store_updated_count += 1
            
    print(f"Updated {store_updated_count} Store Inventory records to match exact StockBatch balances.")

    # 2. Fix Duplicate Stock Movements and Kitchen Inventory
    print("\n--- Fixing Duplicate Stock Movements & Kitchen Inventory ---")
    
    # Find combinations of reference_id (MR number) and item_id that have >1 movement
    duplicate_groups = StockMovement.objects.filter(
        type='STORE_TO_KITCHEN'
    ).values('reference_id', 'item_id').annotate(
        count=Count('id')
    ).filter(count__gt=1)
    
    deleted_movements = 0
    kitchen_updated_count = 0
    
    for group in duplicate_groups:
        mr_number = group['reference_id']
        item_id = group['item_id']
        
        # Get all movements for this group ordered by ID (oldest first)
        movements = list(StockMovement.objects.filter(
            type='STORE_TO_KITCHEN',
            reference_id=mr_number,
            item_id=item_id
        ).order_by('id'))
        
        # Keep the first one, delete the rest
        original_movement = movements[0]
        duplicates_to_delete = movements[1:]
        
        # Find the MRItem to know how much financial value to deduct from Kitchen
        try:
            mr_item = MaterialRequestItem.objects.get(
                material_request__mr_number=mr_number,
                item_id=item_id
            )
            cost_to_deduct = mr_item.consumption_total_cost
        except MaterialRequestItem.DoesNotExist:
            print(f"Warning: Could not find MRItem for MR {mr_number} and item {item_id}. Skipping financial deduction.")
            continue
            
        for dup in duplicates_to_delete:
            qty_to_deduct = dup.quantity
            
            # Deduct from Kitchen Inventory
            try:
                kitchen_inv = Inventory.objects.get(item_id=item_id, department=kitchen_dept)
                kitchen_inv.current_quantity -= qty_to_deduct
                kitchen_inv.inventory_value -= cost_to_deduct
                
                if kitchen_inv.current_quantity > 0:
                    kitchen_inv.average_price = (kitchen_inv.inventory_value / kitchen_inv.current_quantity).quantize(Decimal('0.00'))
                else:
                    kitchen_inv.average_price = Decimal('0.00')
                    
                kitchen_inv.save()
                kitchen_updated_count += 1
            except Inventory.DoesNotExist:
                pass
            
            # Delete the duplicate movement
            dup.delete()
            deleted_movements += 1
            
    print(f"Deleted {deleted_movements} duplicate StockMovement records.")
    print(f"Corrected {kitchen_updated_count} Kitchen Inventory records by reversing duplicate deductions.")
    print("\nFinancial Fix Complete!")

if __name__ == '__main__':
    fix_financials()
