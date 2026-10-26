from core.models import *
from django.db.models import Sum

mris = MaterialRequestItem.objects.all()
count = 0
for mri in mris:
    alloc_sum = mri.batch_allocations.aggregate(s=Sum('quantity_allocated'))['s'] or 0
    returns = StockMovement.objects.filter(type='KITCHEN_TO_STORE', item=mri.item, reference_id=mri.material_request.mr_number).aggregate(s=Sum('quantity'))['s'] or 0
    expected_alloc = mri.quantity_issued - returns
    if alloc_sum != expected_alloc:
        print(f'MR {mri.material_request.mr_number} Item {mri.item.name}: Alloc={alloc_sum}, Expected={expected_alloc}')
        diff = expected_alloc - alloc_sum
        first_alloc = mri.batch_allocations.first()
        if first_alloc:
            first_alloc.quantity_allocated += diff
            first_alloc.save()
            count += 1
            print(f'Fixed {count} corrupted allocations.')
