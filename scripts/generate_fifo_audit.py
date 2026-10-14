import os
import django
from decimal import Decimal
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

# Initialize Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
try:
    django.setup()
except Exception:
    django.setup()

from core.models import Item, PurchaseRequestItem, MaterialRequestItem

def generate_fifo_report():
    items = Item.objects.all().order_by('name')
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "True FIFO Balance Audit"
    
    # Styles
    item_header_font = Font(bold=True, color="FFFFFF", size=12)
    item_header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
    
    col_header_font = Font(bold=True)
    col_header_fill = PatternFill(start_color="DCE6F1", end_color="DCE6F1", fill_type="solid")
    
    summary_font = Font(bold=True, color="FF0000")

    current_row = 1
    
    grand_total_inventory_value = Decimal('0.00')

    for item in items:
        # 1. Build Incoming Queue (Purchases)
        purchases = PurchaseRequestItem.objects.filter(
            item=item,
            quantity_received__gt=0
        ).exclude(
            purchase_request__status__in=['DRAFT', 'CANCELLED']
        ).order_by('purchase_request__order_date', 'id')
        
        batch_queue = []
        for p in purchases:
            base_price = p.unit_price
            tax_rate = p.tax_rate or Decimal('0.00')
            tax_amt = base_price * (tax_rate / Decimal('100'))
            final_price = (base_price + tax_amt).quantize(Decimal('0.00'))
            
            batch_queue.append({
                'pr_ref': p.purchase_request.pr_reference,
                'date': p.purchase_request.order_date.strftime('%Y-%m-%d') if p.purchase_request.order_date else "N/A",
                'initial_qty': p.quantity_received,
                'remaining_qty': p.quantity_received,
                'unit_price': final_price # Valuation includes tax
            })
            
        # 2. Simulate Outgoing (MR Consumptions)
        mrs = MaterialRequestItem.objects.filter(
            item=item,
            material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED']
        ).order_by('material_request__target_date', 'id')
        
        for mr in mrs:
            issued = mr.quantity_issued or Decimal('0.00')
            returned = mr.quantity_returned or Decimal('0.00')
            to_deduct = issued - returned
            
            if to_deduct <= 0:
                continue
                
            # FIFO Deduction
            for batch in batch_queue:
                if to_deduct <= 0:
                    break
                if batch['remaining_qty'] > 0:
                    take = min(to_deduct, batch['remaining_qty'])
                    batch['remaining_qty'] -= take
                    to_deduct -= take

        # 3. Filter remaining batches
        remaining_batches = [b for b in batch_queue if b['remaining_qty'] > 0]
        
        if not remaining_batches:
            continue # Skip items with zero true balance

        # --- ITEM HEADER ---
        ws.cell(row=current_row, column=1, value=f"ITEM: {item.name}").font = item_header_font
        for col in range(1, 6):
            ws.cell(row=current_row, column=col).fill = item_header_fill
        ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=5)
        current_row += 1
        
        # --- COLUMNS ---
        headers = ["Remaining PR Batch (Ref)", "Purchase Date", "True Remaining Qty", "Final Unit Price (w/ Tax)", "Total Batch Value"]
        for i, header in enumerate(headers, 1):
            cell = ws.cell(row=current_row, column=i, value=header)
            cell.font = col_header_font
            cell.fill = col_header_fill
        current_row += 1
        
        # --- ROWS ---
        total_qty = Decimal('0.00')
        total_val = Decimal('0.00')
        
        for b in remaining_batches:
            val = (b['remaining_qty'] * b['unit_price']).quantize(Decimal('0.00'))
            
            ws.cell(row=current_row, column=1, value=b['pr_ref'])
            ws.cell(row=current_row, column=2, value=b['date'])
            ws.cell(row=current_row, column=3, value=float(b['remaining_qty']))
            ws.cell(row=current_row, column=4, value=float(b['unit_price']))
            ws.cell(row=current_row, column=5, value=float(val))
            
            total_qty += b['remaining_qty']
            total_val += val
            current_row += 1
            
        grand_total_inventory_value += total_val
        
        # --- SUMMARY ---
        ws.cell(row=current_row, column=2, value="TOTAL TRUE IN HAND:").font = summary_font
        ws.cell(row=current_row, column=3, value=float(total_qty)).font = summary_font
        ws.cell(row=current_row, column=5, value=float(total_val)).font = summary_font
        
        current_row += 2 # spacing

    # --- GRAND TOTAL ---
    grand_total_font = Font(bold=True, color="FFFFFF", size=14)
    grand_total_fill = PatternFill(start_color="00B050", end_color="00B050", fill_type="solid")
    
    ws.cell(row=current_row, column=1, value="GRAND TOTAL TRUE INVENTORY VALUATION:").font = grand_total_font
    for col in range(1, 6):
        ws.cell(row=current_row, column=col).fill = grand_total_fill
    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=4)
    
    ws.cell(row=current_row, column=5, value=float(grand_total_inventory_value)).font = grand_total_font

    current_row += 2

    # Auto-adjust column widths
    widths = [30, 15, 20, 25, 20]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    output_path = 'd:/FoodDepartment/true_fifo_balance_audit.xlsx'
    wb.save(output_path)
    print(f"Report generated successfully at {output_path}")

if __name__ == '__main__':
    generate_fifo_report()
