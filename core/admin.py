from django.contrib import admin
from .models import OrganizationUnit, Designation, DeploymentPlan, DeploymentStatus

@admin.register(OrganizationUnit)
class OrganizationUnitAdmin(admin.ModelAdmin):
    list_display = ('name', 'type', 'parent', 'display_order', 'is_active')
    list_filter = ('type', 'is_active')
    search_fields = ('name',)

@admin.register(Designation)
class DesignationAdmin(admin.ModelAdmin):
    list_display = ('name', 'department_type', 'display_order')
    list_filter = ('department_type',)
    search_fields = ('name',)

@admin.register(DeploymentPlan)
class DeploymentPlanAdmin(admin.ModelAdmin):
    list_display = ('organization_unit', 'designation', 'required_count', 'month', 'year')
    list_filter = ('organization_unit', 'designation', 'month', 'year')
    search_fields = ('organization_unit__name', 'designation__name')

@admin.register(DeploymentStatus)
class DeploymentStatusAdmin(admin.ModelAdmin):
    list_display = ('deployment_plan', 'actual_count', 'updated_on')
    search_fields = ('deployment_plan__organization_unit__name', 'deployment_plan__designation__name')
