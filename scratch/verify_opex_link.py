import os
import django
import json
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import PurchaseRequest, OpexEntry, Procurement
from core.views import PurchaseRequestViewSet
from rest_framework.test import APIRequestFactory, force_authenticate
from django.contrib.auth.models import User

def verify_auto_link():
    # 1. Setup data
    # We use PR 299 and OPEX 43 which exist in the user's DB
    pr = PurchaseRequest.objects.get(id=299)
    opex = OpexEntry.objects.get(id=43)
    user = User.objects.filter(is_superuser=True).first()
    
    # Ensure no procurements exist for this PR currently
    pr.procurements.all().delete()
    pr.status = 'PLACED'
    pr.save()
    
    print(f"Pre-test: PR {pr.pr_reference} Status: {pr.status}, OPEX {opex.opex_no} Items: {opex.procurements.count()}")
    
    # 2. Simulate the "Receive" API call
    factory = APIRequestFactory()
    items_data = []
    for item in pr.items.all():
        items_data.append({
            'item_id': item.item_id,
            'received_qty': float(item.quantity_ordered),
            'received_price': float(item.unit_price)
        })
    
    payload = {
        'items': json.dumps(items_data),
        'invoice_number': 'INV-TEST-LINK',
        'purchase_date': '2026-05-14',
        'short_close': True
    }
    
    request = factory.post(f'/api/purchase-requests/{pr.id}/receive/', payload, format='multipart')
    force_authenticate(request, user=user)
    
    view = PurchaseRequestViewSet.as_view({'post': 'receive'})
    response = view(request, pk=pr.id)
    
    print(f"API Response Status: {response.status_code}")
    
    # 3. Verify the link
    opex.refresh_from_db()
    linked_count = opex.procurements.count()
    print(f"Post-test: OPEX {opex.opex_no} Items count: {linked_count}")
    
    if linked_count > 0:
        print("SUCCESS: Procurements were automatically linked to the OPEX entry!")
    else:
        print("FAILURE: Procurements were NOT linked.")

if __name__ == "__main__":
    verify_auto_link()
