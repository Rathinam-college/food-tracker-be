import os
import sys
import django

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'food_tracker_backend.settings')
django.setup()

from django.contrib.auth.models import User
from core.models import Role, Profile

def fix_mr_permissions():
    print("--- Starting MR Permission Fix ---")
    
    # 1. Update/Fix Roles
    roles_to_fix = {
        'Approver 1': {'can_verify_mr': True},
        'Approver 2': {'can_verify_mr': True},
        'Super Admin': {'can_verify_mr': True, 'can_manage_mr': True},
        'Procurement': {'can_manage_mr': True},
        'Management': {'can_verify_mr': True}
    }
    
    for role_name, perms in roles_to_fix.items():
        role, created = Role.objects.get_or_create(name=role_name)
        for perm, value in perms.items():
            setattr(role, perm, value)
        role.save()
        print(f"Role '{role_name}' updated successfully: {perms}")
        
    # 2. Update/Fix Specific Profiles
    # HOD -> can_verify_mr
    hod_user = User.objects.filter(username__iexact='hod').first()
    if hod_user:
        profile = getattr(hod_user, 'profile', None)
        if not profile:
            profile = Profile.objects.create(user=hod_user)
        profile.can_verify_mr = True
        # Ensure correct role is assigned
        role = Role.objects.filter(name='Approver 1').first()
        if role:
            profile.role = role
        profile.save()
        print("HOD profile and role updated.")
        
    # GM -> can_verify_mr
    gm_user = User.objects.filter(username__iexact='gm').first()
    if gm_user:
        profile = getattr(gm_user, 'profile', None)
        if not profile:
            profile = Profile.objects.create(user=gm_user)
        profile.can_verify_mr = True
        # Ensure correct role is assigned
        role = Role.objects.filter(name='Approver 2').first()
        if role:
            profile.role = role
        profile.save()
        print("GM profile and role updated.")
        
    # Procurement
    proc_user = User.objects.filter(username__iexact='procurement').first()
    if proc_user:
        profile = getattr(proc_user, 'profile', None)
        if not profile:
            profile = Profile.objects.create(user=proc_user)
        role = Role.objects.filter(name='Procurement').first()
        if role:
            profile.role = role
        profile.save()
        print("Procurement profile and role updated.")

    print("--- MR Permission Fix Completed ---")

if __name__ == "__main__":
    fix_mr_permissions()
