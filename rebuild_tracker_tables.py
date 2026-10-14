import django, os
from decimal import Decimal
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Inventory, StockBatch, MRItemBatchAllocation, StockMovement, Procurement, MaterialRequestItem, Department, Item

def rebuild():
    print("Wiping tables...")
    Inventory.objects.all().delete()
    MRItemBatchAllocation.objects.all().delete()
    StockBatch.objects.all().delete()
    StockMovement.objects.all().delete()
    
    proc_dept = Department.objects.get(code='procurement')
    kitchen_dept = Department.objects.get(code='kitchen')
    
    print("Rebuilding Procurements...")
    procurements = Procurement.objects.all().order_by('purchase_date', 'created_at')
    for p in procurements:
        if not p.item: continue
        
        # Create StockBatch
        sb = StockBatch.objects.create(
            item=p.item,
            department=proc_dept,
            procurement=p,
            initial_quantity=p.quantity,
            remaining_quantity=p.quantity,
            unit_price=p.unit_price,
            batch_date=p.purchase_date
        )
        
        # Create StockMovement
        StockMovement.objects.create(
            item=p.item,
            type='STORE_INWARD',
            quantity=p.quantity,
            reference_id=str(p.id),
            remarks=f"Rebuilt from Procurement {p.id}"
        )
        
    print("Rebuilding Material Requests...")
    mris = MaterialRequestItem.objects.all().order_by('material_request__order_date', 'material_request__created_at')
    
    for mri in mris:
        item = mri.item
        if not item: continue
        
        # Issue
        issue_qty = mri.quantity_issued or Decimal('0.000')
        if issue_qty > 0:
            # Deduct via FIFO
            batches = StockBatch.objects.filter(item=item, department=proc_dept, remaining_quantity__gt=0).order_by('batch_date', 'id')
            rem_to_issue = issue_qty
            for b in batches:
                if rem_to_issue <= 0: break
                take = min(b.remaining_quantity, rem_to_issue)
                b.remaining_quantity -= take
                b.save()
                
                MRItemBatchAllocation.objects.create(
                    mr_item=mri,
                    stock_batch=b,
                    quantity_allocated=take
                )
                rem_to_issue -= take
                
            # Movement
            mr_date = mri.material_request.kitchen_accepted_at or mri.material_request.action_at or mri.material_request.created_at
            sm = StockMovement.objects.create(
                item=item,
                type='STORE_TO_KITCHEN',
                quantity=issue_qty,
                reference_id=str(mri.id),
                remarks=f"Rebuilt Issue from MRI {mri.id}"
            )
            sm.movement_date = mr_date
            sm.save(update_fields=['movement_date'])
            
        # Return
        ret_qty = mri.quantity_returned or Decimal('0.000')
        if ret_qty > 0:
            # Add back via LIFO (most recent batch)
            b = StockBatch.objects.filter(item=item, department=proc_dept).order_by('-batch_date', '-id').first()
            if b:
                b.remaining_quantity += ret_qty
                b.save()
                
            ret_date = mri.material_request.updated_at or mri.material_request.created_at
            sm = StockMovement.objects.create(
                item=item,
                type='KITCHEN_TO_STORE',
                quantity=ret_qty,
                reference_id=str(mri.id),
                remarks=f"Rebuilt Return from MRI {mri.id}"
            )
            sm.movement_date = ret_date
            sm.save(update_fields=['movement_date'])
            
    print("Rebuilding Inventory Balances...")
    for item in Item.objects.all():
        # Procurement Inv
        batch_sum = StockBatch.objects.filter(item=item, department=proc_dept).aggregate(s=django.db.models.Sum('remaining_quantity'))['s'] or Decimal('0.000')
        Inventory.objects.create(
            item=item,
            department=proc_dept,
            current_quantity=batch_sum,
            min_stock_level=0
        )
        
        # Kitchen Inv
        mris_item = MaterialRequestItem.objects.filter(item=item)
        k_in = mris_item.aggregate(s=django.db.models.Sum('quantity_issued'))['s'] or Decimal('0.000')
        k_out = mris_item.aggregate(s=django.db.models.Sum('quantity_returned'))['s'] or Decimal('0.000')
        k_bal = k_in - k_out
        
        Inventory.objects.create(
            item=item,
            department=kitchen_dept,
            current_quantity=k_bal,
            min_stock_level=0
        )
        
    print("Rebuild Complete!")

if __name__ == '__main__':
    rebuild()
