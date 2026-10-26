from core.models import Inventory, Procurement, TaxRate
from decimal import Decimal

queryset = Inventory.objects.filter(department__code='procurement')
total_tax = Decimal('0')
print(f"Inventory Count: {queryset.count()}")

for inv in queryset:
    latest_proc = Procurement.objects.filter(item=inv.item).order_by('-purchase_date', '-id').first()
    if latest_proc:
        active_tax_rate = latest_proc.tax_rate
        source = "Procurement"
    else:
        latest_tax_hist = inv.item.tax_history.first()
        active_tax_rate = latest_tax_hist.rate_percentage if latest_tax_hist else Decimal('0')
        source = "TaxHistory"
    
    item_tax = (inv.inventory_value * active_tax_rate) / (100 + active_tax_rate)
    total_tax += item_tax
    if item_tax > 0:
        print(f"Item: {inv.item.name}, Val: {inv.inventory_value}, TaxRate: {active_tax_rate}, ItemTax: {item_tax}, Source: {source}")

print(f"Total Tax: {total_tax}")
