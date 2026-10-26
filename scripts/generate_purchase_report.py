import os
import django
from decimal import Decimal
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from collections import defaultdict

# Initialize Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "food_tracker_backend.settings")
try:
    django.setup()
except Exception:
    django.setup()

from core.models import PurchaseRequestItem

def generate_purchase_report():
    # Fetch all PR items ordered by item name and then PR date
    pr_items = PurchaseRequestItem.objects.select_related(
        'purchase_request', 'item', 'item__category'
    ).exclude(
        purchase_request__status__in=['DRAFT', 'CANCELLED']
    ).filter(
        purchase_request__order_date__lte='2026-06-30'
    ).order_by('item__name', 'purchase_request__order_date')

    # Group by Item
    data_by_item = defaultdict(list)
    for pr_item in pr_items:
        data_by_item[pr_item.item.name].append(pr_item)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Purchase Audit Report"
    
    # Styles
    item_header_font = Font(bold=True, color="FFFFFF", size=12)
    item_header_fill = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
    
    col_header_font = Font(bold=True)
    col_header_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    
    total_font = Font(bold=True, color="FF0000")

    current_row = 1

    for item_name, items in data_by_item.items():
        # --- ITEM HEADER ROW ---
        ws.cell(row=current_row, column=1, value=f"ITEM: {item_name}").font = item_header_font
        for col in range(1, 11):
            ws.cell(row=current_row, column=col).fill = item_header_fill
        ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=10)
        current_row += 1
        
        # --- COLUMN HEADERS ---
        headers = ["PR Reference", "Order Date", "Status", "Ordered Qty", "Received Qty", "Unit Price", "Base Value", "Tax %", "Tax Amt", "Total w/ Tax"]
        for i, header in enumerate(headers, 1):
            cell = ws.cell(row=current_row, column=i, value=header)
            cell.font = col_header_font
            cell.fill = col_header_fill
        current_row += 1
        
        # --- PR ROWS ---
        total_ordered = Decimal('0.00')
        total_received = Decimal('0.00')
        total_base = Decimal('0.00')
        total_tax = Decimal('0.00')
        total_value = Decimal('0.00')
        
        for pr_item in items:
            pr = pr_item.purchase_request
            
            # Use received qty if it's > 0, otherwise ordered qty for value
            qty_for_value = pr_item.quantity_received if pr_item.quantity_received > 0 else pr_item.quantity_ordered
            
            base_value = (qty_for_value * pr_item.unit_price).quantize(Decimal('0.00'))
            tax_rate = pr_item.tax_rate or Decimal('0.00')
            tax_amount = (base_value * (tax_rate / Decimal('100'))).quantize(Decimal('0.00'))
            row_value = base_value + tax_amount
            
            ws.cell(row=current_row, column=1, value=pr.pr_reference)
            ws.cell(row=current_row, column=2, value=pr.order_date.strftime('%Y-%m-%d') if pr.order_date else "")
            ws.cell(row=current_row, column=3, value=pr.status)
            ws.cell(row=current_row, column=4, value=float(pr_item.quantity_ordered))
            ws.cell(row=current_row, column=5, value=float(pr_item.quantity_received))
            ws.cell(row=current_row, column=6, value=float(pr_item.unit_price))
            ws.cell(row=current_row, column=7, value=float(base_value))
            ws.cell(row=current_row, column=8, value=float(tax_rate))
            ws.cell(row=current_row, column=9, value=float(tax_amount))
            ws.cell(row=current_row, column=10, value=float(row_value))
            
            total_ordered += pr_item.quantity_ordered
            total_received += pr_item.quantity_received
            total_base += base_value
            total_tax += tax_amount
            total_value += row_value
            current_row += 1
            
        # --- TOTALS ROW ---
        ws.cell(row=current_row, column=3, value="TOTALS:").font = total_font
        ws.cell(row=current_row, column=4, value=float(total_ordered)).font = total_font
        ws.cell(row=current_row, column=5, value=float(total_received)).font = total_font
        ws.cell(row=current_row, column=7, value=float(total_base)).font = total_font
        ws.cell(row=current_row, column=9, value=float(total_tax)).font = total_font
        ws.cell(row=current_row, column=10, value=float(total_value)).font = total_font
        
        current_row += 2 # Extra space between items
        
    widths = [15, 12, 20, 15, 15, 15, 15]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    output_path = 'd:/FoodDepartment/purchase_history_report_with_tax.xlsx'
    wb.save(output_path)
    print(f"Report generated successfully at {output_path}")

if __name__ == '__main__':
    generate_purchase_report()
