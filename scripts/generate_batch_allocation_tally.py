import os
import django
from decimal import Decimal
import openpyxl
from openpyxl.styles import Font, PatternFill

# Initialize Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
try:
    django.setup()
except Exception:
    django.setup()

from core.models import MaterialRequestItem, MRItemBatchAllocation
from django.db.models import Sum

def generate_batch_allocation_tally():
    mr_items = MaterialRequestItem.objects.select_related('material_request', 'item').filter(
        material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED'],
        quantity_issued__gt=0
    ).order_by('material_request__target_date', 'id')

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Batch Allocation Tally"
    
    # Styles
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="333333", end_color="333333", fill_type="solid")
    
    error_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    error_font = Font(color="9C0006")
    
    success_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    success_font = Font(color="006100")

    # --- HEADERS ---
    headers = [
        "MR Reference", 
        "Date",
        "Item Name", 
        "Authorized Issue Qty", 
        "Total Batch Qty Allocated", 
        "Discrepancy (Missing/Duplicate)",
        "Audit Status"
    ]
    
    for i, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=i, value=header)
        cell.font = header_font
        cell.fill = header_fill
        
    current_row = 2
    
    mismatch_count = 0

    for mr_item in mr_items:
        mr = mr_item.material_request
        
        # 1. True Authorized Quantity
        authorized_qty = mr_item.quantity_issued or Decimal('0.00')
        
        # 2. Total Allocated in MRItemBatchAllocation
        allocations = MRItemBatchAllocation.objects.filter(mr_item=mr_item)
        allocated_qty = allocations.aggregate(total=Sum('quantity_allocated'))['total'] or Decimal('0.00')
        
        # 3. Discrepancy
        discrepancy = allocated_qty - authorized_qty
        
        # --- WRITE ROW ---
        ws.cell(row=current_row, column=1, value=mr.mr_number)
        ws.cell(row=current_row, column=2, value=mr.target_date.strftime('%Y-%m-%d') if mr.target_date else "")
        ws.cell(row=current_row, column=3, value=mr_item.item.name)
        ws.cell(row=current_row, column=4, value=float(authorized_qty)).font = Font(bold=True)
        ws.cell(row=current_row, column=5, value=float(allocated_qty)).font = Font(bold=True)
        
        disc_cell = ws.cell(row=current_row, column=6, value=float(discrepancy))
        status_cell = ws.cell(row=current_row, column=7)
        
        if discrepancy != Decimal('0.00'):
            mismatch_count += 1
            disc_cell.fill = error_fill
            disc_cell.font = error_font
            status_cell.value = "MISMATCH - ALLOCATION ERROR"
            status_cell.fill = error_fill
            status_cell.font = error_font
        else:
            disc_cell.fill = success_fill
            disc_cell.font = success_font
            status_cell.value = "PERFECT TALLY"
            status_cell.fill = success_fill
            status_cell.font = success_font
            
        current_row += 1

    # Auto-adjust column widths
    widths = [20, 15, 25, 22, 25, 30, 30]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    output_path = 'd:/FoodDepartment/batch_allocation_tally.xlsx'
    wb.save(output_path)
    print(f"Report generated successfully at {output_path}")
    print(f"Total Mismatches Found: {mismatch_count}")

if __name__ == '__main__':
    generate_batch_allocation_tally()
