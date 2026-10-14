import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()
from core.models import Item, StockBatch, StockBatchTransaction
from django.db.models import Sum

breads = Item.objects.filter(name__icontains='Bread')
for bread in breads:
    batches = StockBatch.objects.filter(item=bread)
    purchased_qty = sum(b.initial_quantity for b in batches)
    
    pushed = StockBatchTransaction.objects.filter(
        batch__item=bread, 
        transaction_type='issue'
    ).aggregate(Sum('quantity'))['quantity__sum'] or 0
    
    print(f'Item: {bread.name}')
    print(f'Bought (Purchased): {purchased_qty}')
    print(f'Pushed (Issued to kitchen): {pushed}')
    print('-' * 20)
