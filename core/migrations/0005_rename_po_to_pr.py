from django.db import migrations, models

def rename_po_to_pr_data(apps, schema_editor):
    PurchaseRequest = apps.get_model('core', 'PurchaseRequest')
    for pr in PurchaseRequest.objects.all():
        if pr.pr_reference.startswith('PO-'):
            pr.pr_reference = pr.pr_reference.replace('PO-', 'PR-', 1)
            pr.save()

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_procurement_purchase_order_alter_category_id_and_more'),
    ]

    operations = [
        # 1. Rename Models
        migrations.RenameModel(
            old_name='PurchaseOrder',
            new_name='PurchaseRequest',
        ),
        migrations.RenameModel(
            old_name='PurchaseOrderItem',
            new_name='PurchaseRequestItem',
        ),
        
        # 2. Rename Fields in PurchaseRequest
        migrations.RenameField(
            model_name='PurchaseRequest',
            old_name='po_reference',
            new_name='pr_reference',
        ),
        
        # 3. Rename Fields in PurchaseRequestItem
        migrations.RenameField(
            model_name='PurchaseRequestItem',
            old_name='purchase_order',
            new_name='purchase_request',
        ),
        
        # 4. Rename Field in Procurement
        migrations.RenameField(
            model_name='Procurement',
            old_name='purchase_order',
            new_name='purchase_request',
        ),
        
        # 5. Data Migration: Update existing references from PO- to PR-
        migrations.RunPython(rename_po_to_pr_data),
    ]
