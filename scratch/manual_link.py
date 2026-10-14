import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import OpexEntry

opex = OpexEntry.objects.get(id=43)
prs = opex.purchase_requests.all()
print(f"Before: {opex.procurements.count()}")
for pr in prs:
    pr.procurements.filter(opex_entry__isnull=True).update(opex_entry=opex)
print(f"After: {opex.procurements.count()}")
