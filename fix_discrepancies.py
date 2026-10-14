import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Item, Inventory, StockBatch, Department
from django.db import transaction
from django.db.models import Sum
from datetime import date
from decimal import Decimal

procurement_dept = Department.objects.get(code='procurement')

with transaction.atomic():
    items = Item.objects.all()
    fixed_positive = 0
    fixed_negative = 0
    
    for item in items:
        inv = Inventory.objects.filter(item=item, department=procurement_dept).first()
        if not inv:
            continue
            
        inv_qty = inv.current_quantity
        batch_qs = StockBatch.objects.filter(item=item, department=procurement_dept).aggregate(Sum('remaining_quantity'))
        batch_qty = batch_qs['remaining_quantity__sum'] or Decimal('0')
        
        diff = inv_qty - batch_qty
        
        if diff > 0:
            # Missing batches: create a system adjustment batch
            price = inv.average_price or Decimal('0')
            if price == 0:
                # find any historical price
                last_batch = StockBatch.objects.filter(item=item, department=procurement_dept).order_by('-batch_date').first()
                if last_batch:
                    price = last_batch.unit_price
            
            StockBatch.objects.create(
                item=item,
                department=procurement_dept,
                initial_quantity=diff,
                remaining_quantity=diff,
                unit_price=price,
                batch_date=date.today()
            )
            fixed_positive += 1
            print(f"Added {diff:.3f} batch to {item.name}")
            
        elif diff < 0:
            # Ghost batches: reduce remaining quantities of existing batches
            to_reduce = abs(diff)
            batches = StockBatch.objects.filter(item=item, department=procurement_dept, remaining_quantity__gt=0).order_by('batch_date')
            for batch in batches:
                if to_reduce <= 0:
                    break
                if batch.remaining_quantity >= to_reduce:
                    batch.remaining_quantity -= to_reduce
                    batch.save()
                    to_reduce = Decimal('0')
                else:
                    to_reduce -= batch.remaining_quantity
                    batch.remaining_quantity = Decimal('0')
                    batch.save()
            fixed_negative += 1
            print(f"Removed {abs(diff):.3f} ghost batch quantity from {item.name}")

    print(f"\nSuccessfully fixed {fixed_positive} items with missing batches and {fixed_negative} items with ghost batches.")
