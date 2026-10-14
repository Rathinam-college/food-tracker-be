import os
import django
import sys

# Initialize Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
try:
    django.setup()
except Exception:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
    django.setup()

from core.models import StockMovement
from django.db.models import Count

def find_duplicates():
    # Find combinations of reference_id, item, and type that have more than 1 entry
    duplicates = StockMovement.objects.values('reference_id', 'item__name', 'type', 'quantity').annotate(
        count=Count('id')
    ).filter(count__gt=1, reference_id__isnull=False).exclude(reference_id='').order_by('-count')

    if not duplicates:
        print("No duplicate entries found!")
        return

    print(f"{'Reference ID':<20} | {'Item Name':<25} | {'Type':<18} | {'Qty':<10} | {'Count'}")
    print("-" * 90)
    
    total_dupes = 0
    for d in duplicates:
        print(f"{d['reference_id']:<20} | {d['item__name']:<25} | {d['type']:<18} | {d['quantity']:<10} | {d['count']} times")
        total_dupes += (d['count'] - 1)
        
    print("-" * 90)
    print(f"Total duplicate excess instances found: {total_dupes}")

if __name__ == '__main__':
    find_duplicates()
