import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import PurchaseRequestItem
item = PurchaseRequestItem.objects.first()
if item:
    print("Item:", item.item.name)
    print("Qty ordered:", item.quantity_ordered)
    print("Qty received:", item.quantity_received)
    print("Unit Price:", item.unit_price)
    print("Tax rate:", item.tax_rate)
    print("Discount:", item.discount_value)
    print("Line Total:", item.line_total)
else:
    print("No items")
