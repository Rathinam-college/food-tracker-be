import pandas as pd
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Inventory, Item, Department
store = Department.objects.get(code='procurement')

df_download = pd.read_excel('C:/Users/janar/Downloads/RathinamCampus/June Closing_Stock.xlsx')
download_dict = dict(zip(df_download['Item Name'], df_download['Current Stock']))

mismatches = 0
for item_name, download_val in download_dict.items():
    try:
        item = Item.objects.get(name=item_name)
        inv = Inventory.objects.get(item=item, department=store)
        actual = float(inv.current_quantity)
    except Exception:
        actual = 0.0

    if abs(actual - download_val) > 0.01:
        mismatches += 1

print(f'Mismatches with current Inventory table: {mismatches}')
