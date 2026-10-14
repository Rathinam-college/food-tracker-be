from core.models import *
from django.db.models import Sum

mris = MaterialRequestItem.objects.filter(quantity_returned__gt=0)
print(f"Found {mris.count()} MRIs with returns")
for mri in mris:
    alloc_sum = mri.batch_allocations.aggregate(s=Sum('quantity_allocated'))['s'] or 0
    returns = mri.quantity_returned
    expected = mri.quantity_issued - returns
    print(f"MR {mri.material_request.mr_number}: Issued={mri.quantity_issued}, Returned={returns}, AllocSum={alloc_sum}, Expected={expected}")
