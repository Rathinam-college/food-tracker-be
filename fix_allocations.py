from core.models import MaterialRequestItem, MRItemBatchAllocation, StockBatch
from django.db import transaction
from decimal import Decimal

@transaction.atomic
def fix_allocations():
    print("Starting duplicate allocation cleanup...")
    
    # 1. Iterate over all MR Items that have issues
    items = MaterialRequestItem.objects.filter(quantity_issued__gt=0)
    
    deleted_count = 0
    batches_to_update = set()
    
    for mr_item in items:
        expected_issued = mr_item.quantity_issued
        allocations = MRItemBatchAllocation.objects.filter(mr_item=mr_item).order_by('id')
        
        running_sum = Decimal('0.000')
        
        for alloc in allocations:
            if running_sum >= expected_issued:
                # We have already fulfilled this MR, this allocation is a duplicate
                batches_to_update.add(alloc.stock_batch_id)
                alloc.delete()
                deleted_count += 1
                continue
                
            # If adding this allocation exceeds the expected (should not happen normally unless data is really weird)
            if running_sum + alloc.quantity_allocated > expected_issued:
                excess = (running_sum + alloc.quantity_allocated) - expected_issued
                alloc.quantity_allocated = expected_issued - running_sum
                alloc.save()
                batches_to_update.add(alloc.stock_batch_id)
                running_sum += alloc.quantity_allocated
            else:
                running_sum += alloc.quantity_allocated
                batches_to_update.add(alloc.stock_batch_id)
                
    print(f"Deleted {deleted_count} duplicate MRItemBatchAllocation records.")
    
    # 2. Recalculate StockBatch remaining quantities
    print(f"Recalculating {StockBatch.objects.count()} Stock Batches...")
    updated_batches = 0
    
    for batch in StockBatch.objects.all():
        total_allocations = sum(a.quantity_allocated for a in batch.allocations.all())
        expected_remaining = batch.initial_quantity - total_allocations
        
        if batch.remaining_quantity != expected_remaining:
            batch.remaining_quantity = expected_remaining
            batch.save(update_fields=['remaining_quantity'])
            updated_batches += 1
            
    print(f"Updated remaining_quantity for {updated_batches} StockBatches.")

if __name__ == '__main__':
    fix_allocations()
