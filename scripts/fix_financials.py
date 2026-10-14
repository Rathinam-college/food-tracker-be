import os
import django
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
try:
    django.setup()
except Exception:
    pass

from core.models import (
    StockMovement, Inventory, StockBatch, MaterialRequest, Department, Item,
    PurchaseRequestItem, MaterialRequestItem, MRItemBatchAllocation, Procurement
)

def rebuild_financials():
    procurement_dept = Department.objects.get(code='procurement')
    kitchen_dept = Department.objects.get(code='kitchen')

    with transaction.atomic():
        print("--- PHASE 1: WIPE CORRUPTED DATA ---")
        
        # 1. Delete all StockMovements from PR and MR
        movements_pr = StockMovement.objects.filter(type='IN', reference_id__startswith='PR-')
        movements_mr = StockMovement.objects.filter(type='OUT', reference_id__startswith='MR-')
        pr_del = movements_pr.count()
        mr_del = movements_mr.count()
        movements_pr.delete()
        movements_mr.delete()
        print(f"Deleted {pr_del} PR ledger entries and {mr_del} MR ledger entries.")

        # 2. Delete all Batch Allocations
        alloc_del = MRItemBatchAllocation.objects.all().count()
        MRItemBatchAllocation.objects.all().delete()
        print(f"Deleted {alloc_del} batch allocations.")

        # 3. Delete all StockBatches from PR
        batch_del = StockBatch.objects.filter(procurement__isnull=False).count()
        StockBatch.objects.filter(procurement__isnull=False).delete()
        print(f"Deleted {batch_del} stock batches.")

        # 3.5 Restore Ghost Batches (Opening Stock)
        ghost_batches = StockBatch.objects.filter(procurement__isnull=True)
        ghost_count = ghost_batches.count()
        for gb in ghost_batches:
            gb.remaining_quantity = gb.initial_quantity
            gb.save()
            
            # Ensure it has a ledger entry
            StockMovement.objects.get_or_create(
                item=gb.item,
                type='IN',
                reference_id=f"OPENING-{gb.id}",
                defaults={
                    'quantity': gb.initial_quantity,
                    'movement_date': gb.batch_date,
                    'remarks': 'Opening Stock (Restored)'
                }
            )
        print(f"Restored {ghost_count} opening stock ghost batches.")

        print("\n--- PHASE 2: REBUILD PURCHASES (IN) ---")
        pr_items = PurchaseRequestItem.objects.select_related('purchase_request', 'item').filter(
            purchase_request__status__in=['RECEIVED', 'COMPLETED']
        ).order_by('purchase_request__order_date', 'purchase_request__id')
        
        for pr_item in pr_items:
            pr = pr_item.purchase_request
            qty = pr_item.quantity_received
            if qty <= 0:
                continue
                
            # 1. Create true Stock Movement
            StockMovement.objects.create(
                item=pr_item.item,
                quantity=qty,
                type='IN',
                reference_id=pr.pr_reference,
                movement_date=pr.created_at or pr.order_date
            )
            
            proc = Procurement.objects.filter(purchase_request=pr, item=pr_item.item).first()
            
            # 2. Create true Stock Batch
            StockBatch.objects.create(
                item=pr_item.item,
                department=procurement_dept,
                procurement=proc,
                initial_quantity=qty,
                remaining_quantity=qty,
                unit_price=pr_item.unit_price,
                batch_date=pr.order_date
            )
        print("Purchases and Batches successfully rebuilt.")

        print("\n--- PHASE 3: REBUILD MATERIAL REQUESTS (OUT) USING FIFO ---")
        mr_items = MaterialRequestItem.objects.select_related('material_request', 'item').filter(
            material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED'],
            quantity_issued__gt=0
        ).order_by('material_request__created_at', 'material_request__id')

        for mr_item in mr_items:
            mr = mr_item.material_request
            qty_to_issue = mr_item.quantity_issued
            
            # 1. Create true Stock Movement
            StockMovement.objects.create(
                item=mr_item.item,
                quantity=qty_to_issue,
                type='OUT',
                reference_id=mr.mr_number,
                movement_date=mr.updated_at or mr.created_at
            )
            
            # 2. True FIFO Allocation
            batches = StockBatch.objects.filter(
                item=mr_item.item,
                remaining_quantity__gt=0,
                department=procurement_dept
            ).order_by('batch_date', 'id')
            
            remaining_to_allocate = qty_to_issue
            total_cost = Decimal('0.00')
            
            for batch in batches:
                if remaining_to_allocate <= 0:
                    break
                    
                allocated_qty = min(batch.remaining_quantity, remaining_to_allocate)
                
                MRItemBatchAllocation.objects.create(
                    mr_item=mr_item,
                    stock_batch=batch,
                    quantity_allocated=allocated_qty
                )
                
                batch.remaining_quantity -= allocated_qty
                batch.save()
                
                total_cost += allocated_qty * batch.unit_price
                remaining_to_allocate -= allocated_qty
                
            mr_item.consumption_total_cost = total_cost
            mr_item.save()
            
        print("Material Requests successfully rebuilt with true FIFO logic.")

        print("\n--- PHASE 4: RECALIBRATE INVENTORY BALANCES ---")
        for item in Item.objects.all():
            # 1. Store Inventory Recalibration
            store_inv, _ = Inventory.objects.get_or_create(item=item, department=procurement_dept)
            
            store_qty = StockBatch.objects.filter(item=item, department=procurement_dept).aggregate(
                total=Sum('remaining_quantity')
            )['total'] or Decimal('0.000')
            
            store_val = Decimal('0.00')
            for batch in StockBatch.objects.filter(item=item, department=procurement_dept):
                store_val += batch.remaining_quantity * batch.unit_price
                
            store_inv.current_quantity = store_qty
            store_inv.inventory_value = store_val.quantize(Decimal('0.00'))
            if store_qty > 0:
                store_inv.average_price = (store_val / store_qty).quantize(Decimal('0.00'))
            else:
                store_inv.average_price = Decimal('0.00')
            store_inv.save()
            
            # 2. Kitchen Inventory Recalibration
            kitchen_inv, _ = Inventory.objects.get_or_create(item=item, department=kitchen_dept)
            
            kitchen_qty = Decimal('0.000')
            kitchen_val = Decimal('0.00')
            
            # active MRs
            active_mrs = MaterialRequest.objects.filter(
                status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE']
            )
            for mr in active_mrs:
                mi = mr.items.filter(item=item).first()
                if mi:
                    kitchen_qty += mi.quantity_issued
                    kitchen_val += mi.consumption_total_cost
                    
            kitchen_inv.current_quantity = kitchen_qty
            kitchen_inv.inventory_value = kitchen_val.quantize(Decimal('0.00'))
            if kitchen_qty > 0:
                kitchen_inv.average_price = (kitchen_val / kitchen_qty).quantize(Decimal('0.00'))
            else:
                kitchen_inv.average_price = Decimal('0.00')
            kitchen_inv.save()
            
        print("Inventory perfectly recalibrated.")
        print("\n=== SYSTEM REBUILD COMPLETE ===")

if __name__ == '__main__':
    rebuild_financials()
