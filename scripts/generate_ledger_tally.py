import os
import django
from decimal import Decimal
import openpyxl
from openpyxl.styles import Font, PatternFill
from django.db.models import Sum

# Initialize Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
try:
    django.setup()
except Exception:
    django.setup()

from core.models import PurchaseRequestItem, MaterialRequestItem, StockMovement, Inventory, MRItemBatchAllocation, Item

def generate_ledger_tally():
    wb = openpyxl.Workbook()
    
    # Styles
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="333333", end_color="333333", fill_type="solid")
    error_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    error_font = Font(color="9C0006")
    success_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    success_font = Font(color="006100")

    # =========================================================================
    # SHEET 1: LEDGER INTEGRITY TALLY (TRANSACTIONS)
    # =========================================================================
    ws1 = wb.active
    ws1.title = "Ledger Transaction Tally"
    
    headers1 = [
        "Transaction Type",
        "Reference Number",
        "Item Name",
        "Source Expected Qty",
        "Total Ledger Qty (StockMovement)",
        "Discrepancy (Missing/Duplicate)",
        "Audit Status",
        "Total Ledger Records Count"
    ]
    
    for i, header in enumerate(headers1, 1):
        cell = ws1.cell(row=1, column=i, value=header)
        cell.font = header_font
        cell.fill = header_fill
        
    current_row = 2
    ledger_mismatches = 0
    
    # 1. Audit Purchases (IN)
    pr_items = PurchaseRequestItem.objects.select_related('purchase_request', 'item').all()
    for pr_item in pr_items:
        pr = pr_item.purchase_request
        expected_qty = pr_item.quantity_received
        
        movements = StockMovement.objects.filter(
            item=pr_item.item,
            type='IN',
            reference_id=pr.pr_reference
        )
        
        ledger_qty = movements.aggregate(total=Sum('quantity'))['total'] or Decimal('0.00')
        record_count = movements.count()
        
        discrepancy = ledger_qty - expected_qty
        
        # Write Row
        ws1.cell(row=current_row, column=1, value="PURCHASE (IN)")
        ws1.cell(row=current_row, column=2, value=pr.pr_reference)
        ws1.cell(row=current_row, column=3, value=pr_item.item.name)
        ws1.cell(row=current_row, column=4, value=float(expected_qty))
        ws1.cell(row=current_row, column=5, value=float(ledger_qty)).font = Font(bold=True)
        disc_cell = ws1.cell(row=current_row, column=6, value=float(discrepancy))
        status_cell = ws1.cell(row=current_row, column=7)
        ws1.cell(row=current_row, column=8, value=record_count)
        
        if discrepancy != Decimal('0.00') or record_count > 1:
            ledger_mismatches += 1
            disc_cell.fill = error_fill
            disc_cell.font = error_font
            status_cell.value = "MISMATCH - LEDGER ERROR"
            status_cell.fill = error_fill
            status_cell.font = error_font
        else:
            disc_cell.fill = success_fill
            disc_cell.font = success_font
            status_cell.value = "PERFECT TALLY"
            status_cell.fill = success_fill
            status_cell.font = success_font
            
        current_row += 1

    # 2. Audit Material Requests (OUT)
    mr_items = MaterialRequestItem.objects.select_related('material_request', 'item').filter(
        material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED'],
        quantity_issued__gt=0
    )
    
    for mr_item in mr_items:
        mr = mr_item.material_request
        expected_qty = mr_item.quantity_issued
        
        movements = StockMovement.objects.filter(
            item=mr_item.item,
            type='OUT',
            reference_id=mr.mr_number
        )
        
        ledger_qty = movements.aggregate(total=Sum('quantity'))['total'] or Decimal('0.00')
        record_count = movements.count()
        
        discrepancy = ledger_qty - expected_qty
        
        # Write Row
        ws1.cell(row=current_row, column=1, value="ISSUE (OUT)")
        ws1.cell(row=current_row, column=2, value=mr.mr_number)
        ws1.cell(row=current_row, column=3, value=mr_item.item.name)
        ws1.cell(row=current_row, column=4, value=float(expected_qty))
        ws1.cell(row=current_row, column=5, value=float(ledger_qty)).font = Font(bold=True)
        disc_cell = ws1.cell(row=current_row, column=6, value=float(discrepancy))
        status_cell = ws1.cell(row=current_row, column=7)
        ws1.cell(row=current_row, column=8, value=record_count)
        
        if discrepancy != Decimal('0.00') or record_count > 1:
            ledger_mismatches += 1
            disc_cell.fill = error_fill
            disc_cell.font = error_font
            status_cell.value = "MISMATCH - LEDGER ERROR"
            status_cell.fill = error_fill
            status_cell.font = error_font
        else:
            disc_cell.fill = success_fill
            disc_cell.font = success_font
            status_cell.value = "PERFECT TALLY"
            status_cell.fill = success_fill
            status_cell.font = success_font
            
        current_row += 1

    # Auto-adjust column widths for sheet 1
    widths1 = [20, 20, 30, 22, 35, 30, 25, 25]
    for i, w in enumerate(widths1, 1):
        ws1.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    # =========================================================================
    # SHEET 2: LEDGER VS BATCH ALLOCATION (ITEM-WISE)
    # =========================================================================
    ws2 = wb.create_sheet(title="Ledger vs Batch Allocation")
    
    headers2 = [
        "Item Name",
        "Total Ledger OUT Qty",
        "Total Batch Allocated Qty",
        "Discrepancy",
        "Status"
    ]
    
    for i, header in enumerate(headers2, 1):
        cell = ws2.cell(row=1, column=i, value=header)
        cell.font = header_font
        cell.fill = header_fill
        
    current_row = 2
    item_mismatches = 0
    
    items = Item.objects.all().order_by('name')
    for item in items:
        # Total Ledger OUT
        ledger_out = StockMovement.objects.filter(
            item=item, 
            type='OUT', 
            reference_id__startswith='MR-'
        ).aggregate(total=Sum('quantity'))['total'] or Decimal('0.00')
        
        # Total Batch Allocated
        batch_allocated = MRItemBatchAllocation.objects.filter(
            mr_item__item=item
        ).aggregate(total=Sum('quantity_allocated'))['total'] or Decimal('0.00')
        
        discrepancy = ledger_out - batch_allocated
        
        # Only show items that have either ledger out or batch allocation
        if ledger_out == Decimal('0.00') and batch_allocated == Decimal('0.00'):
            continue
            
        ws2.cell(row=current_row, column=1, value=item.name)
        ws2.cell(row=current_row, column=2, value=float(ledger_out))
        ws2.cell(row=current_row, column=3, value=float(batch_allocated))
        
        disc_cell = ws2.cell(row=current_row, column=4, value=float(discrepancy))
        status_cell = ws2.cell(row=current_row, column=5)
        
        if discrepancy != Decimal('0.00'):
            item_mismatches += 1
            disc_cell.fill = error_fill
            disc_cell.font = error_font
            status_cell.value = "SYNC ERROR"
            status_cell.fill = error_fill
            status_cell.font = error_font
        else:
            disc_cell.fill = success_fill
            disc_cell.font = success_font
            status_cell.value = "SYNCED"
            status_cell.fill = success_fill
            status_cell.font = success_font
            
        current_row += 1

    # Auto-adjust column widths for sheet 2
    widths2 = [30, 25, 25, 20, 20]
    for i, w in enumerate(widths2, 1):
        ws2.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    output_path = 'd:/FoodDepartment/ledger_integrity_tally_v2.xlsx'
    wb.save(output_path)
    print(f"Report generated successfully at {output_path}")
    print(f"Ledger Transaction Mismatches Found: {ledger_mismatches}")
    print(f"Item-wise Batch Sync Errors Found: {item_mismatches}")

if __name__ == '__main__':
    generate_ledger_tally()
