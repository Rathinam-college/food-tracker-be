from rest_framework import serializers
from .models import (
    Category, Item, Vendor, TaxRate, PurchaseRequest, PurchaseRequestItem,
    Inventory, Procurement, CompanyProfile, ShippingLocation, CustomSummaryConfig,
    Role, Profile, Department, MaterialRequest, MaterialRequestItem, StockMovement,
    OpexEntry, VendorCategory, StockBatch, MRItemBatchAllocation,
    VendorReturn, VendorReturnItem, VendorDocument,
    PurchaseRequestReceipt, PurchaseRequestReceiptFile, PasswordResetRequest
)
from django.contrib.auth.models import User
from decimal import Decimal

class StockMovementSerializer(serializers.ModelSerializer):
    item_name = serializers.ReadOnlyField(source='item.name')
    user_username = serializers.ReadOnlyField(source='user.username')
    item_unit = serializers.ReadOnlyField(source='item.unit')

    class Meta:
        model = StockMovement
        fields = '__all__'

    def to_representation(self, instance):
        data = super().to_representation(instance)
        ref_id = data.get('reference_id') or ""
        
        if "Batch" in ref_id:
            try:
                import re
                batch_ids = [int(x) for x in re.findall(r'Batch (\d+)', ref_id)]
                if batch_ids:
                    from .models import StockBatch
                    # N+1 is acceptable here because this endpoint is strictly paginated 
                    # and usually batches per movement is just 1.
                    sbs = StockBatch.objects.filter(id__in=batch_ids).select_related('procurement__purchase_request')
                    batch_details_list = []
                    
                    for sb in sbs:
                        if sb.procurement and sb.procurement.purchase_request:
                            pr_ref = sb.procurement.purchase_request.pr_reference
                        elif sb.procurement:
                            pr_ref = "Direct Purchase"
                        else:
                            pr_ref = "Opening Stock"
                        batch_details_list.append(f"{pr_ref} (Batch {sb.id})")
                        
                    if batch_details_list:
                        data['reference_id'] = ", ".join(batch_details_list)
            except Exception as e:
                pass
                
        return data


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = '__all__'


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'


class TaxRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxRate
        fields = '__all__'


class ItemSerializer(serializers.ModelSerializer):
    current_stock = serializers.SerializerMethodField()
    effective_stock = serializers.SerializerMethodField()
    locked_stock = serializers.SerializerMethodField()
    current_price = serializers.SerializerMethodField()
    category_name = serializers.ReadOnlyField(source='category.name')
    current_tax_rate = serializers.SerializerMethodField()

    class Meta:
        model = Item
        fields = [
            'id', 'name', 'category', 'category_name', 
            'unit', 'current_tax_rate', 'min_stock_level',
            'current_stock', 'effective_stock', 'locked_stock', 'current_price'
        ]

    def _get_procurement_inventory(self, obj):
        return obj.inventory_records.filter(department__name__icontains='Procurement').first()

    def get_current_stock(self, obj):
        inventory = self._get_procurement_inventory(obj)
        return float(inventory.current_quantity) if inventory else 0.0

    def get_locked_stock(self, obj):
        from django.db.models import Sum
        from .models import MaterialRequestItem
        return float(MaterialRequestItem.objects.filter(
            item=obj,
            material_request__status__in=['PLACED', 'MGMT_APPROVED']
        ).aggregate(Sum('quantity'))['quantity__sum'] or 0.0)

    def get_effective_stock(self, obj):
        inventory = self._get_procurement_inventory(obj)
        current_qty = float(inventory.current_quantity) if inventory else 0.0
        if current_qty <= 0:
            return 0.0
            
        from django.db.models import Sum
        from .models import MaterialRequestItem
        
        locked_stock = MaterialRequestItem.objects.filter(
            item=obj,
            material_request__status__in=['PLACED', 'MGMT_APPROVED']
        ).aggregate(Sum('quantity'))['quantity__sum'] or 0.0
        
        return max(0.0, current_qty - float(locked_stock))

    def get_current_price(self, obj):
        inventory = self._get_procurement_inventory(obj)
        return float(inventory.average_price) if inventory else 0.0

    def get_current_tax_rate(self, obj):
        latest_tax = obj.tax_history.first()
        return latest_tax.rate_percentage if latest_tax else 0


class VendorCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorCategory
        fields = '__all__'


class VendorSerializer(serializers.ModelSerializer):
    category_name = serializers.ReadOnlyField(source='category.name')

    class Meta:
        model = Vendor
        fields = '__all__'


class VendorDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorDocument
        fields = '__all__'


class CompanyProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyProfile
        fields = '__all__'


class ShippingLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingLocation
        fields = '__all__'


class InventorySerializer(serializers.ModelSerializer):
    item_name = serializers.ReadOnlyField(source='item.name')
    category_name = serializers.ReadOnlyField(source='item.category.name')
    unit = serializers.ReadOnlyField(source='item.unit')
    min_stock_level = serializers.ReadOnlyField(source='item.min_stock_level')
    current_tax_rate = serializers.SerializerMethodField()

    class Meta:
        model = Inventory
        fields = '__all__'

    def get_current_tax_rate(self, obj):
        # 1. Try to get tax rate from the most recent procurement record
        from .models import Procurement
        latest_proc = Procurement.objects.filter(item=obj.item).order_by('-purchase_date', '-id').first()
        if latest_proc and latest_proc.tax_rate > 0:
            return latest_proc.tax_rate
            
        # 2. Fallback to Master Tax History
        latest_tax = obj.item.tax_history.first()
        return latest_tax.rate_percentage if latest_tax else 0


class StockBatchSerializer(serializers.ModelSerializer):
    item_name = serializers.ReadOnlyField(source='item.name')
    item_unit = serializers.ReadOnlyField(source='item.unit')
    pr_reference = serializers.SerializerMethodField()

    class Meta:
        model = StockBatch
        fields = [
            'id', 'item', 'item_name', 'item_unit', 'department',
            'procurement', 'pr_reference',
            'initial_quantity', 'remaining_quantity',
            'unit_price', 'batch_date', 'created_at'
        ]

    def get_pr_reference(self, obj):
        if obj.procurement and obj.procurement.purchase_request:
            return obj.procurement.purchase_request.pr_reference
        return None


class MRItemBatchAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = MRItemBatchAllocation
        fields = '__all__'


class ProcurementSerializer(serializers.ModelSerializer):
    item_name = serializers.ReadOnlyField(source='item.name')
    item_unit = serializers.ReadOnlyField(source='item.unit')
    vendor_name = serializers.ReadOnlyField(source='vendor.name')
    invoice_number = serializers.ReadOnlyField(source='receipt.invoice_number')
    returned_value = serializers.SerializerMethodField()

    class Meta:
        model = Procurement
        fields = '__all__'
        
    def get_returned_value(self, obj):
        from .models import VendorReturnItem
        from django.db.models import Sum, F, Case, When, DecimalField
        
        # Calculate returned value for this item in this PR
        return_items = VendorReturnItem.objects.filter(
            vendor_return__purchase_request=obj.purchase_request,
            item=obj.item
        )
        if not return_items.exists():
            return 0
            
        summary = return_items.annotate(
            discounted_price=Case(
                When(discount_type='PERCENTAGE', then=F('unit_price') * (1 - F('discount_value') / 100)),
                default=F('unit_price') - F('discount_value'),
                output_field=DecimalField()
            )
        ).aggregate(
            total=Sum(F('quantity') * F('discounted_price') * (1 + F('tax_rate') / 100))
        )
        return float((summary['total'] or 0))


class PurchaseRequestItemSerializer(serializers.ModelSerializer):
    item_name = serializers.ReadOnlyField(source='item.name')
    line_total = serializers.ReadOnlyField()
    received_tax_rate = serializers.SerializerMethodField()
    returnable_quantity = serializers.SerializerMethodField()
    
    class Meta:
        model = PurchaseRequestItem
        fields = [
            'id', 'item', 'item_name', 'quantity_ordered', 
            'quantity_received', 'returnable_quantity', 'unit_price', 'tax_rate', 
            'received_tax_rate', 'discount_type', 'discount_value', 
            'line_total', 'unit'
        ]
        extra_kwargs = {
            'purchase_request': {'read_only': True}
        }

    def get_received_tax_rate(self, obj):
        # Find the most recent procurement record for this PR and item
        proc = obj.purchase_request.procurements.filter(item=obj.item).last()
        return proc.tax_rate if proc else obj.tax_rate

    def get_returnable_quantity(self, obj):
        batches = obj.purchase_request.procurements.filter(
            item=obj.item
        ).values_list('stock_batches__remaining_quantity', flat=True)
        total = sum([b for b in batches if b is not None])
        return float(total)
    def validate_unit_price(self, value):
        from decimal import Decimal
        if value is None or Decimal(str(value)) <= 0:
            raise serializers.ValidationError("Unit price must be strictly greater than 0.")
        return value

    def validate_quantity_ordered(self, value):
        from decimal import Decimal
        if value is None or Decimal(str(value)) <= 0:
            raise serializers.ValidationError("Quantity ordered must be strictly greater than 0.")
        return value






class PurchaseRequestReceiptFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseRequestReceiptFile
        fields = ['id', 'file', 'uploaded_at']

class PurchaseRequestReceiptSerializer(serializers.ModelSerializer):
    files = PurchaseRequestReceiptFileSerializer(many=True, read_only=True)
    
    class Meta:
        model = PurchaseRequestReceipt
        fields = ['id', 'invoice_number', 'purchase_date', 'created_at', 'files']

class PurchaseRequestSerializer(serializers.ModelSerializer):
    vendor_name = serializers.ReadOnlyField(source='vendor.name')
    ship_to_name = serializers.ReadOnlyField(source='ship_to.name')
    items = PurchaseRequestItemSerializer(many=True, required=False)
    receipts = PurchaseRequestReceiptSerializer(many=True, read_only=True)
    procurements = ProcurementSerializer(many=True, read_only=True)
    approved_by_l1_name = serializers.SerializerMethodField()
    approved_by_l2_name = serializers.SerializerMethodField()
    received_total = serializers.SerializerMethodField()
    confirmed_total = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseRequest
        fields = '__all__'

    def get_approved_by_l1_name(self, obj):
        if obj.approved_by_l1:
            return obj.approved_by_l1.get_full_name() or obj.approved_by_l1.username
        return None

    def get_approved_by_l2_name(self, obj):
        if obj.approved_by_l2:
            return obj.approved_by_l2.get_full_name() or obj.approved_by_l2.username
        return None

    def get_received_total(self, obj):
        from decimal import Decimal
        return sum(p.total_price for p in obj.procurements.all()) or Decimal('0.00')

    def get_confirmed_total(self, obj):
        from decimal import Decimal
        received_total = sum(p.total_price for p in obj.procurements.all()) or Decimal('0.00')
        remaining_approved = Decimal('0.00')
        for item in obj.items.all():
            qty_remaining = max(Decimal('0'), item.quantity_ordered - item.quantity_received)
            if qty_remaining > 0 and item.quantity_ordered > 0:
                # Proportion of the approved line total for remaining quantity
                ratio = qty_remaining / item.quantity_ordered
                remaining_approved += Decimal(str(item.line_total)) * ratio
        return (received_total + remaining_approved).quantize(Decimal('0.01'))

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        purchase_request = PurchaseRequest.objects.create(**validated_data)
        total = 0
        for item_data in items_data:
            from decimal import Decimal
            pri = PurchaseRequestItem.objects.create(
                purchase_request=purchase_request, **item_data
            )
            # Calculate line total with discount
            base_price = pri.quantity_ordered * pri.unit_price
            if pri.discount_type == 'PERCENTAGE':
                discount_amount = base_price * (pri.discount_value / Decimal('100'))
            else:
                discount_amount = pri.discount_value
            
            discounted_price = base_price - discount_amount
            total += discounted_price * (Decimal('1') + (pri.tax_rate / Decimal('100')))
        
        purchase_request.total_amount = total
        purchase_request.save()
        return purchase_request

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        instance.pr_reference = validated_data.get(
            'pr_reference', instance.pr_reference
        )
        instance.vendor = validated_data.get('vendor', instance.vendor)
        instance.order_date = validated_data.get(
            'order_date', instance.order_date
        )
        
        old_delivery_date = instance.delivery_date
        new_delivery_date = validated_data.get(
            'delivery_date', instance.delivery_date
        )
        instance.delivery_date = new_delivery_date
        
        # Sync procurement records if delivery date changed
        if old_delivery_date != new_delivery_date:
            instance.procurements.all().update(purchase_date=new_delivery_date)
            
        instance.status = validated_data.get('status', instance.status)
        instance.bill_image = validated_data.get('bill_image', instance.bill_image)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            total = 0
            for item_data in items_data:
                pri = PurchaseRequestItem.objects.create(
                    purchase_request=instance, **item_data
                )
                # Calculate line total with discount
                base_price = pri.quantity_ordered * pri.unit_price
                if pri.discount_type == 'PERCENTAGE':
                    discount_amount = base_price * (pri.discount_value / 100)
                else:
                    discount_amount = pri.discount_value
                
                discounted_price = base_price - discount_amount
                total += discounted_price * (1 + (pri.tax_rate / 100))
            instance.total_amount = total
            instance.save()
        
        return instance


class OpexEntrySerializer(serializers.ModelSerializer):
    vendor_name = serializers.SerializerMethodField()
    pr_references = serializers.SerializerMethodField()
    pr_details = PurchaseRequestSerializer(source='purchase_requests', many=True, read_only=True)
    procurements = ProcurementSerializer(many=True, read_only=True)

    class Meta:
        model = OpexEntry
        fields = '__all__'

    def get_vendor_name(self, obj):
        # Taking from the first PR since they are grouped by vendor
        first_pr = obj.purchase_requests.first()
        return first_pr.vendor.name if first_pr else "N/A"

    def get_pr_references(self, obj):
        return ", ".join([pr.pr_reference for pr in obj.purchase_requests.all()])

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        if instance.bill_image:
            ret['bill_image'] = instance.bill_image.url
        return ret

class CustomSummaryConfigSerializer(serializers.ModelSerializer):
    category_name = serializers.ReadOnlyField(source='category.name')
    item_name = serializers.ReadOnlyField(source='item.name')
    item_details = ItemSerializer(source='specific_items', many=True, read_only=True)

    class Meta:
        model = CustomSummaryConfig
        fields = ['id', 'config_type', 'custom_name', 'display_name', 'category', 'category_name', 'item', 'item_name', 'display_order', 'show_total_only', 'specific_items', 'item_details']

class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = '__all__'


class ProfileSerializer(serializers.ModelSerializer):
    role_details = RoleSerializer(source='role', read_only=True)
    department_details = DepartmentSerializer(source='departments', many=True, read_only=True)

    class Meta:
        model = Profile
        fields = ['role', 'role_details', 'plain_password', 'departments', 'department_details', 'landing_page', 'auto_approve', 'auto_approve_threshold', 'can_verify_mr', 'show_switch_dept']


class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name', 'email', 'is_staff', 'is_superuser', 'profile', 'permissions']

    def get_permissions(self, obj):
        # Default all to False
        perms = {field.name: False for field in Role._meta.fields if field.name.startswith('can_')}
        
        if obj.is_superuser:
            return {k: True for k in perms}
        
        if hasattr(obj, 'profile'):
            profile = obj.profile
            # 1. Apply Role-based permissions
            if profile.role:
                for field in Role._meta.fields:
                    if field.name.startswith('can_'):
                        perms[field.name] = getattr(profile.role, field.name, False)
            
            # 2. Apply Profile-level overrides (Individual toggles)
            if hasattr(profile, 'can_verify_mr') and profile.can_verify_mr:
                perms['can_verify_mr'] = True
                
        return perms

class MaterialRequestItemSerializer(serializers.ModelSerializer):
    item_name = serializers.ReadOnlyField(source='item.name')
    
    class Meta:
        model = MaterialRequestItem
        fields = '__all__'
        extra_kwargs = {
            'material_request': {'read_only': True}
        }

class MaterialRequestSerializer(serializers.ModelSerializer):
    requested_by_username = serializers.ReadOnlyField(source='requested_by.username')
    action_by_username = serializers.ReadOnlyField(source='action_by.username')
    mgmt_verified_by_username = serializers.ReadOnlyField(source='mgmt_verified_by.username')
    items = MaterialRequestItemSerializer(many=True, required=False)

    class Meta:
        model = MaterialRequest
        fields = '__all__'
        extra_kwargs = {
            'requested_by': {'read_only': True},
            'action_by': {'read_only': True},
            'action_at': {'read_only': True},
            'is_acknowledged': {'read_only': True},
            'mgmt_verified_by': {'read_only': True},
            'mgmt_verified_at': {'read_only': True}
        }

    def validate_user_count(self, value):
        if value is None or int(value) <= 0:
            raise serializers.ValidationError("User count details must be strictly greater than 0.")
        return value

    def validate(self, attrs):
        from .models import StockBatch, MaterialRequestItem, Inventory
        from django.db.models import Sum
        from decimal import Decimal
        from datetime import datetime
        
        order_date = attrs.get('order_date', None)
        if not order_date and self.instance:
            order_date = self.instance.order_date
            
        if not order_date:
            order_date = datetime.now().date()
            
        target_date = attrs.get('target_date', None)
        if not target_date and self.instance:
            target_date = self.instance.target_date
            
        if target_date and order_date and target_date < order_date:
            raise serializers.ValidationError({"target_date": "Target date must be greater than or equal to the order date."})
            
        items_data = self.initial_data.get('items', []) if hasattr(self, 'initial_data') else []
        # If we are parsing from validated_data:
        if 'items' in attrs:
            items_data = attrs['items']
            
        # If no items provided in request, but we are updating an existing instance (like Draft -> Placed)
        if not items_data and self.instance:
            items_data = [{'item_id': item.item_id, 'quantity': item.quantity} for item in self.instance.items.all()]
            
        if not items_data:
            return attrs
            
        for item_data in items_data:
            item = item_data.get('item')
            if not item:
                # Might be updating just the request, items not fully resolved. 
                # If we are parsing from raw data, item is an ID.
                item_id = item_data.get('item_id') or (item_data.get('item') if isinstance(item_data.get('item'), int) else None)
                if not item_id and hasattr(item, 'id'):
                    item_id = item.id
                
                if item_id:
                    from .models import Item
                    item = Item.objects.filter(id=item_id).first()
            
            if not item:
                continue
                
            req_qty = Decimal(str(item_data.get('quantity', '0')))
            
            if req_qty <= 0:
                raise serializers.ValidationError(
                    f"Requested quantity for {item.name} must be greater than zero."
                )
            
            inwards = StockBatch.objects.filter(
                item=item,
                department__code='procurement',
                batch_date__lte=order_date
            ).aggregate(total=Sum('initial_quantity'))['total'] or Decimal('0')
            
            outwards = MaterialRequestItem.objects.filter(
                item=item,
                material_request__order_date__lte=order_date,
                material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED']
            ).aggregate(total=Sum('quantity_issued'))['total'] or Decimal('0')
            
            from .models import VendorReturnItem, StockMovement
            vendor_returns = VendorReturnItem.objects.filter(
                item=item,
                vendor_return__return_date__lte=order_date
            ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
            
            adjustments = StockMovement.objects.filter(
                item=item,
                type__in=['WASTAGE', 'BREAKAGE', 'EXPIRED', 'AUDIT'],
                movement_date__date__lte=order_date
            ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
            
            historical_stock = inwards - outwards - vendor_returns - adjustments
            
            pending_query = MaterialRequestItem.objects.filter(
                item=item,
                material_request__status__in=['PLACED', 'MGMT_APPROVED']
            )
            if self.instance:
                pending_query = pending_query.exclude(material_request=self.instance)
            
            pending_mrs = pending_query.aggregate(total=Sum('quantity'))['total'] or Decimal('0')
            
            inv = Inventory.objects.filter(item=item, department__code='procurement').first()
            current_stock = inv.current_quantity if inv else Decimal('0')
            
            effective_current_stock = current_stock - pending_mrs
            effective_historical_stock = historical_stock - pending_mrs
            
            available_stock = min(effective_historical_stock, effective_current_stock)
            available_stock = max(Decimal('0'), available_stock)
            
            if req_qty > available_stock:
                raise serializers.ValidationError(
                    f"Requested quantity {req_qty} for {item.name} exceeds available stock ({available_stock})."
                )
                
        return attrs

    def create(self, validated_data):
        from django.db import transaction
        items_data = validated_data.pop('items', [])
        
        with transaction.atomic():
            material_request = MaterialRequest.objects.create(**validated_data)
            for item_data in items_data:
                item = MaterialRequestItem(material_request=material_request, **item_data)
                item.clean()
                item.save()
                
        return material_request

    def update(self, instance, validated_data):
        from django.db import transaction
        if instance.status != 'DRAFT':
            raise serializers.ValidationError({"error": "Only DRAFT requests can be edited."})
            
        items_data = validated_data.pop('items', None)
        
        with transaction.atomic():
            instance.mr_number = validated_data.get('mr_number', instance.mr_number)
            instance.order_date = validated_data.get('order_date', instance.order_date)
            instance.target_date = validated_data.get('target_date', instance.target_date)
            instance.meal_type = validated_data.get('meal_type', instance.meal_type)
            instance.user_count = validated_data.get('user_count', instance.user_count)
            instance.status = validated_data.get('status', instance.status)
            instance.remarks = validated_data.get('remarks', instance.remarks)
            instance.save()

            if items_data is not None:
                instance.items.all().delete()
                for item_data in items_data:
                    item = MaterialRequestItem(material_request=instance, **item_data)
                    item.clean()
                    item.save()
        
        return instance


class VendorReturnItemSerializer(serializers.ModelSerializer):
    item_name = serializers.ReadOnlyField(source='item.name')
    batch_id = serializers.IntegerField(required=True, write_only=True)
    batch_display = serializers.SerializerMethodField()
    total_amount = serializers.SerializerMethodField()

    class Meta:
        model = VendorReturnItem
        fields = '__all__'
        read_only_fields = ['vendor_return', 'batch']

    def get_batch_display(self, obj):
        return f"Batch {obj.batch.id}" if obj.batch else None

    def get_total_amount(self, obj):
        from decimal import Decimal
        base_price = obj.unit_price * obj.quantity
        discount_amount = Decimal('0')
        if obj.discount_type == 'PERCENTAGE':
            discount_amount = base_price * (obj.discount_value / Decimal('100'))
        else:
            discount_amount = obj.discount_value
            
        discounted_price = base_price - discount_amount
        line_tax_factor = Decimal('1') + (obj.tax_rate / Decimal('100'))
        return (discounted_price * line_tax_factor).quantize(Decimal('0.00'))

class VendorReturnSerializer(serializers.ModelSerializer):
    items = VendorReturnItemSerializer(many=True)
    vendor_name = serializers.ReadOnlyField(source='vendor.name')
    created_by_username = serializers.ReadOnlyField(source='created_by.username')

    class Meta:
        model = VendorReturn
        fields = '__all__'
        read_only_fields = ['created_by', 'total_amount']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        
        from django.db import transaction
        from .models import StockBatch, Inventory, StockMovement, Department
        
        with transaction.atomic():
            vendor_return = VendorReturn.objects.create(**validated_data)
            store_dept = Department.objects.get(code='procurement')
            total_amount = 0
            
            for item_data in items_data:
                batch_id = item_data.pop('batch_id')
                qty = item_data['quantity']
                
                try:
                    batch = StockBatch.objects.select_for_update().get(id=batch_id, item=item_data['item'], department=store_dept)
                except StockBatch.DoesNotExist:
                    raise serializers.ValidationError({"error": f"Batch {batch_id} not found or does not belong to this item."})
                    
                if batch.remaining_quantity < qty:
                    raise serializers.ValidationError({"error": f"Insufficient quantity in Batch {batch.id}. Requested {qty}, Available {batch.remaining_quantity}."})
                    
                # Deduct from Batch
                batch.remaining_quantity -= qty
                batch.save()
                
                # Deduct from Inventory
                inventory = Inventory.objects.select_for_update().get(item=item_data['item'], department=store_dept)
                inventory.current_quantity -= qty
                total_value_to_reduce = (qty * batch.unit_price).quantize(Decimal('0.00'))
                inventory.inventory_value -= total_value_to_reduce
                if inventory.current_quantity > 0:
                    inventory.average_price = (inventory.inventory_value / inventory.current_quantity).quantize(Decimal('0.00'))
                else:
                    inventory.inventory_value = Decimal('0.00')
                    inventory.average_price = Decimal('0.00')
                inventory.save()
                
                # Create Stock Movement
                StockMovement.objects.create(
                    item=item_data['item'],
                    quantity=qty,
                    type='VENDOR_RETURN',
                    user=validated_data.get('created_by'),
                    remarks=item_data.get('reason', ''),
                    reference_id=f"Batch {batch.id}"
                )
                
                # Create VendorReturnItem tied to batch
                item_data['batch_id'] = batch.id
                vri = VendorReturnItem.objects.create(vendor_return=vendor_return, **item_data)
                
                # Accumulate total return value for VendorReturn model
                # Tax calculation exactly matches how it was originally shown in VendorReturnForm
                base = vri.quantity * vri.unit_price
                if vri.discount_type == 'PERCENTAGE':
                    discount = base * (vri.discount_value / Decimal('100'))
                else:
                    discount = vri.discount_value  # Warning: this is fixed discount per item row, handled roughly here
                
                tax = (base - discount) * (vri.tax_rate / Decimal('100'))
                total_amount += (base - discount + tax)
                
            vendor_return.total_amount = total_amount
            vendor_return.save()
            
        return vendor_return

class PasswordResetRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = PasswordResetRequest
        fields = '__all__'
        read_only_fields = ['status', 'requested_at', 'completed_at', 'completed_by']



from .models import OrganizationUnit, Designation, DeploymentPlan, DeploymentStatus

class DeploymentStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeploymentStatus
        fields = '__all__'

class DeploymentPlanSerializer(serializers.ModelSerializer):
    designation_name = serializers.CharField(source='designation.name', read_only=True)
    status = DeploymentStatusSerializer(read_only=True)
    actual_count = serializers.SerializerMethodField()
    children_deployments = serializers.SerializerMethodField()

    class Meta:
        model = DeploymentPlan
        fields = ['id', 'designation', 'designation_name', 'required_count', 'month', 'year', 'status', 'actual_count', 'children_deployments', 'reports_to', 'organization_unit']

    def get_actual_count(self, obj):
        if hasattr(obj, 'status'):
            return obj.status.actual_count
        return 0

    def get_children_deployments(self, obj):
        children = obj.children_deployments.all()
        return DeploymentPlanSerializer(children, many=True).data

class OrganizationUnitSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()
    deployments = serializers.SerializerMethodField()
    
    # Aggregated fields for the whole subtree
    total_required = serializers.SerializerMethodField()
    total_present = serializers.SerializerMethodField()

    class Meta:
        model = OrganizationUnit
        fields = ['id', 'name', 'type', 'children', 'deployments', 'total_required', 'total_present', 'show_counts']

    def get_children(self, obj):
        # Recursively serialize children
        children = obj.children.filter(is_active=True).order_by('display_order')
        return OrganizationUnitSerializer(children, many=True).data

    def get_deployments(self, obj):
        # Only return root-level deployments (those not reporting to another role)
        deployments = obj.deployments.filter(reports_to__isnull=True)
        return DeploymentPlanSerializer(deployments, many=True).data

    def get_total_required(self, obj):
        return sum(d.required_count for d in obj.deployments.all())

    def get_total_present(self, obj):
        return sum(d.status.actual_count for d in obj.deployments.all() if hasattr(d, 'status'))

class DesignationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Designation
        fields = '__all__'

class OrganizationUnitCRUDSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationUnit
        fields = '__all__'

class DeploymentPlanCRUDSerializer(serializers.ModelSerializer):
    designation_name = serializers.CharField(source='designation.name', read_only=True)

    class Meta:
        model = DeploymentPlan
        fields = '__all__'
