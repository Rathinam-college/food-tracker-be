import django
import os
import pandas as pd
from decimal import Decimal
from django.db.models import Sum

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Item, Procurement, MaterialRequestItem

def generate_document_based_audit():
    print("Generating Document-Based Audit Report (Procurement vs MaterialRequest)...")
    data = []
    
    items = Item.objects.all().order_by('name')
    start_date = '2026-04-30'
    
    for item in items:
        # Get all procurements for this item from 30-04-2026
        procs = Procurement.objects.filter(item=item, purchase_date__gte=start_date)
        
        purchased_qty = procs.aggregate(s=Sum('quantity'))['s'] or Decimal('0.000')
        total_price = procs.aggregate(s=Sum('total_price'))['s'] or Decimal('0.00')
        
        # Calculate tax sum
        tax_sum = Decimal('0.00')
        for p in procs:
            base_cost = (p.quantity or Decimal('0')) * (p.unit_price or Decimal('0'))
            tax = (p.total_price or Decimal('0')) - base_cost
            tax_sum += max(Decimal('0'), tax)
            
        # Get material request items
        mris = MaterialRequestItem.objects.filter(item=item, material_request__order_date__gte=start_date)
        
        issued_qty = mris.aggregate(s=Sum('quantity_issued'))['s'] or Decimal('0.000')
        returned_qty = mris.aggregate(s=Sum('quantity_returned'))['s'] or Decimal('0.000')
        
        # Calculate balance
        left_in_stock = purchased_qty - issued_qty + returned_qty
        
        # Calculate average value
        if purchased_qty > 0:
            avg_price = total_price / purchased_qty
        else:
            avg_price = Decimal('0.00')
            
        left_value = left_in_stock * avg_price
        
        if purchased_qty > 0 or issued_qty > 0 or returned_qty > 0:
            data.append({
                'Item Name': item.name,
                'Total Purchased Qty': float(purchased_qty),
                'Total Purchase Value (Incl. Tax)': float(total_price),
                'Total Tax Paid': float(tax_sum),
                'Average Unit Cost': float(avg_price),
                'Total Issued to Kitchen': float(issued_qty),
                'Total Kitchen Returns': float(returned_qty),
                'Left in Stock Balance (Procurement)': float(left_in_stock),
                'Remaining Stock Value': float(left_value)
            })
            
    df = pd.DataFrame(data)
    output_path = r'D:\FoodDepartment\Document_Based_Audit_Report.xlsx'
    df.to_excel(output_path, index=False)
    print(f"Report saved to {output_path}")

if __name__ == '__main__':
    generate_document_based_audit()
