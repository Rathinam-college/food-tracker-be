import os
import django
import sys

# Set up Django environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Item, Department, Inventory

def sync_inventory():
    print("Starting Inventory Synchronization...")
    items = Item.objects.all()
    departments = Department.objects.all()
    created_count = 0
    skipped_count = 0

    for item in items:
        for dept in departments:
            inv, created = Inventory.objects.get_or_create(
                item=item, 
                department=dept,
                defaults={'current_quantity': 0, 'min_stock_level': 0}
            )
            if created:
                created_count += 1
            else:
                skipped_count += 1

    print(f"Sync Complete!")
    print(f"Created: {created_count} missing records.")
    print(f"Skipped: {skipped_count} existing records.")

if __name__ == "__main__":
    sync_inventory()
