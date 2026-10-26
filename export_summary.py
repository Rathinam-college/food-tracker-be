import os
import sys
import django
import json

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
django.setup()

from django.test import RequestFactory
from core.views import ProcurementViewSet
from rest_framework.request import Request
from django.contrib.auth import get_user_model

User = get_user_model()
admin_user = User.objects.filter(is_superuser=True).first()

factory = RequestFactory()
request = factory.get('/api/procurement/custom_monthly_summary/?month=6&year=2026&months_count=3')
request.user = admin_user

view = ProcurementViewSet.as_view({'get': 'custom_monthly_summary'})
response = view(request)

if response.status_code == 200:
    data = response.data
    # Save it in the root folder as requested
    output_path = r"d:\FoodDepartment\monthly_expenditure_summary.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
    print(f"Data successfully saved to {output_path}")
else:
    print(f"Failed to fetch data: {response.status_code}")
    print(response.data)
