import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import OpexEntry, Procurement, PurchaseRequest

try:
    opex = OpexEntry.objects.get(id=43)
    print(f"OPEX No: {opex.opex_no}")
    print(f"OPEX ID: {opex.id}")
    
    prs = opex.purchase_requests.all()
    print(f"Linked PRs: {[pr.pr_reference for pr in prs]}")
    
    for pr in prs:
        print(f"\nChecking PR: {pr.pr_reference} (ID: {pr.id})")
        procs = pr.procurements.all()
        print(f"  Total Procurements for this PR: {procs.count()}")
        for proc in procs:
            print(f"    - Item: {proc.item.name}, Opex Entry: {proc.opex_entry_id}")
            
    print(f"\nDirectly linked Procurements for OPEX {opex.id}:")
    direct_procs = opex.procurements.all()
    print(f"  Count: {direct_procs.count()}")
    for proc in direct_procs:
        print(f"    - Item: {proc.item.name}")

except OpexEntry.DoesNotExist:
    print("OPEX 43 not found.")
except Exception as e:
    print(f"Error: {e}")
