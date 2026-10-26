import os
import sys
import django

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from django.contrib.auth.models import User
from django.db import transaction
from core.models import Department, Profile

def setup_initial_data():
    print("--- Starting Initial System Setup (V2) ---")
    
    # 1. Create Superuser if it doesn't exist
    admin_user = 'admin'
    admin_pass = 'admin123'
    admin_email = 'admin@rathinamfood.com'

    if not User.objects.filter(username=admin_user).exists():
        print(f"Creating default superuser: {admin_user}...")
        admin = User.objects.create_superuser(
            username=admin_user,
            email=admin_email,
            password=admin_pass
        )
        print(f"Superuser '{admin_user}' created successfully.")
    else:
        print(f"Superuser '{admin_user}' already exists. Skipping creation.")
        admin = User.objects.get(username=admin_user)

    # 2. Seed Departments
    depts_to_seed = [
        {
            'name': 'Procurement Department',
            'code': 'procurement',
            'description': 'Manage purchase requests, vendors, and material registry.',
            'logo': '📦',
            'display_name': 'Procurement'
        },
        {
            'name': 'Kitchen Department',
            'code': 'kitchen',
            'description': 'Manage daily consumption, yields, and kitchen inventory.',
            'logo': '🍳',
            'display_name': 'Kitchen'
        }
    ]

    for dept_data in depts_to_seed:
        dept, created = Department.objects.update_or_create(
            code=dept_data['code'],
            defaults=dept_data
        )
        if created:
            print(f"Created department: {dept.name}")
        else:
            print(f"Updated department: {dept.name}")

    # 3. Ensure admin has a Profile and is assigned to both departments
    profile, created = Profile.objects.get_or_create(user=admin)
    all_depts = Department.objects.all()
    profile.departments.set(all_depts)
    profile.save()
    print(f"Admin user '{admin_user}' assigned to all available departments.")

    print("--- Initial System Setup Completed ---")

if __name__ == "__main__":
    setup_initial_data()
