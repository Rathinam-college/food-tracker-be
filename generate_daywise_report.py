import django
import os
import pandas as pd
from decimal import Decimal
from collections import defaultdict

# Setup Django Environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import StockMovement, StockBatch, Inventory, Item
from django.db.models import Sum

def generate_audit_report():
    output_file = r'D:\FoodDepartment\DayWise_Stock_Audit.xlsx'
    
    items = Item.objects.all()
    
    day_rows = []
    summary_rows = []
    
    print("Gathering data for all items...")
    
    for item in items:
        movements = StockMovement.objects.filter(item=item).order_by('movement_date')
        if not movements.exists():
            continue
            
        day_movements = defaultdict(lambda: {
            'inwards': Decimal('0'),
            'issued': Decimal('0'),
            'k_returns': Decimal('0'),
            'v_returns': Decimal('0'),
            'wastage': Decimal('0')
        })
        
        # 1. Aggregate movements by day
        for m in movements:
            date_str = m.movement_date.date().isoformat()
            if m.type in ['IN', 'STORE_INWARD']:
                day_movements[date_str]['inwards'] += m.quantity
            elif m.type == 'STORE_TO_KITCHEN':
                day_movements[date_str]['issued'] += m.quantity
            elif m.type == 'KITCHEN_TO_STORE':
                day_movements[date_str]['k_returns'] += m.quantity
            elif m.type == 'VENDOR_RETURN':
                day_movements[date_str]['v_returns'] += m.quantity
            elif m.type in ['WASTAGE', 'KITCHEN_WASTAGE', 'BREAKAGE', 'EXPIRED']:
                day_movements[date_str]['wastage'] += m.quantity
                
        dates = sorted(list(day_movements.keys()))
        
        proc_opening = Decimal('0')
        kit_opening = Decimal('0')
        
        # 2. Calculate day-wise ledgers
        for d in dates:
            movs = day_movements[d]
            
            proc_closing = proc_opening + movs['inwards'] - movs['issued'] + movs['k_returns'] - movs['v_returns'] - movs['wastage']
            kit_closing = kit_opening + movs['issued'] - movs['k_returns']
            
            day_rows.append({
                'Date': d,
                'Item Name': item.name,
                'Procurement Opening Stock': float(proc_opening),
                'Purchased (Inwards)': float(movs['inwards']),
                'Issued (MR)': float(movs['issued']),
                'Kitchen Returns': float(movs['k_returns']),
                'Vendor Returns': float(movs['v_returns']),
                'Wastage': float(movs['wastage']),
                'Procurement Closing Stock': float(proc_closing),
                'Kitchen Opening Stock': float(kit_opening),
                'Kitchen Closing Stock': float(kit_closing)
            })
            
            proc_opening = proc_closing
            kit_opening = kit_closing
    
        # 3. Compile Current Summary Check for this item
        inv_proc = Inventory.objects.filter(item=item, department__code='procurement').first()
        actual_proc = inv_proc.current_quantity if inv_proc else Decimal('0')
        actual_val = inv_proc.inventory_value if inv_proc else Decimal('0')
        
        inv_kit = Inventory.objects.filter(item=item, department__code='kitchen').first()
        actual_kit = inv_kit.current_quantity if inv_kit else Decimal('0')
        
        batches = StockBatch.objects.filter(item=item, remaining_quantity__gt=0)
        batch_sum = batches.aggregate(s=Sum('remaining_quantity'))['s'] or Decimal('0')
        
        is_synced = float(proc_opening) == float(actual_proc) == float(batch_sum)
        
        summary_rows.append({
            'Item Name': item.name,
            'Expected Proc Stock (Math)': float(proc_opening),
            'Actual Proc Stock (Inventory Table)': float(actual_proc),
            'Sum of Active Batches': float(batch_sum),
            'Expected Kitchen Stock (Math)': float(kit_opening),
            'Actual Kitchen Stock (Inventory Table)': float(actual_kit),
            'Total Inventory Value (₹)': float(actual_val),
            'Synced Perfectly?': 'YES' if is_synced else 'NO'
        })
    
    print("Writing to Excel file...")
    # Write multi-sheet Excel
    with pd.ExcelWriter(output_file) as writer:
        pd.DataFrame(day_rows).to_excel(writer, sheet_name='Day-by-Day Ledgers', index=False)
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name='Global Sync Check', index=False)
    
    print(f"\nSuccess! The report has been saved to: {output_file}")

if __name__ == '__main__':
    generate_audit_report()
