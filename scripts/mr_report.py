import csv
import sys
import django
import os

# Initialize Django environment if run independently
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core.models import MaterialRequest, MaterialRequestItem

def generate_mr_report():
    print("MR Number | Status | Item Name | Requested Qty | Issued Qty | Returned Qty | Consumed Net Qty")
    print("-" * 110)
    
    # Get all MRs in descending order
    mrs = MaterialRequest.objects.all().order_by('-id')
    
    for mr in mrs:
        for item in mr.items.all():
            req_qty = item.quantity
            issued_qty = item.quantity_issued
            returned_qty = item.quantity_returned
            consumed = issued_qty - returned_qty
            
            print(f"{mr.mr_number:<9} | {mr.status:<15} | {item.item.name:<25} | {req_qty:<13} | {issued_qty:<10} | {returned_qty:<12} | {consumed}")

if __name__ == '__main__':
    generate_mr_report()
