import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from django.test import RequestFactory
from rest_framework.test import force_authenticate
from core.views import InventoryViewSet
from django.contrib.auth import get_user_model
from core.models import Item, StockMovement, StockBatch, Department
from django.db.models import Sum
from datetime import datetime, timedelta
import pandas as pd

def generate_full_historical_audit():
    User = get_user_model()
    user = User.objects.first()
    factory = RequestFactory()
    view = InventoryViewSet.as_view({'get': 'stock_balance_report'})
    proc_dept = Department.objects.filter(code='procurement').first()
    
    items = list(Item.objects.all())
    
    # Get earliest movement date
    earliest = StockMovement.objects.order_by('movement_date').first()
    if not earliest:
        print("No movements found in the database.")
        return
        
    start_date = datetime(2026, 4, 30).date()
    end_date = datetime.now().date()
    
    # Generate list of days
    days = [(start_date + timedelta(days=i)) for i in range((end_date - start_date).days + 1)]
    
    audit_data = []
    
    def get_api_balance(date_obj):
        request = factory.get(f'/stock_balance_report/?as_of_date={date_obj.strftime("%Y-%m-%d")}')
        force_authenticate(request, user=user)
        response = view(request)
        api_balances = {}
        if hasattr(response, 'data'):
            for data_item in response.data:
                api_balances[data_item['item_name'].strip().lower()] = float(data_item['current_balance'])
        return api_balances
    
    print(f"Running FULL historical audit from {start_date} to {end_date} for {len(items)} items...")
    
    # Cache the daily API responses
    print("Caching daily API responses... (This may take a moment)")
    api_cache = {}
    for d in days:
        api_cache[d] = get_api_balance(d)
        
    print("Processing item ledgers...")
    
    for item in items:
        # Base numbers before start_date (should be 0 since start_date is the earliest, but included for safety)
        batch_base = StockBatch.objects.filter(item=item, department=proc_dept, batch_date__lt=start_date).aggregate(s=Sum('initial_quantity'))['s'] or 0
        issued_base = StockMovement.objects.filter(item=item, type='STORE_TO_KITCHEN', movement_date__date__lt=start_date).aggregate(s=Sum('quantity'))['s'] or 0
        wastage_base = StockMovement.objects.filter(item=item, type='WASTAGE', movement_date__date__lt=start_date).aggregate(s=Sum('quantity'))['s'] or 0
        vr_base = StockMovement.objects.filter(item=item, type__in=['VENDOR_RETURN', 'OUT'], movement_date__date__lt=start_date).aggregate(s=Sum('quantity'))['s'] or 0
        kr_base = StockMovement.objects.filter(item=item, type='KITCHEN_TO_STORE', movement_date__date__lt=start_date).aggregate(s=Sum('quantity'))['s'] or 0
        
        running_balance = float(batch_base - issued_base - wastage_base - vr_base + kr_base)
        
        for d in days:
            # Daily activity
            d_batches = StockBatch.objects.filter(item=item, department=proc_dept, batch_date=d).aggregate(s=Sum('initial_quantity'))['s'] or 0
            # Some INWARDs might not be in StockBatch, checking StockMovement as fallback
            d_inwards_mov = StockMovement.objects.filter(item=item, type__in=['IN', 'STORE_INWARD'], movement_date__date=d).aggregate(s=Sum('quantity'))['s'] or 0
            
            purchased_val = float(d_batches)
            
            d_issued = StockMovement.objects.filter(item=item, type='STORE_TO_KITCHEN', movement_date__date=d).aggregate(s=Sum('quantity'))['s'] or 0
            d_wastage = StockMovement.objects.filter(item=item, type='WASTAGE', movement_date__date=d).aggregate(s=Sum('quantity'))['s'] or 0
            d_kr = StockMovement.objects.filter(item=item, type='KITCHEN_TO_STORE', movement_date__date=d).aggregate(s=Sum('quantity'))['s'] or 0
            d_vr = StockMovement.objects.filter(item=item, type__in=['VENDOR_RETURN', 'OUT'], movement_date__date=d).aggregate(s=Sum('quantity'))['s'] or 0
            
            opening = running_balance
            running_balance += purchased_val + float(d_kr) - float(d_issued) - float(d_wastage) - float(d_vr)
            
            # API Match
            api_bal = api_cache[d].get(item.name.lower(), 0.0)
            
            audit_data.append({
                'Date': d.strftime("%Y-%m-%d"),
                'Item': item.name,
                'Opening Balance': opening,
                'Purchased (Batches)': purchased_val,
                'Kitchen Returns': float(d_kr),
                'Issued': float(d_issued),
                'Wastage': float(d_wastage),
                'Vendor Returns': float(d_vr),
                'Calculated Closing': running_balance,
                'API Report Balance': api_bal,
                'Match?': 'Yes' if round(running_balance, 2) == round(api_bal, 2) else 'NO'
            })
            
    output_path = r'D:\FoodDepartment\Full_Historical_Audit_Complete.xlsx'
    df = pd.DataFrame(audit_data)
    df.to_excel(output_path, index=False)
    print(f'Complete audit saved to {output_path}')

if __name__ == '__main__':
    generate_full_historical_audit()
