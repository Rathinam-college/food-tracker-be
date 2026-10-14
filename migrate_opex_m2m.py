import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import OpexEntry

def migrate_data():
    entries = OpexEntry.objects.all()
    count = 0
    print(f"Starting migration for {entries.count()} entries...")
    for entry in entries:
        if entry.purchase_request and not entry.purchase_requests.exists():
            entry.purchase_requests.add(entry.purchase_request)
            count += 1
    print(f"Successfully migrated {count} entries.")

if __name__ == "__main__":
    migrate_data()
