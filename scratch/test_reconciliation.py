import os
import django
import sys
from decimal import Decimal
from django.utils import timezone

# Add the current directory to sys.path
sys.path.append(os.getcwd())

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Item, Inventory, MaterialRequest, MaterialRequestItem, Department, PurchaseRequest, PurchaseRequestItem, StockBatch, Procurement, Category

def run_test():
    print("--- Starting Negative Stock Reconciliation Test ---")
    
    # 1. Setup Data
    cat, _ = Category.objects.get_or_create(name="Test Category")
    item, _ = Item.objects.get_or_create(name="Test Reconciliation Salt", unit="kg", defaults={'category': cat})
    store_dept = Department.objects.get(code='procurement')
    kitchen_dept = Department.objects.get(code='kitchen')
    
    # Reset inventory to 0
    inv, _ = Inventory.objects.get_or_create(item=item, department=store_dept)
    inv.current_quantity = Decimal('0.00')
    inv.inventory_value = Decimal('0.00')
    inv.average_price = Decimal('10.00') # Assume some last known price
    inv.save()
    
    print(f"Initial Store Inventory: {inv.current_quantity} kg @ avg Rs.{inv.average_price}")

    # 2. Kitchen Material Request
    from core.models import Institution
    inst, _ = Institution.objects.get_or_create(name="Test Institution")
    mr = MaterialRequest.objects.create(
        mr_number="MR-TEST-001",
        department=kitchen_dept,
        status='MGMT_APPROVED', # Skip approval for speed
        requested_by_id=1, # Assume admin exists
        order_date=timezone.now().date(),
        target_date=timezone.now().date(),
        institution=inst
    )
    mri = MaterialRequestItem.objects.create(
        material_request=mr,
        item=item,
        quantity=Decimal('5.00')
    )
    
    print(f"Created Kitchen MR for 5kg {item.name}")

    # 3. Simulate Force Issue (Partial implementation of views.py logic)
    # We'll call the actual logic if possible, but for a script it's better to manually trigger it 
    # OR simulate the steps we just wrote.
    
    force_issue = True
    total_allocated = Decimal('0.00') # No batches
    total_cost = (mri.quantity * inv.average_price).quantize(Decimal('0.00')) # ₹50.00
    
    # Record deficit
    mri.deficit_quantity = mri.quantity - total_allocated
    mri.deficit_estimated_unit_cost = inv.average_price
    mri.quantity_issued = mri.quantity
    mri.consumption_total_cost = total_cost
    mri.consumption_unit_cost = (total_cost / mri.quantity).quantize(Decimal('0.00'))
    mri.save()
    
    # Update Store Inventory
    inv.current_quantity -= mri.quantity
    inv.inventory_value -= total_cost
    inv.save()
    
    print(f"Force Issued 5kg. MRI Cost: ₹{mri.consumption_total_cost} (Unit: ₹{mri.consumption_unit_cost})")
    print(f"Deficit Quantity: {mri.deficit_quantity} kg")
    print(f"Store Inventory now: {inv.current_quantity} kg, Value: ₹{inv.inventory_value}")

    # 4. Procurement receives stock at a DIFFERENT price
    print("\n--- Procurement receives new stock ---")
    received_qty = Decimal('10.00')
    received_price = Decimal('14.00') # Higher than estimate ₹10
    line_total = received_qty * received_price # ₹140.00
    
    # Update Inventory
    inv.current_quantity += received_qty
    inv.inventory_value += line_total
    # (average price calc skipped here as it's done at the end of satisfy debt in views.py)
    
    # Satisfy Debt (The logic we just wrote)
    debt = abs(min(Decimal('0'), inv.current_quantity - received_qty)) # 5kg debt
    batch_unit_price = (line_total / received_qty).quantize(Decimal('0.0000')) # ₹14.00
    
    if debt > 0:
        print(f"Satisfying {debt}kg debt at ₹{batch_unit_price}/kg...")
        debt_items = MaterialRequestItem.objects.filter(item=item, deficit_quantity__gt=0).order_by('material_request__created_at')
        for di in debt_items:
            to_fill = min(debt, di.deficit_quantity)
            price_diff = (to_fill * (batch_unit_price - di.deficit_estimated_unit_cost)).quantize(Decimal('0.00'))
            
            di.consumption_total_cost += price_diff
            di.consumption_unit_cost = (di.consumption_total_cost / di.quantity_issued).quantize(Decimal('0.00'))
            di.deficit_quantity -= to_fill
            di.save()
            
            inv.inventory_value -= price_diff
            print(f"  MRI {di.id} updated: Total Cost ₹{di.consumption_total_cost}, Unit Cost ₹{di.consumption_unit_cost}")
            print(f"  Price Diff Adjusted in Store: ₹{price_diff}")

    # Final Inventory Average Price
    if inv.current_quantity > 0:
        inv.average_price = (inv.inventory_value / inv.current_quantity).quantize(Decimal('0.00'))
    inv.save()

    print(f"\nFinal Store Inventory: {inv.current_quantity} kg @ avg ₹{inv.average_price}")
    print(f"Total Store Value: ₹{inv.inventory_value}")
    
    # 5. Verification
    # Expected MRI cost: 5kg * ₹14 = ₹70
    # Expected Store Inventory Value: 5kg remaining * ₹14 = ₹70
    # (Because 140 was inwarded, 20 was "true-up" adjustment, 50 was original deduction. 140 - 20 - 50 = 70. Correct!)
    
    if mri.consumption_total_cost == Decimal('70.00'):
        print("\nSUCCESS: MRI cost retroactively updated correctly!")
    else:
        print(f"\nFAILURE: MRI cost is {mri.consumption_total_cost}, expected 70.00")

if __name__ == "__main__":
    run_test()
