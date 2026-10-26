import os
import django
import random
from datetime import datetime, timedelta
from decimal import Decimal
from django.utils import timezone

# Set up Django environment
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import (
    Item, Vendor, PurchaseRequest, PurchaseRequestItem, 
    PurchaseRequestReceipt, Department, Inventory, 
    MaterialRequest, MaterialRequestItem, StockBatch,
    MRItemBatchAllocation, OpexEntry, StockMovement,
    Category, CustomSummaryConfig, Procurement, VendorCategory
)
from django.contrib.auth.models import User

def generate_enhanced_data():
    print("Starting Enhanced Dummy Data Generation...")
    
    # 0. Setup Users & Passwords
    user_creds = {
        "admin": "admin123",
        "HOD": "hod123",
        "GM": "gm123",
        "Procurement": "Procurement",
        "Kitchen": "Kitchen"
    }
    
    users = {}
    for username, password in user_creds.items():
        u, created = User.objects.get_or_create(username=username)
        u.set_password(password)
        u.is_staff = True
        if username == 'admin':
            u.is_superuser = True
        u.save()
        users[username] = u
        print(f"  User '{username}' updated")

    # Get or create procurement/kitchen departments
    procurement_dept, _ = Department.objects.get_or_create(code='procurement', defaults={'name': 'Procurement'})
    kitchen_dept, _ = Department.objects.get_or_create(code='kitchen', defaults={'name': 'Kitchen'})

    # 1. Ensure Vendor Categories and 10+ Vendors exist for Spend Analysis
    v_cat_names = ["Dairy & Eggs", "Grains & Pulses", "Meats & Poultry", "Spices & Oils", "Vegetables & Fruits", "Packaging & Misc"]
    v_categories = [VendorCategory.objects.get_or_create(name=name)[0] for name in v_cat_names]

    vendor_names = [
        "Fresh Farms Ltd", "Reliable Grains Corp", "Heritage Dairy", 
        "Global Spices", "Prime Meats & Poultry", "Green Valley Organics",
        "Ocean Catch Seafood", "Sunrise Bakery", "Packaging Solutions",
        "Essential Oils & Fats", "Modern Beverages", "Eco-Friendly Cleaning"
    ]
    
    for name in vendor_names:
        Vendor.objects.get_or_create(
            name=name, 
            defaults={
                'category': random.choice(v_categories),
                'brand': name.split()[0],
                'phone': f"98765{random.randint(10000, 99999)}"
            }
        )
    
    vendors = list(Vendor.objects.all())
    items = list(Item.objects.all())
    categories = list(Category.objects.all())

    if not all([items, vendors]):
        print("Error: Missing required master data (Items or Vendors)")
        return

    # 2. Comprehensive Cleanup
    print("Cleaning up old transaction data...")
    PurchaseRequest.objects.all().delete()
    MaterialRequest.objects.all().delete()
    Procurement.objects.all().delete()
    OpexEntry.objects.all().delete()
    StockMovement.objects.all().delete()
    StockBatch.objects.all().delete()
    Inventory.objects.update(current_quantity=0, inventory_value=0, average_price=0)

    start_date = (datetime.now() - timedelta(days=90)).date()
    
    # 2. Purchase Tracker & Price Deviation (90 days history)
    print("Generating Purchases and Price Deviations...")
    for i in range(90):
        current_date = start_date + timedelta(days=i)
        if current_date > datetime.now().date():
            break
        
        # Randomly skip some days for PRs
        if random.random() > 0.4:
            vendor = random.choice(vendors)
            
            # Determine status: most are COMPLETED, some recent ones are PENDING
            days_from_now = (datetime.now().date() - current_date).days
            if days_from_now < 3:
                status = random.choice(['AWAITING_L1', 'AWAITING_L2', 'DRAFT'])
            else:
                status = 'COMPLETED'
            
            pr = PurchaseRequest.objects.create(
                pr_reference=f"PR-{current_date.strftime('%Y%m%d')}-{random.randint(100, 999)}",
                vendor=vendor,
                order_date=current_date,
                delivery_date=current_date + timedelta(days=random.randint(1, 3)),
                status=status
            )
            
            # If AWAITING_L2, simulate L1 approval
            if status == 'AWAITING_L2':
                pr.approved_by_l1 = users['HOD']
                pr.approved_at_l1 = timezone.now()
                pr.save()

            pr_total = Decimal('0.00')
            selected_items = random.sample(items, min(len(items), random.randint(4, 10)))
            
            for item in selected_items:
                qty = Decimal(random.randint(50, 200))
                # Base price with some item-specific logic to see trends
                base_price = Decimal(50 + (item.id % 10) * 20) 
                deviation = Decimal(random.uniform(0.9, 1.1)) 
                price = (base_price * deviation).quantize(Decimal('0.00'))
                
                PurchaseRequestItem.objects.create(
                    purchase_request=pr,
                    item=item,
                    quantity_ordered=qty,
                    quantity_received=qty if status == 'COMPLETED' else 0,
                    unit_price=price,
                    unit=item.unit,
                    tax_rate=Decimal('5.00') # 5% tax
                )
                pr_total += (qty * price * Decimal('1.05'))
            
            pr.total_amount = pr_total.quantize(Decimal('0.00'))
            pr.save()

            # Only create receipts/inventory for COMPLETED ones
            if status == 'COMPLETED':
                receipt = PurchaseRequestReceipt.objects.create(
                    purchase_request=pr,
                    purchase_date=pr.delivery_date or pr.order_date,
                    invoice_number=f"INV-{random.randint(1000, 9999)}"
                )
                
                for pr_item in pr.items.all():
                    StockBatch.objects.create(
                        item=pr_item.item,
                        department=procurement_dept,
                        initial_quantity=pr_item.quantity_ordered,
                        remaining_quantity=pr_item.quantity_ordered,
                        unit_price=pr_item.unit_price,
                        batch_date=receipt.purchase_date
                    )

                    inv, _ = Inventory.objects.get_or_create(item=pr_item.item, department=procurement_dept)
                    all_batches = StockBatch.objects.filter(item=pr_item.item, department=procurement_dept, remaining_quantity__gt=0)
                    total_val = sum(b.remaining_quantity * b.unit_price for b in all_batches)
                    total_qty = sum(b.remaining_quantity for b in all_batches)
                    inv.current_quantity = total_qty
                    inv.inventory_value = total_val
                    inv.average_price = (total_val / total_qty).quantize(Decimal('0.00')) if total_qty > 0 else Decimal('0.00')
                    inv.save()
                    
                    Procurement.objects.create(
                        item=pr_item.item,
                        vendor=pr.vendor,
                        purchase_request=pr,
                        receipt=receipt,
                        quantity=pr_item.quantity_ordered,
                        unit_price=pr_item.unit_price,
                        tax_rate=pr_item.tax_rate,
                        total_price=(pr_item.quantity_ordered * pr_item.unit_price),
                        unit=pr_item.unit,
                        purchase_date=receipt.purchase_date
                    )

                    StockMovement.objects.create(
                        item=pr_item.item,
                        quantity=pr_item.quantity_ordered,
                        type='STORE_INWARD',
                        reference_id=pr.pr_reference,
                        user=users['Procurement'],
                        movement_date=timezone.make_aware(datetime.combine(receipt.purchase_date, datetime.min.time()))
                    )

                # 3. Opex Entries
                if random.random() > 0.3:
                    opex = OpexEntry.objects.create(
                        opex_no=f"OPX-{pr.pr_reference}",
                        opex_date=pr.delivery_date or pr.order_date,
                        opex_value=pr.total_amount,
                        bill_no=receipt.invoice_number,
                        bill_date=receipt.purchase_date,
                        bill_status='RECEIVED',
                        payment_status='PAID'
                    )
                    opex.purchase_requests.add(pr)

    # 4. Consumption Report & Transition Report
    print("Generating Consumption and Stock Transitions...")
    for d in range(90):
        current_date = start_date + timedelta(days=d)
        if current_date > datetime.now().date():
            break
        
        days_from_now = (datetime.now().date() - current_date).days
        
        # 1-2 Daily MRs
        for _ in range(random.randint(1, 2)):
            status = 'COMPLETED'
            if days_from_now < 2:
                status = random.choice(['PLACED', 'ACCEPTED', 'COMPLETED'])

            mr = MaterialRequest.objects.create(
                mr_number=f"MR-{current_date.strftime('%Y%m%d')}-{random.randint(10, 99)}",
                department=kitchen_dept,
                order_date=current_date,
                target_date=current_date,
                meal_type=random.choice(['BREAKFAST', 'LUNCH', 'DINNER']),
                user_count=random.randint(50, 200),
                status=status,
                requested_by=users['Kitchen'],
                action_by=users['Procurement'] if status != 'PLACED' else None,
                action_at=timezone.make_aware(datetime.combine(current_date, datetime.min.time())) if status != 'PLACED' else None
            )
            
            selected_items = random.sample(items, min(len(items), random.randint(3, 8)))
            for item in selected_items:
                req_qty = Decimal(random.randint(5, 15))
                
                store_inv = Inventory.objects.filter(item=item, department=procurement_dept).first()
                if not store_inv or store_inv.current_quantity < req_qty:
                    continue

                mr_item = MaterialRequestItem.objects.create(
                    material_request=mr,
                    item=item,
                    original_quantity=req_qty,
                    quantity=req_qty,
                    quantity_issued=req_qty if status in ['ACCEPTED', 'COMPLETED'] else 0,
                    unit=item.unit
                )
                
                if status in ['ACCEPTED', 'COMPLETED']:
                    # Allocation
                    batches = StockBatch.objects.filter(item=item, department=procurement_dept, remaining_quantity__gt=0).order_by('batch_date')
                    rem = req_qty
                    for b in batches:
                        if rem <= 0:
                            break
                        take = min(b.remaining_quantity, rem)
                        b.remaining_quantity -= take
                        b.save()
                        
                        MRItemBatchAllocation.objects.create(
                            mr_item=mr_item,
                            stock_batch=b,
                            quantity_allocated=take
                        )
                        rem -= take
                    
                    # Store Inventory down
                    all_batches = StockBatch.objects.filter(item=item, department=procurement_dept, remaining_quantity__gt=0)
                    total_val = sum(b.remaining_quantity * b.unit_price for b in all_batches)
                    total_qty = sum(b.remaining_quantity for b in all_batches)
                    store_inv.current_quantity = total_qty
                    store_inv.inventory_value = total_val
                    if total_qty > 0:
                        store_inv.average_price = (total_val / total_qty).quantize(Decimal('0.00'))
                    store_inv.save()
                    
                    # Kitchen Inventory up
                    kit_inv, _ = Inventory.objects.get_or_create(item=item, department=kitchen_dept)
                    kit_inv.current_quantity += req_qty
                    kit_inv.average_price = store_inv.average_price
                    kit_inv.inventory_value = kit_inv.current_quantity * kit_inv.average_price
                    kit_inv.save()
                    
                    StockMovement.objects.create(
                        item=item,
                        quantity=req_qty,
                        type='STORE_TO_KITCHEN',
                        reference_id=mr.mr_number,
                        user=users['Procurement'],
                        movement_date=timezone.make_aware(datetime.combine(current_date, datetime.min.time()))
                    )
                    
                    # Record cost for reports
                    mr_item.consumption_unit_cost = store_inv.average_price
                    mr_item.consumption_total_cost = (req_qty * store_inv.average_price).quantize(Decimal('0.00'))
                    mr_item.save()

    # 5. Summary Configs
    print("Setting up Summary Configurations...")
    CustomSummaryConfig.objects.all().delete()
    for cat in random.sample(categories, min(len(categories), 5)):
        CustomSummaryConfig.objects.create(
            config_type='CATEGORY',
            category=cat,
            custom_name=f"Summary: {cat.name}"
        )
    
    print("\nEnhanced Dummy Data Generation Complete!")

if __name__ == "__main__":
    generate_enhanced_data()
