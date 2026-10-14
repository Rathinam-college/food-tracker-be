from django.db import models
from decimal import Decimal
from django.utils import timezone
import os
from django.db.models.signals import post_delete, pre_save, post_save
from django.dispatch import receiver
from django.core.files.base import ContentFile
from django.contrib.auth.models import User
from .file_utils import compress_file

class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, unique=True) # 'procurement', 'kitchen'
    description = models.TextField(blank=True, null=True)
    logo = models.CharField(max_length=100, blank=True, null=True, default='📦') # Emoji or icon class
    display_name = models.CharField(max_length=200, blank=True, null=True)

    def __str__(self):
        return self.name

class Role(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    
    # Permissions
    can_place_po = models.BooleanField(default=False)
    can_confirm_receive = models.BooleanField(default=False)
    can_edit_po = models.BooleanField(default=False)
    can_delete_po = models.BooleanField(default=False)
    can_generate_po = models.BooleanField(default=False)
    can_upload_bill = models.BooleanField(default=False)
    
    can_manage_items = models.BooleanField(default=False)
    can_manage_categories = models.BooleanField(default=False)
    can_manage_tax = models.BooleanField(default=False)
    can_manage_vendors = models.BooleanField(default=False)
    can_manage_locations = models.BooleanField(default=False)
    can_manage_company_profile = models.BooleanField(default=False)
    can_manage_summary_config = models.BooleanField(default=False)
    
    can_view_reports = models.BooleanField(default=False)
    can_view_pr_entries = models.BooleanField(default=False)

    # Specific Reports
    can_view_stock_ledger = models.BooleanField(default=False)
    can_view_consumption_report = models.BooleanField(default=False)
    can_view_opex_report = models.BooleanField(default=False)
    can_view_custom_monthly_summary = models.BooleanField(default=False)

    # Kitchen Permissions
    can_place_mr = models.BooleanField(default=False)
    can_view_mr = models.BooleanField(default=False)
    can_manage_mr = models.BooleanField(default=False)
    can_delete_mr = models.BooleanField(default=False)
    can_view_stock = models.BooleanField(default=False)

    can_approve_pr_l1 = models.BooleanField(default=False)
    can_approve_pr_l2 = models.BooleanField(default=False)
    can_verify_mr = models.BooleanField(default=False)  # Management: verify kitchen MRs before procurement

    # Administrative
    can_access_admin_panel = models.BooleanField(default=False)
    can_perform_hard_reset = models.BooleanField(default=False)

    def __str__(self):
        return self.name

class Profile(models.Model):
    LANDING_PAGE_CHOICES = [
        ('launcher', 'App Launcher'),
        ('procurement', 'Procurement Dashboard'),
        ('kitchen', 'Kitchen Dashboard'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True)
    plain_password = models.CharField(max_length=255, blank=True, null=True)
    departments = models.ManyToManyField(Department, related_name='profiles', blank=True)
    landing_page = models.CharField(max_length=20, choices=LANDING_PAGE_CHOICES, default='launcher')
    auto_approve = models.BooleanField(default=False)
    auto_approve_threshold = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    can_verify_mr = models.BooleanField(default=False)
    show_switch_dept = models.BooleanField(default=True)


    def __str__(self):
        return f"{self.user.username} - {self.role.name if self.role else 'No Role'}"

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

class Item(models.Model):
    name = models.CharField(max_length=200, unique=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='items')
    unit = models.CharField(max_length=50) # e.g., Kg, Ltr, Bag (25kg)
    min_stock_level = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    def __str__(self):
        return self.name

@receiver(post_save, sender=Item)
def initialize_item_inventory(sender, instance, created, **kwargs):
    """
    Automatically create 0-quantity inventory records for all departments
    when a new item is created in Master Data.
    """
    if created:
        from .models import Department, Inventory
        for dept in Department.objects.all():
            Inventory.objects.get_or_create(item=instance, department=dept)

class VendorCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

class Vendor(models.Model):
    name = models.CharField(max_length=200, unique=True)
    category = models.ForeignKey(VendorCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='vendors')
    contact_person = models.CharField(max_length=100, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    brand = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class VendorDocument(models.Model):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='documents')
    name = models.CharField(max_length=200)
    file = models.FileField(upload_to='vendor_documents/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.file: 
            filename = self.file.name.lower()
            if filename.endswith(('.jpg', '.jpeg', '.png')):
                self.file = compress_file(self.file)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} for {self.vendor.name}"

class TaxRate(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='tax_history')
    rate_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    effective_date = models.DateField()
    remarks = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-effective_date']

    def __str__(self):
        return f"{self.item.name} - {self.rate_percentage}% ({self.effective_date})"

class CompanyProfile(models.Model):
    name = models.CharField(max_length=200)
    address = models.TextField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)

    class Meta:
        verbose_name = "Company Profile"
        verbose_name_plural = "Company Profile"

    def __str__(self):
        return self.name


class ShippingLocation(models.Model):
    name = models.CharField(max_length=200)
    address = models.TextField(blank=True, null=True)
    contact_person = models.CharField(max_length=100, blank=True, null=True)
    is_default = models.BooleanField(default=False)

    def __str__(self):
        return self.name


def purchase_request_bill_path(instance, filename):
    # Format: YYYYMMDD_PR-REF/filename
    date_str = instance.order_date.strftime('%Y%m%d')
    return f"{date_str}_{instance.pr_reference}/{filename}"

def opex_bill_path(instance, filename):
    # Format: opex_bills/YYYYMMDD_OPEX-NO/filename
    date_str = instance.opex_date.strftime('%Y%m%d')
    return f"opex_bills/{date_str}_{instance.opex_no}/{filename}"


class PurchaseRequest(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('AWAITING_L1', 'Awaiting L1 Approval'),
        ('AWAITING_L2', 'Awaiting L2 Approval'),
        ('AWAITING_APPROVAL', 'Awaiting Approval(s)'),
        ('PLACED', 'Placed'),
        ('RECEIVED', 'Partially Received'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]
    pr_reference = models.CharField(max_length=100, unique=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='purchase_requests')
    order_date = models.DateField()
    delivery_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    ship_to = models.ForeignKey(ShippingLocation, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_requests')
    terms = models.TextField(blank=True, null=True)
    authorized_by = models.CharField(max_length=200, blank=True, null=True)
    
    PURCHASE_TYPE_CHOICES = [
        ('REGULAR', 'Regular Purchase'),
        ('SPECIAL_EVENT', 'Special Event'),
    ]
    purchase_type = models.CharField(max_length=20, choices=PURCHASE_TYPE_CHOICES, default='REGULAR')
    event_name = models.CharField(max_length=255, blank=True, null=True)
    
    # Audit for Approvals
    approved_by_l1 = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='pr_approvals_l1')
    approved_at_l1 = models.DateTimeField(null=True, blank=True)
    approved_by_l2 = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='pr_approvals_l2')
    approved_at_l2 = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='pr_closed')
    closed_at = models.DateTimeField(null=True, blank=True)

    bill_image = models.FileField(upload_to=purchase_request_bill_path, null=True, blank=True)

    # Audit timestamps
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.bill_image:
            filename = self.bill_image.name.lower()
            if filename.endswith(('.jpg', '.jpeg', '.png')):
                self.bill_image = compress_file(self.bill_image)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.pr_reference

def pr_receipt_file_path(instance, filename):
    # Format: pr_receipts/YYYYMMDD_PR-REF/filename
    date_str = instance.receipt.purchase_date.strftime('%Y%m%d')
    return f"pr_receipts/{date_str}_{instance.receipt.purchase_request.pr_reference}/{filename}"

class PurchaseRequestReceipt(models.Model):
    purchase_request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name='receipts')
    invoice_number = models.CharField(max_length=100, blank=True, null=True)
    purchase_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Receipt for {self.purchase_request.pr_reference} on {self.purchase_date}"

class PurchaseRequestReceiptFile(models.Model):
    receipt = models.ForeignKey(PurchaseRequestReceipt, on_delete=models.CASCADE, related_name='files')
    file = models.FileField(upload_to=pr_receipt_file_path)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.file: 
            filename = self.file.name.lower()
            if filename.endswith(('.jpg', '.jpeg', '.png')):
                self.file = compress_file(self.file)
        super().save(*args, **kwargs)

class PurchaseRequestItem(models.Model):
    purchase_request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name='items')
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    quantity_ordered = models.DecimalField(max_digits=12, decimal_places=2)
    quantity_received = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2) # Captured at time of order
    unit = models.CharField(max_length=50, blank=True, null=True) # Override unit for this specific order
    
    DISCOUNT_TYPE_CHOICES = [
        ('PERCENTAGE', 'Percentage'),
        ('AMOUNT', 'Fixed Amount'),
    ]
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES, default='PERCENTAGE')
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    @property
    def line_total(self):
        from decimal import Decimal
        base_price = self.quantity_ordered * self.unit_price
        if self.discount_type == 'PERCENTAGE':
            discount_amount = base_price * (self.discount_value / Decimal('100'))
        else:
            discount_amount = self.discount_value
        
        discounted_price = base_price - discount_amount
        return discounted_price * (Decimal('1') + (self.tax_rate / Decimal('100')))

    def __str__(self):
        return f"{self.purchase_request.pr_reference} - {self.item.name}"

class Inventory(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='inventory_records')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='inventory')
    current_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    min_stock_level = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    average_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    inventory_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Inventory"
        unique_together = ('item', 'department')

    def __str__(self):
        return f"{self.item.name}: {self.current_quantity}"

class StockBatch(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='stock_batches')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='stock_batches')
    procurement = models.ForeignKey('Procurement', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_batches')
    initial_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    remaining_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    batch_date = models.DateField(default=timezone.localdate, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['batch_date', 'created_at']

    def __str__(self):
        return f"{self.item.name} - Batch {self.id} ({self.remaining_quantity} @ {self.unit_price})"

class OpexEntry(models.Model):
    BILL_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('RECEIVED', 'Received'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PAID', 'Paid'),
    ]
    opex_no = models.CharField(max_length=1000, unique=True)
    # Support grouping multiple PRs into one settlement
    purchase_requests = models.ManyToManyField('PurchaseRequest', related_name='opex_entries')
    # Legacy field - will be migrated
    purchase_request = models.ForeignKey('PurchaseRequest', on_delete=models.CASCADE, related_name='legacy_opex_entries', null=True, blank=True)
    
    opex_date = models.DateField()
    opex_value = models.DecimalField(max_digits=15, decimal_places=2)
    bill_no = models.CharField(max_length=1000)
    bill_date = models.DateField()
    bill_status = models.CharField(max_length=20, choices=BILL_STATUS_CHOICES, default='PENDING')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='PENDING')
    is_locked = models.BooleanField(default=False)
    
    # Bill image for the grouped settlement
    bill_image = models.FileField(upload_to=opex_bill_path, null=True, blank=True)
    
    PURCHASE_TYPE_CHOICES = [
        ('REGULAR', 'Regular Purchase'),
        ('SPECIAL_EVENT', 'Special Event'),
    ]
    purchase_type = models.CharField(max_length=20, choices=PURCHASE_TYPE_CHOICES, default='REGULAR')
    event_name = models.CharField(max_length=255, blank=True, null=True)
    
    remarks = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.bill_image:
            filename = self.bill_image.name.lower()
            if filename.endswith(('.jpg', '.jpeg', '.png')):
                self.bill_image = compress_file(self.bill_image)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.opex_no

class Procurement(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE)
    purchase_request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, null=True, blank=True, related_name='procurements')
    receipt = models.ForeignKey(PurchaseRequestReceipt, on_delete=models.SET_NULL, null=True, blank=True, related_name='procurements')
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=50, blank=True, null=True)
    opex_entry = models.ForeignKey(OpexEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='procurements')
    
    DISCOUNT_TYPE_CHOICES = [
        ('PERCENTAGE', 'Percentage'),
        ('AMOUNT', 'Fixed Amount'),
    ]
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES, default='PERCENTAGE')
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    purchase_date = models.DateField(default=timezone.localdate, db_index=True)
    description_of_work = models.TextField(blank=True, null=True)
    
    PURCHASE_TYPE_CHOICES = [
        ('REGULAR', 'Regular Purchase'),
        ('SPECIAL_EVENT', 'Special Event'),
    ]
    purchase_type = models.CharField(max_length=20, choices=PURCHASE_TYPE_CHOICES, default='REGULAR')
    event_name = models.CharField(max_length=255, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-purchase_date', '-id']

    def __str__(self):
        return f"{self.item.name} from {self.vendor.name} on {self.purchase_date}"

class CustomSummaryConfig(models.Model):
    CONFIG_TYPES = [
        ('CATEGORY', 'Category'),
        ('ITEM', 'Item'),
        ('GROUP', 'Group'),
    ]
    config_type = models.CharField(max_length=20, choices=CONFIG_TYPES, default='CATEGORY')
    custom_name = models.CharField(max_length=100, null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='custom_summary_configs', null=True, blank=True)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='custom_item_configs', null=True, blank=True)
    display_name = models.CharField(max_length=200, null=True, blank=True)
    display_order = models.IntegerField(default=0)
    show_total_only = models.BooleanField(default=True)
    specific_items = models.ManyToManyField(Item, blank=True, related_name='custom_summary_configs')

    class Meta:
        ordering = ['display_order']

    def __str__(self):
        return f"{self.category.name} Config (Order: {self.display_order})"


@receiver(post_delete, sender=PurchaseRequest)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    """
    Deletes file from filesystem
    when corresponding PurchaseRequest object is deleted.
    """
    if instance.bill_image:
        if os.path.isfile(instance.bill_image.path):
            os.remove(instance.bill_image.path)


@receiver(pre_save, sender=PurchaseRequest)
def auto_delete_file_on_change(sender, instance, **kwargs):
    """
    Deletes old file from filesystem
    when corresponding PurchaseRequest object is updated
    with new file.
    """
    if not instance.pk:
        return False

    try:
        old_file = PurchaseRequest.objects.get(pk=instance.pk).bill_image
    except PurchaseRequest.DoesNotExist:
        return False

    new_file = instance.bill_image
    if not old_file == new_file:
        if old_file and os.path.isfile(old_file.path):
            os.remove(old_file.path)

@receiver(post_delete, sender=OpexEntry)
def opex_auto_delete_file_on_delete(sender, instance, **kwargs):
    """
    Deletes file from filesystem
    when corresponding OpexEntry object is deleted.
    """
    if instance.bill_image:
        if os.path.isfile(instance.bill_image.path):
            os.remove(instance.bill_image.path)

@receiver(pre_save, sender=OpexEntry)
def opex_auto_delete_file_on_change(sender, instance, **kwargs):
    """
    Deletes old file from filesystem
    when corresponding OpexEntry object is updated
    with new file or cleared.
    """
    if not instance.pk:
        return False

    try:
        old_file = OpexEntry.objects.get(pk=instance.pk).bill_image
    except OpexEntry.DoesNotExist:
        return False

    new_file = instance.bill_image
    if not old_file == new_file:
        if old_file and os.path.isfile(old_file.path):
            os.remove(old_file.path)

@receiver(post_delete, sender=PurchaseRequestReceiptFile)
def pr_receipt_file_auto_delete_on_delete(sender, instance, **kwargs):
    """
    Deletes file from filesystem
    when corresponding PurchaseRequestReceiptFile object is deleted.
    """
    if instance.file:
        if os.path.isfile(instance.file.path):
            os.remove(instance.file.path)

@receiver(pre_save, sender=PurchaseRequestReceiptFile)
def pr_receipt_file_auto_delete_on_change(sender, instance, **kwargs):
    """
    Deletes old file from filesystem
    when corresponding PurchaseRequestReceiptFile object is updated
    with new file.
    """
    if not instance.pk:
        return False

    try:
        old_file = PurchaseRequestReceiptFile.objects.get(pk=instance.pk).file
    except PurchaseRequestReceiptFile.DoesNotExist:
        return False

    new_file = instance.file
    if not old_file == new_file:
        if old_file and os.path.isfile(old_file.path):
            os.remove(old_file.path)

@receiver(post_delete, sender=VendorDocument)
def vendor_document_auto_delete_on_delete(sender, instance, **kwargs):
    """
    Deletes file from filesystem
    when corresponding VendorDocument object is deleted.
    """
    if instance.file:
        if os.path.isfile(instance.file.path):
            os.remove(instance.file.path)

class MaterialRequest(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('PLACED', 'Placed'),
        ('MGMT_APPROVED', 'Management Approved'),
        ('ISSUED', 'Issued'),
        ('ACCEPTED', 'Accepted'),
        ('PENDING_RECONCILE', 'Pending Store Confirmation'),
        ('REJECTED', 'Rejected'),
        ('COMPLETED', 'Completed'),
    ]
    MEAL_CHOICES = [
        ('BREAKFAST', 'Breakfast'),
        ('LUNCH', 'Lunch'),
        ('DINNER', 'Dinner'),
        ('SNACKS', 'Snacks'),
        ('DAILY', 'Daily Requirement'),
        ('SPECIAL', 'Special Events'),
    ]
    mr_number = models.CharField(max_length=100, unique=True)
    order_date = models.DateField()
    target_date = models.DateField()
    meal_type = models.CharField(max_length=20, choices=MEAL_CHOICES)
    user_count = models.IntegerField(default=0)
    actual_user_count = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    is_acknowledged = models.BooleanField(default=False)
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='material_requests')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='material_requests', null=True, blank=True)
    action_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='processed_mrs')
    action_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True, null=True)
    # Management verification fields
    mgmt_verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_mrs')
    mgmt_verified_at = models.DateTimeField(null=True, blank=True)
    mgmt_remarks = models.TextField(blank=True, null=True)
    # Kitchen acceptance fields
    kitchen_accepted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='kitchen_accepted_mrs')
    kitchen_accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.mr_number

class MaterialRequestItem(models.Model):
    material_request = models.ForeignKey(MaterialRequest, on_delete=models.CASCADE, related_name='items')
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=12, decimal_places=3) # This will be the adjusted quantity
    original_quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True) # Kitchen's original request
    quantity_issued = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    quantity_returned = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    unit = models.CharField(max_length=50) # UOM
    mgmt_adjustment_remarks = models.TextField(blank=True, null=True) # Reason for change
    consumption_unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    consumption_total_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    deficit_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0) # Tracks "Debt" for Force Issues
    deficit_estimated_unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0) # Price used at time of deficit issue

    def clean(self):
        super().clean()
        
        if getattr(self, 'material_request', None) and self.material_request.status in ['PLACED', 'MGMT_APPROVED']:
            from django.db.models import Sum
            from django.core.exceptions import ValidationError
            
            order_date = self.material_request.order_date
            if not order_date:
                return
                
            try:
                locked_item = Item.objects.select_for_update(nowait=False).get(id=self.item_id)
            except Item.DoesNotExist:
                return
            
            inwards = StockBatch.objects.filter(
                item_id=self.item_id,
                department__code='procurement',
                batch_date__lte=order_date
            ).aggregate(total=Sum('initial_quantity'))
            tot_in = float(inwards['total'] or 0)
            
            mr_outwards = MaterialRequestItem.objects.filter(
                item_id=self.item_id,
                material_request__order_date__lte=order_date,
                material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED']
            ).aggregate(total=Sum('quantity_issued'))
            tot_out = float(mr_outwards['total'] or 0)
            
            pending_qs = MaterialRequestItem.objects.filter(
                item_id=self.item_id,
                material_request__order_date__lte=order_date,
                material_request__status__in=['PLACED', 'MGMT_APPROVED']
            )
            
            if self.pk:
                pending_qs = pending_qs.exclude(pk=self.pk)
                
            pending_mrs = pending_qs.aggregate(total=Sum('quantity'))
            locked_qty = float(pending_mrs['total'] or 0)
            
            qty_requested = float(self.quantity or 0)
            historical_qty = tot_in - tot_out
            effective_stock = max(0.0, historical_qty - locked_qty)
            
            if qty_requested > effective_stock:
                raise ValidationError({
                    'quantity': f"Insufficient stock for {locked_item.name}. You requested {qty_requested} {self.unit}, but only {effective_stock} {self.unit} was available on {order_date}."
                })

class MRItemBatchAllocation(models.Model):
    mr_item = models.ForeignKey(MaterialRequestItem, on_delete=models.CASCADE, related_name='batch_allocations')
    stock_batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name='allocations')
    quantity_allocated = models.DecimalField(max_digits=12, decimal_places=3)

    def __str__(self):
        return f"Allocation for MR Item {self.mr_item.id} from Batch {self.stock_batch.id} ({self.quantity_allocated})"


class StockMovement(models.Model):
    MOVEMENT_TYPES = [
        ('STORE_INWARD', 'Store Inward (Procurement)'),
        ('STORE_TO_KITCHEN', 'Store to Kitchen'),
        ('KITCHEN_TO_STORE', 'Kitchen to Store (Return)'),
        ('WASTAGE', 'Wastage/Adjustment'),
        ('VENDOR_RETURN', 'Vendor Return'),
    ]
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='movements')
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    reference_id = models.CharField(max_length=100, blank=True, null=True) # MR or PR number
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    movement_date = models.DateTimeField(auto_now_add=True)
    remarks = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-movement_date']

    def __str__(self):
        return f"{self.type} - {self.item.name} ({self.quantity})"


class VendorReturn(models.Model):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='returns')
    purchase_request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name='vendor_returns')
    return_date = models.DateField(default=timezone.localdate, db_index=True)
    remarks = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Return for {self.purchase_request.pr_reference} - {self.return_date}"


class VendorReturnItem(models.Model):
    vendor_return = models.ForeignKey(VendorReturn, on_delete=models.CASCADE, related_name='items')
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    batch = models.ForeignKey(StockBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name='vendor_returns')
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)  # Original Unit Price
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    discount_type = models.CharField(max_length=20, choices=[('PERCENTAGE', 'Percentage'), ('FIXED', 'Fixed Amount')], default='PERCENTAGE')
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reason = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.item.name} ({self.quantity}) returned"

class PasswordResetRequest(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]
    username = models.CharField(max_length=150)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    requested_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='completed_reset_requests')
    remarks = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"Reset request for {self.username} ({self.status})"

# --- Signals for Inventory Reversion ---

@receiver(post_delete, sender=Procurement)
def revert_inventory_on_procurement_delete(sender, instance, **kwargs):
    """
    When a Procurement record is deleted, automatically subtract the 
    received stock from the store inventory.
    """
    try:
        procurement_dept = Department.objects.get(code='procurement')
        inventory, _ = Inventory.objects.get_or_create(
            item=instance.item, 
            department=procurement_dept
        )
        inventory.current_quantity -= instance.quantity
        inventory.inventory_value -= (instance.total_price or Decimal('0.00'))
        if inventory.current_quantity > 0:
            inventory.average_price = (inventory.inventory_value / inventory.current_quantity).quantize(Decimal('0.00'))
        else:
            inventory.average_price = Decimal('0.00')
            inventory.inventory_value = max(Decimal('0.00'), inventory.inventory_value)
        inventory.save()
    except Exception as e:
        # Log error or handle gracefully
        print(f"Error reverting procurement inventory: {e}")

@receiver(post_delete, sender=MaterialRequestItem)
def revert_inventory_on_mr_item_delete(sender, instance, **kwargs):
    """
    When an MR Item is deleted, if the MR was ISSUED or ACCEPTED,
    move the stock back from Kitchen to Store and restore batches.
    """
    mr = instance.material_request
    if mr.status in ['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED']:
        try:
            procurement_dept = Department.objects.get(code='procurement')
            kitchen_dept = Department.objects.get(code='kitchen')
            
            qty_issued = instance.quantity_issued or instance.quantity
            qty_returned = instance.quantity_returned or Decimal('0.00')
            cost_total = instance.consumption_total_cost or Decimal('0.00')

            # 1. Deduct from Kitchen (Only if not already deducted during reconciliation)
            if mr.status != 'COMPLETED':
                kitchen_inv, _ = Inventory.objects.get_or_create(
                    item=instance.item, 
                    department=kitchen_dept
                )
                kitchen_inv.current_quantity -= qty_issued
                kitchen_inv.inventory_value -= cost_total
                if kitchen_inv.current_quantity > 0:
                    kitchen_inv.average_price = (kitchen_inv.inventory_value / kitchen_inv.current_quantity).quantize(Decimal('0.00'))
                else:
                    kitchen_inv.average_price = Decimal('0.00')
                    kitchen_inv.inventory_value = max(Decimal('0.00'), kitchen_inv.inventory_value)
                kitchen_inv.save()
            
            # 2. Add back to Store
            # If COMPLETED, Store already got qty_returned back. We only restore the consumed part.
            qty_to_restore = qty_issued
            cost_to_restore = cost_total
            if mr.status == 'COMPLETED':
                qty_to_restore = qty_issued - qty_returned
                # Note: cost_total for COMPLETED mr_item is already the consumed cost (after reconcile -= returned_val)
                # So we use it as is.
            
            store_inv, _ = Inventory.objects.get_or_create(
                item=instance.item, 
                department=procurement_dept
            )
            store_inv.current_quantity += qty_to_restore
            store_inv.inventory_value += cost_to_restore
            if store_inv.current_quantity > 0:
                store_inv.average_price = (store_inv.inventory_value / store_inv.current_quantity).quantize(Decimal('0.00'))
            store_inv.save()

            # 3. Restore Stock Batches
            # These allocations represent what was issued but not yet returned to batches
            allocations = MRItemBatchAllocation.objects.filter(mr_item=instance)
            for alloc in allocations:
                batch = alloc.stock_batch
                batch.remaining_quantity += alloc.quantity_allocated
                batch.save()
                alloc.delete()

            # 4. Cleanup Movements
            StockMovement.objects.filter(
                item=instance.item,
                reference_id=mr.mr_number,
                type__in=['STORE_TO_KITCHEN', 'KITCHEN_TO_STORE']
            ).delete()

        except Exception as e:
            print(f"Error reverting MR inventory: {e}")

@receiver(post_delete, sender=VendorReturnItem)
def revert_inventory_on_vendor_return_item_delete(sender, instance, **kwargs):
    """
    When a Vendor Return Item is deleted, add the stock back to Store.
    """
    try:
        vr = instance.vendor_return
        procurement_dept = Department.objects.get(code='procurement')
        
        qty = instance.quantity
        
        # 1. Restore Store Inventory
        store_inv, _ = Inventory.objects.get_or_create(
            item=instance.item, 
            department=procurement_dept
        )
        
        # Calculate line total (tax inclusive) using saved data if possible
        # We'll use the return_item's own tax/discount if available, otherwise estimate.
        tax_rate = instance.tax_rate or Decimal('0.00')
        line_total = instance.line_total or Decimal('0.00') # If we have it

        if line_total == 0:
            # Estimate if not saved (fallback)
            line_total = (instance.unit_price * qty * (1 + tax_rate/100)).quantize(Decimal('0.00'))

        store_inv.current_quantity += qty
        store_inv.inventory_value += line_total
        if store_inv.current_quantity > 0:
            store_inv.average_price = (store_inv.inventory_value / store_inv.current_quantity).quantize(Decimal('0.00'))
        store_inv.save()

        # 2. Restore Stock Batches (add back to latest batch for this PR)
        batch = StockBatch.objects.filter(
            item=instance.item,
            procurement__purchase_request=vr.purchase_request
        ).last()
        if batch:
            batch.remaining_quantity += qty
            batch.save()

        # 3. Update PR Item received quantity
        pr_item = PurchaseRequestItem.objects.filter(
            purchase_request=vr.purchase_request,
            item=instance.item
        ).first()
        if pr_item:
            pr_item.quantity_received += qty
            pr_item.save()

        # 4. Cleanup Movement Log
        StockMovement.objects.filter(
            item=instance.item,
            type='VENDOR_RETURN',
            reference_id=vr.purchase_request.pr_reference,
            quantity=qty
        ).delete()

    except Exception as e:
        print(f"Error reverting vendor return inventory: {e}")



# HR & Organization Models
class OrganizationUnit(models.Model):
    name = models.CharField(max_length=255)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    type = models.CharField(max_length=50, blank=True, null=True) # e.g., Department, Sub-department
    display_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    show_counts = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class Designation(models.Model):
    name = models.CharField(max_length=255)
    department_type = models.CharField(max_length=100, blank=True, null=True)
    display_order = models.IntegerField(default=0)

    def __str__(self):
        return self.name

class DeploymentPlan(models.Model):
    organization_unit = models.ForeignKey(OrganizationUnit, on_delete=models.CASCADE, related_name='deployments')
    designation = models.ForeignKey(Designation, on_delete=models.CASCADE, related_name='deployments')
    reports_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='children_deployments')
    required_count = models.IntegerField(default=0)
    month = models.IntegerField(null=True, blank=True)
    year = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = ('organization_unit', 'designation', 'month', 'year')

    def __str__(self):
        return f"{self.designation.name} at {self.organization_unit.name} (Req: {self.required_count})"

class DeploymentStatus(models.Model):
    deployment_plan = models.OneToOneField(DeploymentPlan, on_delete=models.CASCADE, related_name='status')
    actual_count = models.IntegerField(default=0)
    updated_on = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Actual: {self.actual_count} for {self.deployment_plan}"
