import csv
import re
import os
import django
from decimal import Decimal

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Category, Item, TaxRate
from django.utils import timezone

def parse_unit(unit_str):
    # Extracts text inside parentheses or after Rs/
    # e.g., "OnionBig(Rs/Kg))" -> "Kg"
    # e.g., "Milk(Rs/Ltr)" -> "Ltr"
    # e.g., "Biscuits (pkt/Rs)" -> "Pkt"
    
    match = re.search(r'\((?:Rs/|RS/|/)?(\w+)', unit_str, re.IGNORECASE)
    if match:
        return match.group(1).capitalize()
    
    # Fallback search for common units
    for u in ['Kg', 'Ltr', 'Pkt', 'Nos', 'Piece', 'Gram']:
        if u.lower() in unit_str.lower():
            return u
            
    return "Unit"

def import_data(csv_path):
    print(f"Starting import from {csv_path}...")
    
    unique_items = {} # (name, category_name) -> {unit, tax_rate}

    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row['Item Name'].strip()
            if not name: continue
            
            unit_raw = row['Item (Rs/Units)']
            unit = parse_unit(unit_raw)
            
            tax_raw = row['Tax Rate'].strip().replace('%', '')
            tax_rate = Decimal(tax_raw) if tax_raw else Decimal('0.00')
            
            category_name = row['Categories'].strip()
            
            key = (name, category_name)
            if key not in unique_items:
                unique_items[key] = {'unit': unit, 'tax_rate': tax_rate}

    print(f"Found {len(unique_items)} unique items. Proceeding to database...")

    for (name, cat_name), data in unique_items.items():
        try:
            category, _ = Category.objects.get_or_create(name=cat_name)
            
            item, created = Item.objects.get_or_create(
                name=name,
                defaults={
                    'category': category,
                    'unit': data['unit']
                }
            )
            
            if created:
                print(f"Created Item: {name} ({data['unit']}) in {cat_name}")
            else:
                # Update unit if it changed or was placeholder
                if item.unit != data['unit']:
                    item.unit = data['unit']
                    item.save()
            
            # Create/Update TaxRate
            # For simplicity, we create a tax rate for today if it doesn't exist or differs from latest
            latest_tax = TaxRate.objects.filter(item=item).order_by('-effective_date').first()
            if not latest_tax or latest_tax.rate_percentage != data['tax_rate']:
                TaxRate.objects.create(
                    item=item,
                    rate_percentage=data['tax_rate'],
                    effective_date=timezone.now().date(),
                    remarks="Imported from CSV"
                )
        except Exception as e:
            print(f"Error importing {name}: {e}")

if __name__ == "__main__":
    csv_file = "/app/Purchased_Food_material.csv"
    if os.path.exists(csv_file):
        import_data(csv_file)
    else:
        print(f"CSV file not found at {csv_file}")
