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

from core.models import Item, PurchaseRequestItem, MaterialRequestItem, Inventory, Department

def generate_inventory_tally():
    items = Item.objects.all().order_by('name')
    
    try:
        store_dept = Department.objects.get(code='procurement')
    except Department.DoesNotExist:
        print("Error: Procurement department not found.")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Master Inventory Tally"
    
    # Styles
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="000000", end_color="000000", fill_type="solid")
    
    error_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    error_font = Font(color="9C0006")
    
    success_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    success_font = Font(color="006100")

    # --- HEADERS ---
    headers = [
        "Item Name", 
        "Total Purchased Qty (+)", 
        "Total Consumed Qty (-)", 
        "Expected Store Stock (=)", 
        "Actual Store Inventory", 
        "Discrepancy (Missing/Ghost Stock)",
        "Audit Status"
    ]
    
    for i, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=i, value=header)
        cell.font = header_font
        cell.fill = header_fill
        
    current_row = 2
    
    for item in items:
        # 1. Total Purchased
        purchases = PurchaseRequestItem.objects.filter(
            item=item,
            quantity_received__gt=0,
            purchase_request__order_date__lte='2026-06-30'
        ).exclude(purchase_request__status__in=['DRAFT', 'CANCELLED'])
        
        total_purchased = sum(p.quantity_received for p in purchases) or Decimal('0.00')
        
        # 2. Total Consumed (Issued - Returned)
        mrs = MaterialRequestItem.objects.filter(
            item=item,
            material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED'],
            material_request__target_date__lte='2026-06-30'
        )
        
        total_consumed = Decimal('0.00')
        for mr in mrs:
            issued = mr.quantity_issued or Decimal('0.00')
            returned = mr.quantity_returned or Decimal('0.00')
            total_consumed += (issued - returned)
            
        # 3. Expected vs Actual
        expected_stock = total_purchased - total_consumed
        
        actual_inventory = Inventory.objects.filter(item=item, department=store_dept).first()
        actual_stock = actual_inventory.current_quantity if actual_inventory else Decimal('0.00')
        
        discrepancy = expected_stock - actual_stock
        
        # --- WRITE ROW ---
        ws.cell(row=current_row, column=1, value=item.name)
        ws.cell(row=current_row, column=2, value=float(total_purchased))
        ws.cell(row=current_row, column=3, value=float(total_consumed))
        ws.cell(row=current_row, column=4, value=float(expected_stock)).font = Font(bold=True)
        ws.cell(row=current_row, column=5, value=float(actual_stock)).font = Font(bold=True)
        
        disc_cell = ws.cell(row=current_row, column=6, value=float(discrepancy))
        status_cell = ws.cell(row=current_row, column=7)
        
        if discrepancy != Decimal('0.00'):
            disc_cell.fill = error_fill
            disc_cell.font = error_font
            status_cell.value = "MISMATCH - AUDIT REQUIRED"
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
    widths = [25, 20, 20, 25, 25, 30, 30]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    output_path = 'd:/FoodDepartment/master_inventory_tally.xlsx'
    wb.save(output_path)
    print(f"Report generated successfully at {output_path}")

if __name__ == '__main__':
    generate_inventory_tally()
