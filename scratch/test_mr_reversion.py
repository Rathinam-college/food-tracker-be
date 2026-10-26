import os
import django
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import (
    Item, Department, Inventory, MaterialRequest, 
    MaterialRequestItem, StockMovement, User
)
from django.utils import timezone

def test_mr_reversion():
    item = Item.objects.first()
    user = User.objects.first()
    dept_proc = Department.objects.get(code='procurement')
    dept_kitchen = Department.objects.get(code='kitchen')
    
    # Cleanup
    MaterialRequest.objects.filter(mr_number="TEST-MR-REVERT-001").delete()
    
    # Get initial inventory
    store_inv, _ = Inventory.objects.get_or_create(item=item, department=dept_proc)
    kitchen_inv, _ = Inventory.objects.get_or_create(item=item, department=dept_kitchen)
    
    # Reset to 10 for clean test
    store_inv.current_quantity = Decimal('10.000')
    store_inv.inventory_value = Decimal('100.00')
    store_inv.save()
    
    kitchen_inv.current_quantity = Decimal('0.000')
    kitchen_inv.inventory_value = Decimal('0.00')
    kitchen_inv.save()
    
    print(f"Initial - Store: {store_inv.current_quantity}, Kitchen: {kitchen_inv.current_quantity}")
    
    # 1. Create and Issue MR
    mr = MaterialRequest.objects.create(
        mr_number="TEST-MR-REVERT-001", department=dept_kitchen,
        requested_by=user, status='ISSUED', order_date=timezone.now().date(),
        target_date=timezone.now().date()
    )
    mr_item = MaterialRequestItem.objects.create(
        material_request=mr, item=item, quantity=Decimal('4.000'),
        quantity_issued=Decimal('4.000'), consumption_total_cost=Decimal('40.00')
    )
    
    # Simulate issue effects
    store_inv.current_quantity -= Decimal('4.000')
    store_inv.inventory_value -= Decimal('40.00')
    store_inv.save()
    
    kitchen_inv.current_quantity += Decimal('4.000')
    kitchen_inv.inventory_value += Decimal('40.00')
    kitchen_inv.save()
    
    StockMovement.objects.create(
        item=item, quantity=Decimal('4.000'), type='STORE_TO_KITCHEN',
        reference_id=mr.mr_number, user=user, remarks=f"Issue: {mr.mr_number}"
    )
    
    print(f"After Issue - Store: {store_inv.current_quantity}, Kitchen: {kitchen_inv.current_quantity}")
    
    # 2. Delete MR
    print("Deleting MR...")
    mr.delete()
    
    # Final check
    store_inv.refresh_from_db()
    kitchen_inv.refresh_from_db()
    print(f"Final - Store: {store_inv.current_quantity}, Kitchen: {kitchen_inv.current_quantity}")
    
    movements_count = StockMovement.objects.filter(reference_id="TEST-MR-REVERT-001").count()
    print(f"Movements count: {movements_count}")

if __name__ == "__main__":
    test_mr_reversion()
