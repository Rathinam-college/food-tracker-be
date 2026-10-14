import os
import django

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from core.models import Department, Role


def seed_management():
    """Seed the management department and role."""
    # 1. Create Management Department
    management_dept, created = Department.objects.get_or_create(
        code='management',
        defaults={
            'name': 'Management',
            'display_name': 'Management Department',
            'description': 'Executive management for approvals, reports and analysis.',
            'logo': '💼'
        }
    )
    if created:
        print(f"Created Department: {management_dept.name}")
    else:
        print(f"Department already exists: {management_dept.name}")

    # 2. Create Management Role
    management_role, created = Role.objects.get_or_create(
        name='Management',
        defaults={
            'description': 'Role for managers with approval and reporting access.',
            # Permissions
            'can_approve_pr_l1': True,
            'can_approve_pr_l2': True,
            'can_view_reports': True,
            'can_view_pr_entries': True,
            'can_view_stock': True,
            'can_view_mr': True,
            'can_verify_mr': True,
            'can_view_consumption_report': True,
            'can_view_stock_ledger': True,
            'can_place_po': False,  # Management usually doesn't place POs themselves
            'can_confirm_receive': False,
            'can_edit_po': True,  # Allow editing for adjustments
            'can_manage_items': False,
            'can_manage_categories': False,
            'can_manage_vendors': False,
        }
    )
    if created:
        print(f"Created Role: {management_role.name}")
    else:
        # Update existing role permissions just in case
        management_role.can_approve_pr_l1 = True
        management_role.can_approve_pr_l2 = True
        management_role.can_view_reports = True
        management_role.can_view_pr_entries = True
        management_role.can_view_stock = True
        management_role.can_view_mr = True
        management_role.can_verify_mr = True
        management_role.can_view_consumption_report = True
        management_role.can_view_stock_ledger = True
        management_role.save()
        print(f"Updated Role: {management_role.name}")


if __name__ == '__main__':
    seed_management()
