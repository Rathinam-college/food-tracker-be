from core.models import *
from django.db.models import Sum

mris = MaterialRequestItem.objects.all()
for mri in mris:
    alloc_sum = mri.batch_allocations.aggregate(s=Sum('quantity_allocated'))['s'] or 0
    returns = StockMovement.objects.filter(type='KITCHEN_TO_STORE', item=mri.item, reference_id=mri.material_request.mr_number).aggregate(s=Sum('quantity'))['s'] or 0
    if returns > 0:
        expected_alloc = mri.quantity_issued - returns
        print(f'MR {mri.material_request.mr_number} Item {mri.item.name}: Alloc={alloc_sum}, Expected={expected_alloc}, Returns={returns}, Issued={mri.quantity_issued}')
