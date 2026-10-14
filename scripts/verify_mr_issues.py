import os
import csv
import django
from decimal import Decimal

# Initialize Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
try:
    django.setup()
except Exception:
    django.setup()

from core.models import MaterialRequest, StockMovement
from django.db.models import Sum

def verify_mr_issues():
    # We only want to check MRs that have reached the ISSUED phase or beyond
    mrs = MaterialRequest.objects.filter(status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED']).order_by('-id')
    
    mismatch_count = 0
    csv_file_path = 'mr_verification_report.csv'
    
    with open(csv_file_path, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['MR Number', 'Item', 'Qty on MR Item', 'Qty in Ledger', 'Status'])

        for mr in mrs:
            for mr_item in mr.items.all():
                item_name = mr_item.item.name
                
                # The quantity the MR says was issued
                mr_qty = mr_item.quantity_issued or Decimal('0.000')
                
                # The sum of quantities pushed in the ledger for this specific MR & Item
                ledger_qty = StockMovement.objects.filter(
                    reference_id=mr.mr_number,
                    item=mr_item.item,
                    type='STORE_TO_KITCHEN'
                ).aggregate(total=Sum('quantity'))['total'] or Decimal('0.000')

                # Check if they match
                if mr_qty != ledger_qty:
                    status = "MISMATCH (Double Entry)"
                    mismatch_count += 1
                else:
                    status = "MATCH"
                
                writer.writerow([mr.mr_number, item_name, mr_qty, ledger_qty, status])

    print(f"Report generated successfully: {csv_file_path}")
    print(f"Found {mismatch_count} mismatched records.")

if __name__ == '__main__':
    verify_mr_issues()
