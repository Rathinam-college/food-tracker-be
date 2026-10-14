import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from django.apps import apps
from core.models import PurchaseRequestItem

print("Fields:", [f.name for f in PurchaseRequestItem._meta.fields])
