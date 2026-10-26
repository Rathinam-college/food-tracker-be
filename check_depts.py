import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Department

depts = Department.objects.all()
print(f"Total Departments: {len(depts)}")
for d in depts:
    print(f"ID: {d.id}, Name: {d.name}, Code: {d.code}, Display: {d.display_name}")
