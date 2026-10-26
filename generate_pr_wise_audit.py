import django
import os
import pandas as pd
from decimal import Decimal
from django.db.models import Sum

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Item, Procurement, MaterialRequestItem

def generate_pr_wise_audit():
    print("Generating PR-Wise Audit Report...")
    data = []
    
    items = Item.objects.all().order_by('name')
    start_date = '2026-04-30'
    
    for item in items:
        # Get all procurements (purchases) for this item
        procs = list(Procurement.objects.filter(item=item, purchase_date__gte=start_date).order_by('purchase_date', 'created_at'))
        
        if not procs:
            continue
            
        # Get all material request items (issues and returns) for this item
        mris = MaterialRequestItem.objects.filter(item=item, material_request__order_date__gte=start_date)
        
        total_issued = mris.aggregate(s=Sum('quantity_issued'))['s'] or Decimal('0.000')
        total_returned = mris.aggregate(s=Sum('quantity_returned'))['s'] or Decimal('0.000')
        
        net_consumed_remaining = total_issued - total_returned
        
        for p in procs:
            bought_qty = p.quantity or Decimal('0.000')
            unit_price = p.unit_price or Decimal('0.00')
            total_price = p.total_price or Decimal('0.00')
            
            # Tax Calculation
            base_cost = bought_qty * unit_price
            tax_paid = total_price - base_cost
            tax_paid = max(Decimal('0.00'), tax_paid)
            
            # Allocate Net Consumed using FIFO
            if net_consumed_remaining >= bought_qty:
                pushed_from_this_pr = bought_qty
                net_consumed_remaining -= bought_qty
            elif net_consumed_remaining > 0:
                pushed_from_this_pr = net_consumed_remaining
                net_consumed_remaining = Decimal('0.000')
            else:
                pushed_from_this_pr = Decimal('0.000')
                
            pr_number = p.purchase_request.pr_reference if p.purchase_request else 'Direct Purchase'
            
            avg_unit_incl_tax = total_price / bought_qty if bought_qty > 0 else Decimal('0.00')
            
            remaining_in_stock = bought_qty - pushed_from_this_pr
            remaining_value = remaining_in_stock * avg_unit_incl_tax
            
            data.append({
                'Item Name': item.name,
                'PR Number': pr_number,
                'Purchase Date': p.purchase_date.strftime('%Y-%m-%d'),
                'Purchased Qty': float(bought_qty),
                'Unit Price (Base)': float(unit_price),
                'Unit Price (Incl. Tax)': float(avg_unit_incl_tax),
                'Total PR Value (Incl Tax)': float(total_price),
                'Tax Paid': float(tax_paid),
                'Pushed to Kitchen (Net)': float(pushed_from_this_pr),
                'Remaining on Procurement': float(remaining_in_stock),
                'Remaining Value': float(remaining_value)
            })

    df = pd.DataFrame(data)
    output_path = r'D:\FoodDepartment\PR_and_MR_Wise_Audit_Report.xlsx'
    df.to_excel(output_path, index=False)
    print(f"Report saved to {output_path}")

if __name__ == '__main__':
    generate_pr_wise_audit()
