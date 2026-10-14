import os
import django
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import (
    PurchaseRequest, Item, Vendor, Department, Inventory, 
    Procurement, StockMovement, User, MaterialRequest, 
    MaterialRequestItem, StockBatch, MRItemBatchAllocation
)
from django.utils import timezone

def test_full_chain_reversion():
    # Setup
    item = Item.objects.first()
    vendor = Vendor.objects.first()
    user = User.objects.first()
    dept_proc = Department.objects.get(code='procurement')
    dept_kitchen = Department.objects.get(code='kitchen')
    
    # Cleanup previous tests
    PurchaseRequest.objects.filter(pr_reference="TEST-REVERT-001").delete()
    MaterialRequest.objects.filter(mr_number="TEST-MR-001").delete()
    
    # Get initial inventory
    store_inv, _ = Inventory.objects.get_or_create(item=item, department=dept_proc)
    kitchen_inv, _ = Inventory.objects.get_or_create(item=item, department=dept_kitchen)
    
    initial_store_qty = store_inv.current_quantity
    initial_kitchen_qty = kitchen_inv.current_quantity
    print(f"Initial - Store: {initial_store_qty}, Kitchen: {initial_kitchen_qty}")
    
    # 1. Create and Receive PR
    pr = PurchaseRequest.objects.create(
        pr_reference="TEST-REVERT-001",
        vendor=vendor,
        order_date=timezone.now().date(),
        status='COMPLETED'
    )
    
    received_qty = Decimal('10.000')
    line_total = Decimal('100.00')
    
    # Store stock up
    store_inv.current_quantity += received_qty
    store_inv.inventory_value += line_total
    store_inv.save()
    
    proc = Procurement.objects.create(
        item=item,
        vendor=vendor,
        purchase_request=pr,
        quantity=received_qty,
        unit_price=Decimal('10.00'),
        tax_rate=Decimal('0.00'),
        total_price=line_total,
        purchase_date=timezone.now().date()
    )
    
    batch = StockBatch.objects.create(
        item=item,
        department=dept_proc,
        initial_quantity=received_qty,
        remaining_quantity=received_qty,
        unit_price=Decimal('10.00'),
        batch_date=timezone.now().date(),
        procurement=proc
    )
    
    StockMovement.objects.create(
        item=item, quantity=received_qty, type='STORE_INWARD',
        reference_id=pr.pr_reference, user=user, remarks=f"Rec: {pr.pr_reference}"
    )
    
    print(f"After Receipt - Store: {store_inv.current_quantity}, Kitchen: {kitchen_inv.current_quantity}")
    
    # 2. Issue to Kitchen
    mr = MaterialRequest.objects.create(
        mr_number="TEST-MR-001", department=dept_kitchen,
        requested_by=user, status='ISSUED', order_date=timezone.now().date(),
        target_date=timezone.now().date()
    )
    mr_item = MaterialRequestItem.objects.create(
        material_request=mr, item=item, quantity=Decimal('5.000'),
        quantity_issued=Decimal('5.000'), consumption_total_cost=Decimal('50.00')
    )
    
    # Adjust stock levels
    store_inv.current_quantity -= Decimal('5.000')
    store_inv.inventory_value -= Decimal('50.00')
    store_inv.save()
    
    kitchen_inv.current_quantity += Decimal('5.000')
    kitchen_inv.inventory_value += Decimal('50.00')
    kitchen_inv.save()
    
    batch.remaining_quantity -= Decimal('5.000')
    batch.save()
    
    MRItemBatchAllocation.objects.create(
        mr_item=mr_item, stock_batch=batch, quantity_allocated=Decimal('5.000')
    )
    
    StockMovement.objects.create(
        item=item, quantity=Decimal('5.000'), type='STORE_TO_KITCHEN',
        reference_id=mr.mr_number, user=user, remarks=f"Issue: {mr.mr_number}"
    )
    
    print(f"After Issue - Store: {store_inv.current_quantity}, Kitchen: {kitchen_inv.current_quantity}")
    
    # 3. Force Delete PR
    from core.views import PurchaseRequestViewSet
    viewset = PurchaseRequestViewSet()
    viewset.request = type('Request', (), {'user': user})()
    
    print("Force Deleting PR...")
    viewset._revert_pr_entries(pr)
    pr.delete()
    
    # Final check
    store_inv.refresh_from_db()
    kitchen_inv.refresh_from_db()
    print(f"Final - Store: {store_inv.current_quantity}, Kitchen: {kitchen_inv.current_quantity}")
    
    # Ledger check
    movements = StockMovement.objects.filter(item=item)
    print(f"Movements remaining for this item: {movements.count()}")
    for m in movements:
        print(f"  - {m.type} ({m.reference_id}) Qty: {m.quantity}")

if __name__ == "__main__":
    test_full_chain_reversion()
