import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
try:
    django.setup()
except Exception:
    pass

from core.models import MRItemBatchAllocation

def check_batch():
    allocations = MRItemBatchAllocation.objects.filter(stock_batch_id=1924)
    print("Allocations for Batch 1924 (50 Kg):")
    for a in allocations:
        mr = a.mr_item.material_request
        print(f"MR Ref: {mr.mr_number} | Qty Allocated: {a.quantity_allocated} | Issued On: {mr.created_at.date()}")

if __name__ == '__main__':
    check_batch()
