import os
import django
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
django.setup()

from core.models import (
    StockMovement, Inventory, PurchaseRequestItem, MaterialRequestItem,
    VendorReturnItem, Department, Item, StockBatch
)

def reset_ledger():
    procurement_dept = Department.objects.get(code='procurement')
    
    with transaction.atomic():
        print("Wiping all StockMovements...")
        StockMovement.objects.all().delete()
        
        print("Rebuilding IN (Purchases)...")
        pr_items = PurchaseRequestItem.objects.filter(
            purchase_request__status__in=['RECEIVED', 'COMPLETED'],
            quantity_received__gt=0
        )
        for pri in pr_items:
            StockMovement.objects.create(
                item=pri.item,
                quantity=pri.quantity_received,
                type='IN',
                reference_id=pri.purchase_request.pr_reference,
                movement_date=pri.purchase_request.updated_at or pri.purchase_request.order_date
            )
            
        print("Rebuilding IN (Ghost Batches - Opening Stock)...")
        ghosts = StockBatch.objects.filter(procurement__isnull=True)
        for gb in ghosts:
            StockMovement.objects.create(
                item=gb.item,
                quantity=gb.initial_quantity,
                type='IN',
                reference_id=f"OPENING-{gb.id}",
                movement_date=gb.batch_date,
                remarks='Opening Stock (Restored)'
            )

        print("Rebuilding STORE_TO_KITCHEN (Issuances)...")
        mr_items = MaterialRequestItem.objects.filter(
            material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED'],
            quantity_issued__gt=0
        )
        for mri in mr_items:
            StockMovement.objects.create(
                item=mri.item,
                quantity=mri.quantity_issued,
                type='STORE_TO_KITCHEN',
                reference_id=mri.material_request.mr_number,
                movement_date=mri.material_request.updated_at or mri.material_request.created_at
            )
            
        print("Rebuilding VENDOR_RETURN...")
        vr_items = VendorReturnItem.objects.filter(quantity__gt=0)
        for vri in vr_items:
            StockMovement.objects.create(
                item=vri.item,
                quantity=vri.quantity,
                type='VENDOR_RETURN',
                reference_id=vri.vendor_return.return_reference,
                movement_date=vri.vendor_return.return_date
            )
            
        print("Rebuilding KITCHEN_TO_STORE (Kitchen Returns)...")
        kr_items = MaterialRequestItem.objects.filter(
            quantity_returned__gt=0
        )
        for kr in kr_items:
            StockMovement.objects.create(
                item=kr.item,
                quantity=kr.quantity_returned,
                type='KITCHEN_TO_STORE',
                reference_id=kr.material_request.mr_number,
                movement_date=kr.material_request.updated_at or kr.material_request.created_at
            )
            
        print("Recalculating Inventory.current_quantity from the true ledger...")
        for item in Item.objects.all():
            store_inv, _ = Inventory.objects.get_or_create(item=item, department=procurement_dept)
            
            purchased = StockMovement.objects.filter(item=item, type='IN').aggregate(s=Sum('quantity'))['s'] or Decimal('0')
            issued = StockMovement.objects.filter(item=item, type='STORE_TO_KITCHEN').aggregate(s=Sum('quantity'))['s'] or Decimal('0')
            vendor_ret = StockMovement.objects.filter(item=item, type='VENDOR_RETURN').aggregate(s=Sum('quantity'))['s'] or Decimal('0')
            kitchen_ret = StockMovement.objects.filter(item=item, type='KITCHEN_TO_STORE').aggregate(s=Sum('quantity'))['s'] or Decimal('0')
            wastage = StockMovement.objects.filter(item=item, type='WASTAGE').aggregate(s=Sum('quantity'))['s'] or Decimal('0')
            
            true_qty = purchased - issued + kitchen_ret - vendor_ret - wastage
            
            store_inv.current_quantity = true_qty
            store_inv.save()
            
        print("Done!")

if __name__ == '__main__':
    reset_ledger()
