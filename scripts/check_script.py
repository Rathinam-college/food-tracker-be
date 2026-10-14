import pandas as pd
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import PurchaseRequestItem, MaterialRequestItem, Item
from collections import defaultdict
from decimal import Decimal
from datetime import datetime

df_download = pd.read_excel('C:/Users/janar/Downloads/RathinamCampus/June Closing_Stock.xlsx')
download_dict = dict(zip(df_download['Item Name'], df_download['Current Stock']))

dates_to_check = ['2026-06-30', '2026-07-01', '2026-07-02', '2026-07-15']

for d in dates_to_check:
    print(f'\nChecking date: {d}')
    mismatches = 0
    items = Item.objects.all()
    for item in items:
        purchases = PurchaseRequestItem.objects.filter(
            item=item,
            quantity_received__gt=0,
            purchase_request__order_date__lte=d
        ).exclude(purchase_request__status__in=['DRAFT', 'CANCELLED'])
        total_purchased = sum(p.quantity_received for p in purchases) or Decimal('0.00')

        mrs = MaterialRequestItem.objects.filter(
            item=item,
            material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED'],
            material_request__target_date__lte=d
        )
        total_consumed = Decimal('0.00')
        for mr in mrs:
            issued = mr.quantity_issued or Decimal('0.00')
            returned = mr.quantity_returned or Decimal('0.00')
            total_consumed += (issued - returned)

        expected = float(total_purchased - total_consumed)
        
        if item.name in download_dict:
            download_val = float(download_dict[item.name])
            if abs(expected - download_val) > 0.01:
                mismatches += 1

    print(f'Mismatches for {d}: {mismatches}')
