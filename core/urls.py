from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CategoryViewSet, ItemViewSet, VendorViewSet, TaxRateViewSet, 
    PurchaseRequestViewSet, PurchaseRequestItemViewSet,
    InventoryViewSet, ProcurementViewSet,
    CompanyProfileViewSet, ShippingLocationViewSet, CustomSummaryConfigViewSet,
    RoleViewSet, UserManagementViewSet, DepartmentViewSet,
    MaterialRequestViewSet, StockMovementViewSet, DatabaseViewSet,
    OpexEntryViewSet, VendorCategoryViewSet, StockBatchViewSet,
    VendorReturnViewSet,
    PurchaseRequestReceiptViewSet, PurchaseRequestReceiptFileViewSet,
    PasswordResetRequestViewSet,
    LoginView, LogoutView, UserInfoView, HealthCheckView,
    OrganizationUnitViewSet,
    DesignationViewSet, DeploymentPlanViewSet, DeploymentStatusViewSet, OrganizationUnitCRUDViewSet
)

router = DefaultRouter()
router.register(r'categories', CategoryViewSet)
router.register(r'items', ItemViewSet)
router.register(r'vendors', VendorViewSet)
router.register(r'tax-rates', TaxRateViewSet)
router.register(r'purchase-requests', PurchaseRequestViewSet)
router.register(r'pr-items', PurchaseRequestItemViewSet)
router.register(r'inventory', InventoryViewSet)
router.register(r'procurement', ProcurementViewSet)
router.register(r'company-profile', CompanyProfileViewSet)
router.register(r'shipping-locations', ShippingLocationViewSet)
router.register(r'custom-summary-config', CustomSummaryConfigViewSet)
router.register(r'roles', RoleViewSet)
router.register(r'users', UserManagementViewSet)
router.register(r'departments', DepartmentViewSet)
router.register(r'material-requests', MaterialRequestViewSet)
router.register(r'stock-movements', StockMovementViewSet)
router.register(r'opex-entries', OpexEntryViewSet)
router.register(r'vendor-categories', VendorCategoryViewSet)
router.register(r'database', DatabaseViewSet, basename='database')
router.register(r'stock-batches', StockBatchViewSet, basename='stock-batches')
router.register(r'vendor-returns', VendorReturnViewSet)
router.register(r'receipts', PurchaseRequestReceiptViewSet)
router.register(r'receipt-files', PurchaseRequestReceiptFileViewSet)
router.register(r'password-reset-requests', PasswordResetRequestViewSet)
router.register(r'organization-units', OrganizationUnitViewSet)
router.register(r'org-units-crud', OrganizationUnitCRUDViewSet, basename='org-units-crud')
router.register(r'designations', DesignationViewSet)
router.register(r'deployment-plans', DeploymentPlanViewSet)
router.register(r'deployment-statuses', DeploymentStatusViewSet)

from .audit_views import PurchaseAuditViewSet, MRAuditViewSet, AdjustmentAuditViewSet, ItemSummaryViewSet, VendorReturnsAuditViewSet
router.register(r'audit/purchases', PurchaseAuditViewSet, basename='audit-purchases')
router.register(r'audit/material-requests', MRAuditViewSet, basename='audit-mrs')
router.register(r'audit/adjustments', AdjustmentAuditViewSet, basename='audit-adjustments')
router.register(r'audit/item-summary', ItemSummaryViewSet, basename='audit-item-summary')
router.register(r'audit/vendor-returns', VendorReturnsAuditViewSet, basename='audit-vendor-returns')

urlpatterns = [
    path('api/health/', HealthCheckView.as_view(), name='health'),
    path('api/auth/login/', LoginView.as_view(), name='login'),
    path('api/auth/logout/', LogoutView.as_view(), name='logout'),
    path('api/auth/user/', UserInfoView.as_view(), name='user-info'),
    path('api/', include(router.urls)),
]
