import os
import django
from decimal import Decimal
import pandas as pd
from datetime import datetime, date, timedelta
from collections import defaultdict

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
django.setup()

from core.models import Item, PurchaseRequestItem, MaterialRequestItem, StockBatch

def generate_daily_report():
    print("Fetching data...")
    items = Item.objects.all()
    
    # We will track events per day per item.
    # Structure: events[date][item_name] = {'purchased': 0, 'consumed': 0, 'adjusted': 0}
    events = defaultdict(lambda: defaultdict(lambda: {'purchased': Decimal('0'), 'consumed': Decimal('0'), 'adjusted': Decimal('0')}))
    
    # 1. Purchases
    purchases = PurchaseRequestItem.objects.filter(
        quantity_received__gt=0
    ).exclude(purchase_request__status__in=['DRAFT', 'CANCELLED'])
    
    for p in purchases:
        if p.purchase_request.order_date:
            d = p.purchase_request.order_date
            events[d][p.item.name]['purchased'] += p.quantity_received
            
    # 2. Material Requests (Consumed)
    mrs = MaterialRequestItem.objects.filter(
        material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED']
    )
    for mr in mrs:
        if mr.material_request.target_date:
            d = mr.material_request.target_date
            issued = mr.quantity_issued or Decimal('0')
            returned = mr.quantity_returned or Decimal('0')
            events[d][mr.item.name]['consumed'] += (issued - returned)
            
    # 3. Manual Adjustments
    batches = StockBatch.objects.filter(procurement__isnull=True)
    for b in batches:
        d = b.created_at.date()
        events[d][b.item.name]['adjusted'] += b.initial_quantity
        
    print("Calculating daily balances...")
    all_dates = sorted(list(events.keys()))
    if not all_dates:
        print("No transactions found.")
        return
        
    start_date = all_dates[0]
    end_date = max(all_dates[-1], date.today())
    
    # Generate continuous date range
    date_list = [start_date + timedelta(days=x) for x in range((end_date - start_date).days + 1)]
    
    # Running balances
    balances = {item.name: Decimal('0') for item in items}
    
    rows = []
    
    for current_date in date_list:
        for item in items:
            item_name = item.name
            
            day_events = events[current_date][item_name]
            purchased = day_events['purchased']
            consumed = day_events['consumed']
            adjusted = day_events['adjusted']
            
            # Only add a row if there was a transaction OR if we want to show it anyway
            # Let's show it if there was a transaction to keep the excel clean, 
            # BUT the user asked for stock availability by each day. 
            # Showing every item every day is 200 * 60 = 12000 rows. Let's do it, it's comprehensive.
            
            opening = balances[item_name]
            closing = opening + purchased + adjusted - consumed
            balances[item_name] = closing
            
            if purchased > 0 or consumed > 0 or adjusted > 0 or closing != 0:
                rows.append({
                    'Date': current_date.strftime('%Y-%m-%d'),
                    'Item Name': item_name,
                    'Opening Stock': float(opening),
                    'Purchased (In)': float(purchased),
                    'Manual Adjustment (In)': float(adjusted),
                    'Consumed (Out)': float(consumed),
                    'Closing Stock': float(closing)
                })

    print("Generating Excel file...")
    df = pd.DataFrame(rows)
    
    file_path = "d:/FoodDepartment/daily_stock_availability_report.xlsx"
    df.to_excel(file_path, index=False, sheet_name="Daily Ledger")
    
    print(f"Report generated successfully at {file_path}")

if __name__ == '__main__':
    generate_daily_report()
