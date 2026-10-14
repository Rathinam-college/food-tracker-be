import os
import django
import datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from django.contrib.auth.models import User
from core.models import Department, Role, Profile, Category, Item, MaterialRequest, MaterialRequestItem

# 1. Ensure Kitchen Department exists
kitchen_dept, _ = Department.objects.get_or_create(
    code='kitchen',
    defaults={
        'name': 'Kitchen Department',
        'description': 'Kitchen management',
        'logo': '🍳',
        'display_name': 'Kitchen'
    }
)

# 2. Create Kitchen Role
kitchen_role, _ = Role.objects.get_or_create(
    name='Kitchen Staff',
    defaults={
        'can_place_mr': True,
        'can_view_mr': True,
        'can_view_stock': True,
    }
)

# 3. Create a Kitchen User
user, created = User.objects.get_or_create(
    username='chef_john',
    defaults={'email': 'john@kitchen.com', 'first_name': 'John', 'last_name': 'Doe'}
)
if created:
    user.set_password('chef_password_123')
    user.save()

# Ensure Profile and Role
profile, _ = Profile.objects.get_or_create(user=user)
profile.role = kitchen_role
profile.departments.add(kitchen_dept)
profile.save()

# 4. Create an Item
category, _ = Category.objects.get_or_create(name='Vegetables')
item, _ = Item.objects.get_or_create(
    name='Tomato',
    defaults={'category': category, 'unit': 'Kg'}
)

# 5. CREATE MATERIAL REQUEST (The core of the request)
mr_number = 'MR-20231027-0001'
mr, created = MaterialRequest.objects.get_or_create(
    mr_number=mr_number,
    defaults={
        'order_date': datetime.date.today(),
        'target_date': datetime.date.today(),
        'meal_type': 'LUNCH',
        'status': 'PLACED',
        'requested_by': user,
        'remarks': 'Testing MR placement'
    }
)

# 6. CREATE MATERIAL REQUEST ITEMS
mr_item, _ = MaterialRequestItem.objects.get_or_create(
    material_request=mr,
    item=item,
    defaults={
        'quantity': 10.5,
        'unit': 'Kg'
    }
)

print(f"--- DATABASE VERIFICATION ---")
print(f"MaterialRequest record: MR Number: {mr.mr_number}, Status: {mr.status}, Requested By: {mr.requested_by.username}")
print(f"MaterialRequestItem record: Item: {mr_item.item.name}, Quantity: {mr_item.quantity}, Unit: {mr_item.unit}")

# Querying the actual database tables via Django ORM (which reflects the underlying tables)
print(f"\nChecking 'core_materialrequest' count: {MaterialRequest.objects.filter(mr_number=mr_number).count()}")
print(f"Checking 'core_materialrequestitem' count: {MaterialRequestItem.objects.filter(material_request=mr).count()}")
