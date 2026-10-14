import django
import os
import pandas as pd
from decimal import Decimal
from django.db.models import Sum

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Inventory, StockBatch, Item

def generate_batch_wise_report():
    print("Generating batch-wise vs global inventory report...")
    data = []
    
    # Get all procurement inventories
    inventories = Inventory.objects.filter(department__code='procurement').select_related('item')
    
    for inv in inventories:
        item = inv.item
        global_qty = inv.current_quantity
        
        # Get all batches for this item that have remaining quantity > 0
        batches = StockBatch.objects.filter(item=item, department__code='procurement', remaining_quantity__gt=0).order_by('batch_date')
        
        batch_sum = batches.aggregate(s=Sum('remaining_quantity'))['s'] or Decimal('0.000')
        
        # If there are active batches, list them out
        if batches.exists():
            for i, b in enumerate(batches):
                data.append({
                    'Item Name': item.name,
                    'Global Inventory Balance': float(global_qty) if i == 0 else '',
                    'Batch ID': b.id,
                    'Batch Date': b.batch_date.strftime('%Y-%m-%d'),
                    'Unit Price': float(b.unit_price),
                    'Batch Remaining Qty': float(b.remaining_quantity),
                    'Total Batch Sum': float(batch_sum) if i == 0 else '',
                    'Match?': 'Yes' if round(global_qty, 3) == round(batch_sum, 3) else 'No'
                })
        else:
            # If no active batches, just show the global vs 0
            data.append({
                'Item Name': item.name,
                'Global Inventory Balance': float(global_qty),
                'Batch ID': 'N/A',
                'Batch Date': 'N/A',
                'Unit Price': 0.0,
                'Batch Remaining Qty': 0.0,
                'Total Batch Sum': float(batch_sum),
                'Match?': 'Yes' if round(global_qty, 3) == round(batch_sum, 3) else 'No'
            })
            
    df = pd.DataFrame(data)
    output_path = r'D:\FoodDepartment\Batch_vs_Inventory_Sync_Report.xlsx'
    df.to_excel(output_path, index=False)
    print(f"Report saved to {output_path}")

if __name__ == '__main__':
    generate_batch_wise_report()
