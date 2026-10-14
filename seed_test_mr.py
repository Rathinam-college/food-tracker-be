import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
django.setup()

from django.contrib.auth.models import User
from core.models import Department, Category, Item, MaterialRequest, MaterialRequestItem

def seed():
    admin = User.objects.get(username='admin')
    kitchen_dept = Department.objects.get(code='kitchen')

    cat, _ = Category.objects.get_or_create(name='Test Category')
    item, _ = Item.objects.get_or_create(name='Test Tomato', category=cat, unit='Kg', min_stock_level=5)

    mr = MaterialRequest.objects.create(
        mr_number="MR-TEST-001",
        requested_by=admin,
        department=kitchen_dept,
        status="PLACED",
        meal_type="Lunch"
    )
    MaterialRequestItem.objects.create(
        material_request=mr,
        item=item,
        quantity=10,
        unit="Kg"
    )
    print("Test MR created!")

if __name__ == "__main__":
    seed()
