import os
import django
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import PurchaseRequest, Item, Vendor, Department, Inventory, Procurement, StockMovement, User
from django.utils import timezone

def test_pr_reversion():
    # Setup
    item = Item.objects.first()
    vendor = Vendor.objects.first()
    user = User.objects.first()
    dept_proc = Department.objects.get(code='procurement')
    
    # Cleanup previous tests
    PurchaseRequest.objects.filter(pr_reference="TEST-REVERT-001").delete()
    
    # Get initial inventory
    inv, _ = Inventory.objects.get_or_create(item=item, department=dept_proc)
    initial_qty = inv.current_quantity
    print(f"Initial Inventory Qty: {initial_qty}")
    
    # 1. Create a PR
    pr = PurchaseRequest.objects.create(
        pr_reference="TEST-REVERT-001",
        vendor=vendor,
        order_date=timezone.now().date(),
        status='PLACED'
    )
    
    # 2. Simulate Receiving
    received_qty = Decimal('10.000')
    line_total = Decimal('100.00')
    
    # Manual update inventory (like in views.py receive)
    inv.current_quantity += received_qty
    inv.inventory_value += line_total
    inv.save()
    
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
    
    # Log movement
    StockMovement.objects.create(
        item=item,
        quantity=received_qty,
        type='STORE_INWARD',
        reference_id=pr.pr_reference,
        user=user,
        remarks=f"Received via PR: {pr.pr_reference}"
    )
    
    print(f"Inventory Qty after Receipt: {inv.current_quantity}")
    
    # 3. Simulate Deletion (using the ViewSet logic)
    from core.views import PurchaseRequestViewSet
    viewset = PurchaseRequestViewSet()
    viewset.request = type('Request', (), {'user': user})() # Mock request
    
    print("Deleting PR...")
    viewset._revert_pr_entries(pr)
    pr.delete()
    
    # Final check
    inv.refresh_from_db()
    print(f"Final Inventory Qty: {inv.current_quantity}")
    
    movements_count = StockMovement.objects.filter(reference_id="TEST-REVERT-001").count()
    print(f"Movements count: {movements_count}")
    
    proc_count = Procurement.objects.filter(purchase_request_id=pr.id).count()
    print(f"Procurement count: {proc_count}")

if __name__ == "__main__":
    test_pr_reversion()
