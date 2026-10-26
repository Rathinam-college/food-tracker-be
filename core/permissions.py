from rest_framework import permissions

class HasRolePermission(permissions.BasePermission):
    """
    Base permission class to check for a specific role permission.
    """
    permission_flag = None

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        if request.user.is_superuser:
            return True
        
        if self.permission_flag is None:
            return True
            
        if hasattr(request.user, 'profile'):
            profile = request.user.profile
            # Check individual profile flag first (override)
            if getattr(profile, self.permission_flag, False):
                return True
            # Then check role flag
            if profile.role and getattr(profile.role, self.permission_flag, False):
                return True
        
        return False

# Specific Permission Classes
class CanPlacePO(HasRolePermission): permission_flag = 'can_place_po'
class CanConfirmReceive(HasRolePermission): permission_flag = 'can_confirm_receive'
class CanEditPO(HasRolePermission): permission_flag = 'can_edit_po'
class CanDeletePO(HasRolePermission): permission_flag = 'can_delete_po'
class CanGeneratePO(HasRolePermission): permission_flag = 'can_generate_po'
class CanUploadBill(HasRolePermission): permission_flag = 'can_upload_bill'

class CanManageItems(HasRolePermission): permission_flag = 'can_manage_items'
class CanManageCategories(HasRolePermission): permission_flag = 'can_manage_categories'
class CanManageTax(HasRolePermission): permission_flag = 'can_manage_tax'
class CanManageVendors(HasRolePermission): permission_flag = 'can_manage_vendors'
class CanManageLocations(HasRolePermission): permission_flag = 'can_manage_locations'
class CanManageCompanyProfile(HasRolePermission): permission_flag = 'can_manage_company_profile'
class CanManageSummaryConfig(HasRolePermission): permission_flag = 'can_manage_summary_config'

class CanViewReports(HasRolePermission): permission_flag = 'can_view_reports'
class CanViewOpexReport(HasRolePermission): permission_flag = 'can_view_opex_report'
class CanViewCustomMonthlySummary(HasRolePermission): permission_flag = 'can_view_custom_monthly_summary'
class CanViewPREntries(HasRolePermission): permission_flag = 'can_view_pr_entries'

class CanPlaceMR(HasRolePermission): permission_flag = 'can_place_mr'
class CanViewMR(HasRolePermission): permission_flag = 'can_view_mr'
class CanManageMR(HasRolePermission): permission_flag = 'can_manage_mr'
class CanVerifyMR(HasRolePermission): permission_flag = 'can_verify_mr'
class CanDeleteMR(HasRolePermission): permission_flag = 'can_delete_mr'
class CanViewStock(HasRolePermission): permission_flag = 'can_view_stock'

class CanApprovePRL1(HasRolePermission): permission_flag = 'can_approve_pr_l1'
class CanApprovePRL2(HasRolePermission): permission_flag = 'can_approve_pr_l2'
