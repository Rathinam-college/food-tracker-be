import os
import django
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import PurchaseRequest, OpexEntry, Procurement
from core.serializers import OpexEntrySerializer

def test_workflow():
    pr = PurchaseRequest.objects.get(id=299)
    opex = OpexEntry.objects.get(id=43)
    
    print(f"Initial: PR Status: {pr.status}, Procurements in OPEX: {opex.procurements.count()}")
    
    # Simulate Receive (simplified version of what's in views.py)
    print("\nSimulating Receive...")
    for item in pr.items.all():
        Procurement.objects.create(
            item=item.item,
            vendor=pr.vendor,
            purchase_request=pr,
            quantity=item.quantity_ordered,
            unit_price=item.unit_price,
            total_price=item.quantity_ordered * item.unit_price,
            purchase_date=pr.order_date,
            opex_entry=None # This is the bug! It should probably be linked.
        )
    pr.status = 'RECEIVED'
    pr.save()
    
    # Check after receive
    opex.refresh_from_db()
    print(f"After Receive: PR Status: {pr.status}, Procurements in OPEX: {opex.procurements.count()}")
    
    # Serialized data
    serializer = OpexEntrySerializer(opex)
    print(f"Serialized Procurements Length: {len(serializer.data.get('procurements', []))}")

test_workflow()
