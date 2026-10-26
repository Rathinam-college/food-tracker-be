from django.contrib.auth.models import User
from core.models import Profile, Department, Category, Item, MaterialRequest, MaterialRequestItem
from django.utils import timezone
from decimal import Decimal

# Get essential objects
d_kitchen = Department.objects.get(code='kitchen')
c_veg, _ = Category.objects.get_or_create(name='Vegetables')

# Reconcile User
u_chef, created = User.objects.get_or_create(username='chef_john', defaults={'is_staff': True})
if created:
    print(f"Created user 'chef_john' in Docker")
else:
    print(f"User 'chef_john' already exists in Docker")

# Reconcile Profile
prof_chef, _ = Profile.objects.get_or_create(user=u_chef)
prof_chef.departments.add(d_kitchen)
prof_chef.save()

# Reconcile Item
item_tomato, created = Item.objects.get_or_create(
    name='Tomato', 
    defaults={
        'category': c_veg, 
        'unit': 'Kg'
    }
)
if created:
    print(f"Created item 'Tomato' in Docker")

# Reconcile Material Request
mr, created = MaterialRequest.objects.get_or_create(
    mr_number='MR-20231027-0001', 
    defaults={
        'order_date': timezone.now().date(), 
        'target_date': timezone.now().date(), 
        'meal_type': 'LUNCH', 
        'status': 'PLACED', 
        'requested_by': u_chef, 
        'department': d_kitchen
    }
)

if created:
    MaterialRequestItem.objects.get_or_create(
        material_request=mr, 
        item=item_tomato, 
        defaults={
            'quantity': Decimal('10.5'), 
            'unit': 'Kg'
        }
    )
    print(f"Successfully reconciled MR-20231027-0001 in Docker DB")
else:
    # Ensure it's assigned to kitchen if it already exists
    mr.department = d_kitchen
    mr.save()
    print(f"MR-20231027-0001 was already present, ensured department is assigned")
