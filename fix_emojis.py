import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Department
Department.objects.filter(name__icontains='Management').update(logo='💼')
Department.objects.filter(name__icontains='Procurement').update(logo='📦')
Department.objects.filter(name__icontains='Kitchen').update(logo='🍳')
print("Successfully updated department logos!")
