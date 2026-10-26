from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from django.db import transaction, models
from django.db.models import Q, Sum, F
from django.utils import timezone
from django.http import HttpResponse, StreamingHttpResponse, QueryDict
import openpyxl
import json
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter
import subprocess
import os
import tempfile
import calendar
from django.conf import settings
from rest_framework import viewsets, status, permissions, serializers
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import (
    Category, Item, Vendor, TaxRate, PurchaseRequest, PurchaseRequestItem,
    Inventory, Procurement, CompanyProfile, ShippingLocation, CustomSummaryConfig,
    Department, MaterialRequest, MaterialRequestItem, StockMovement, OpexEntry,
    VendorCategory, StockBatch, MRItemBatchAllocation, VendorReturn, VendorReturnItem,
    VendorDocument, PurchaseRequestReceipt, PurchaseRequestReceiptFile,
    PasswordResetRequest
)
from .serializers import (
    CategorySerializer, ItemSerializer, VendorSerializer, TaxRateSerializer, 
    PurchaseRequestSerializer, PurchaseRequestItemSerializer,
    InventorySerializer, ProcurementSerializer,
    CompanyProfileSerializer, ShippingLocationSerializer, CustomSummaryConfigSerializer,
    RoleSerializer, UserSerializer, DepartmentSerializer,
    MaterialRequestSerializer, MaterialRequestItemSerializer,
    OpexEntrySerializer, VendorCategorySerializer, StockMovementSerializer,
    StockBatchSerializer, VendorReturnSerializer, VendorDocumentSerializer,
    PurchaseRequestReceiptSerializer, PurchaseRequestReceiptFileSerializer,
    PasswordResetRequestSerializer
)
from .permissions import (
    CanPlacePO, CanConfirmReceive, CanEditPO, CanDeletePO, CanGeneratePO, CanUploadBill,
    CanManageItems, CanManageCategories, CanManageTax, CanManageVendors, CanManageLocations,
    CanManageCompanyProfile, CanManageSummaryConfig, CanViewReports, CanViewPREntries,
    CanPlaceMR, CanViewMR, CanManageMR, CanVerifyMR, CanDeleteMR, CanViewStock,
    CanApprovePRL1, CanApprovePRL2, CanViewOpexReport, CanViewCustomMonthlySummary
)
from rest_framework.views import APIView
from rest_framework.authtoken.models import Token
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from .models import Role, Profile

class PRPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 1000

class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            permission_classes = [permissions.IsAuthenticated]
        else:
            permission_classes = [CanManageCategories]
        return [permission() for permission in permission_classes]

class ItemViewSet(viewsets.ModelViewSet):
    queryset = Item.objects.all().order_by('name')
    serializer_class = ItemSerializer
    pagination_class = PRPagination

    def get_queryset(self):
        queryset = Item.objects.all().order_by('name')
        category = self.request.query_params.get('category')
        search = self.request.query_params.get('search')

        if category:
            queryset = queryset.filter(category_id=category)
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | 
                Q(category__name__icontains=search)
            )
        return queryset

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'export_excel', 'check_availability']:
            permission_classes = [permissions.IsAuthenticated]
        else:
            permission_classes = [CanManageItems]
        return [permission() for permission in permission_classes]

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        
        as_of_date_str = request.query_params.get('as_of_date')
        if as_of_date_str:
            from datetime import datetime
            from django.db.models import Sum
            from decimal import Decimal
            try:
                as_of_date = datetime.strptime(as_of_date_str, '%Y-%m-%d').date()
                
                # Retrieve the data list from pagination or response
                data_list = response.data.get('results', response.data) if isinstance(response.data, dict) else response.data
                item_ids = [item['id'] for item in data_list]
                
                from .models import StockBatch, MaterialRequestItem
                
                # 1. Total Inwards (Purchases + Manual Additions) on or before date
                inwards = StockBatch.objects.filter(
                    item_id__in=item_ids,
                    department__code='procurement',
                    batch_date__lte=as_of_date
                ).values('item_id').annotate(total=Sum('initial_quantity'))
                inward_dict = {x['item_id']: x['total'] or Decimal('0') for x in inwards}
                
                # 2. Total Outwards (MR Issues) on or before date
                mr_outwards = MaterialRequestItem.objects.filter(
                    item_id__in=item_ids,
                    material_request__order_date__lte=as_of_date,
                    material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED']
                ).values('item_id').annotate(total=Sum('quantity_issued'))
                mr_out_dict = {x['item_id']: x['total'] or Decimal('0') for x in mr_outwards}
                
                # 3. Pending MRs (Locked Stock) on or before date (Currently Pending)
                pending_mrs = MaterialRequestItem.objects.filter(
                    item_id__in=item_ids,
                    material_request__order_date__lte=as_of_date,
                    material_request__status__in=['PLACED', 'MGMT_APPROVED']
                ).values('item_id').annotate(total=Sum('quantity'))
                pending_dict = {x['item_id']: x['total'] or Decimal('0') for x in pending_mrs}
                
                # Override the current_stock in the response
                for item_data in data_list:
                    i_id = item_data['id']
                    tot_in = float(inward_dict.get(i_id, 0))
                    tot_out = float(mr_out_dict.get(i_id, 0))
                    locked_qty = float(pending_dict.get(i_id, 0))
                    
                    historical_qty = tot_in - tot_out
                    
                    item_data['current_stock'] = max(0.0, historical_qty)
                    item_data['locked_stock'] = locked_qty
                    item_data['effective_stock'] = max(0.0, historical_qty - locked_qty)
                    
            except Exception as e:
                import traceback
                traceback.print_exc()
                pass # Fallback to live stock on error
        return response

    @action(detail=True, methods=['get'])
    def check_availability(self, request, pk=None):
        date_str = request.query_params.get('order_date') or request.query_params.get('target_date')
        if not date_str:
            return Response({"error": "order_date or target_date is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        from datetime import datetime
        from django.db.models import Sum
        from decimal import Decimal
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            item = self.get_object()
            from .models import StockBatch, MaterialRequestItem, Inventory
            
            # Historical Available Stock up to target_date
            inwards = StockBatch.objects.filter(
                item=item,
                department__code='procurement',
                batch_date__lte=target_date
            ).aggregate(total=Sum('initial_quantity'))['total'] or Decimal('0')
            
            outwards = MaterialRequestItem.objects.filter(
                item=item,
                material_request__order_date__lte=target_date,
                material_request__status__in=['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE', 'COMPLETED']
            ).aggregate(total=Sum('quantity_issued'))['total'] or Decimal('0')
            
            from .models import VendorReturnItem, StockMovement
            vendor_returns = VendorReturnItem.objects.filter(
                item=item,
                vendor_return__return_date__lte=target_date
            ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
            
            adjustments = StockMovement.objects.filter(
                item=item,
                type__in=['WASTAGE', 'BREAKAGE', 'EXPIRED', 'AUDIT'],
                movement_date__date__lte=target_date
            ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
            
            historical_stock = inwards - outwards - vendor_returns - adjustments
            
            # Pending MRs globally
            pending_mrs = MaterialRequestItem.objects.filter(
                item=item,
                material_request__status__in=['PLACED', 'MGMT_APPROVED']
            ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
            
            # Current real-time stock
            inv = Inventory.objects.filter(item=item, department__code='procurement').first()
            current_stock = inv.current_quantity if inv else Decimal('0')
            
            effective_current_stock = current_stock - pending_mrs
            effective_historical_stock = historical_stock - pending_mrs
            
            available_stock = min(effective_historical_stock, effective_current_stock)
            available_stock = max(Decimal('0'), available_stock)
            
            return Response({
                "item_id": item.id,
                "target_date": date_str,
                "inwards": inwards,
                "outwards": outwards,
                "adjustments": adjustments,
                "vendor_returns": vendor_returns,
                "historical_stock": historical_stock,
                "current_stock": current_stock,
                "pending_mrs": pending_mrs,
                "available_stock": available_stock
            })
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], url_path='export_excel')
    def export_excel(self, request):
        items = self.get_queryset()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Material Registry"
        
        # Headers
        headers = ["Item Name", "Category", "UOM", "Min Stock Level", "Current Tax %"]
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
            cell.font = Font(color='FFFFFF', bold=True)
        
        # Data
        for row_num, item in enumerate(items, 2):
            ws.cell(row=row_num, column=1, value=item.name)
            ws.cell(row=row_num, column=2, value=item.category.name if item.category else "")
            ws.cell(row=row_num, column=3, value=item.unit)
            ws.cell(row=row_num, column=4, value=float(item.min_stock_level))
            
            latest_tax = item.tax_history.first()
            tax_rate = float(latest_tax.rate_percentage) if latest_tax else 0.0
            ws.cell(row=row_num, column=5, value=tax_rate)
            
        # Adjust column widths
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws.column_dimensions[column].width = max_length + 2

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=Material_Registry_{timezone.now().strftime("%Y%m%d")}.xlsx'
        wb.save(response)
        return response

    @action(detail=False, methods=['post'], url_path='bulk_delete')
    def bulk_delete(self, request):
        ids = request.data.get('ids', [])
        if not ids:
            return Response({"error": "No IDs provided"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            items = Item.objects.filter(id__in=ids)
            count = items.count()
            items.delete()
            return Response({"message": f"Successfully deleted {count} items"})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser], url_path='import_excel')
    def import_excel(self, request):
        file_obj = request.data.get('file')
        if not file_obj:
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            wb = openpyxl.load_workbook(file_obj)
            ws = wb.active
            
            created_count = 0
            updated_count = 0
            errors = []
            
            # Start from row 2 (skipping headers)
            for row in ws.iter_rows(min_row=2, values_only=True):
                # Expected: Item Name, Category, UOM, Min Stock Level, Current Tax %
                if not row or not any(row):
                    continue
                
                item_name = str(row[0]).strip() if row[0] else None
                if not item_name:
                    continue
                
                category_name = str(row[1]).strip() if row[1] else "General"
                unit = str(row[2]).strip() if row[2] else "Unit"
                
                try:
                    min_stock = Decimal(str(row[3])) if row[3] is not None else Decimal('0')
                except (InvalidOperation, ValueError, TypeError):
                    min_stock = Decimal('0')
                    
                try:
                    tax_val = Decimal(str(row[4])) if row[4] is not None else None
                except (InvalidOperation, ValueError, TypeError):
                    tax_val = None
                
                try:
                    with transaction.atomic():
                        category, _ = Category.objects.get_or_create(name=category_name)
                        item, created = Item.objects.update_or_create(
                            name=item_name,
                            defaults={
                                'category': category,
                                'unit': unit,
                                'min_stock_level': min_stock
                            }
                        )
                        
                        if created:
                            created_count += 1
                        else:
                            updated_count += 1
                            
                        # Handle tax rate if provided
                        if tax_val is not None:
                            # Update today's rate if exists, otherwise create new
                            today = timezone.now().date()
                            TaxRate.objects.update_or_create(
                                item=item,
                                effective_date=today,
                                defaults={'rate_percentage': tax_val}
                            )
                except Exception as e:
                    errors.append(f"Error in item '{item_name}': {str(e)}")
            
            return Response({
                "message": "Import completed",
                "created": created_count,
                "updated": updated_count,
                "errors": errors
            })
            
        except Exception as e:
            return Response({"error": f"Failed to parse file: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

class PurchaseRequestReceiptViewSet(viewsets.ModelViewSet):
    queryset = PurchaseRequestReceipt.objects.all()
    serializer_class = PurchaseRequestReceiptSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def upload_files(self, request, pk=None):
        receipt = self.get_object()
        files = request.FILES.getlist('files')
        
        if not files:
            return Response({"error": "No files provided"}, status=status.HTTP_400_BAD_REQUEST)

        uploaded_files = []
        for f in files:
            file_obj = PurchaseRequestReceiptFile.objects.create(
                receipt=receipt,
                file=f
            )
            uploaded_files.append(file_obj)
            
        return Response({
            "message": f"Successfully uploaded {len(uploaded_files)} files.",
            "files": PurchaseRequestReceiptFileSerializer(uploaded_files, many=True).data
        }, status=status.HTTP_201_CREATED)

class PurchaseRequestReceiptFileViewSet(viewsets.ModelViewSet):
    queryset = PurchaseRequestReceiptFile.objects.all()
    serializer_class = PurchaseRequestReceiptFileSerializer
    permission_classes = [permissions.IsAuthenticated]


class VendorCategoryViewSet(viewsets.ModelViewSet):
    queryset = VendorCategory.objects.all()
    serializer_class = VendorCategorySerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            permission_classes = [permissions.IsAuthenticated]
        else:
            permission_classes = [CanManageVendors]
        return [permission() for permission in permission_classes]


class VendorViewSet(viewsets.ModelViewSet):
    queryset = Vendor.objects.all().select_related('category')
    serializer_class = VendorSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            permission_classes = [permissions.IsAuthenticated]
        else:
            permission_classes = [CanManageVendors]
        return [permission() for permission in permission_classes]

    @action(detail=True, methods=['get'])
    def documents(self, request, pk=None):
        vendor = self.get_object()
        docs = vendor.documents.all()
        serializer = VendorDocumentSerializer(docs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def upload_document(self, request, pk=None):
        vendor = self.get_object()
        file = request.FILES.get('file')
        name = request.data.get('name')
        
        if not file or not name:
            return Response({"error": "File and name are required"}, status=status.HTTP_400_BAD_REQUEST)
            
        doc = VendorDocument.objects.create(vendor=vendor, name=name, file=file)
        return Response(VendorDocumentSerializer(doc).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'], url_path='delete_document/(?P<doc_id>[^/.]+)')
    def delete_document(self, request, pk=None, doc_id=None):
        vendor = self.get_object()
        try:
            doc = vendor.documents.get(id=doc_id)
            if doc.file:
                if os.path.exists(doc.file.path):
                    os.remove(doc.file.path)
            doc.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except VendorDocument.DoesNotExist:
            return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['get'])
    def export_excel(self, request):
        import openpyxl
        from openpyxl.styles import Font, PatternFill
        from django.http import HttpResponse

        vendors = self.get_queryset()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Vendors"

        headers = ["Vendor Name", "Category", "Contact Person", "Phone", "Email", "Address", "Brand", "Status"]
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num, value=header)
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')

        for row_num, v in enumerate(vendors, 2):
            ws.cell(row=row_num, column=1, value=v.name)
            ws.cell(row=row_num, column=2, value=v.category.name if v.category else "")
            ws.cell(row=row_num, column=3, value=v.contact_person or "")
            ws.cell(row=row_num, column=4, value=v.phone or "")
            ws.cell(row=row_num, column=5, value=v.email or "")
            ws.cell(row=row_num, column=6, value=v.address or "")
            ws.cell(row=row_num, column=7, value=v.brand or "")
            ws.cell(row=row_num, column=8, value="Active" if v.is_active else "Inactive")

        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws.column_dimensions[column].width = max_length + 2

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename=vendors_export.xlsx'
        wb.save(response)
        return response

class TaxRateViewSet(viewsets.ModelViewSet):
    queryset = TaxRate.objects.all()
    serializer_class = TaxRateSerializer
    permission_classes = [CanManageTax]

    @action(detail=False, methods=['get'])
    def export_excel(self, request):
        rates = self.get_queryset().order_by('-effective_date')
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Tax Rate History"
        
        # Headers
        headers = ["Effective Date", "Item Name", "Rate %", "Remarks"]
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
            cell.font = Font(color='FFFFFF', bold=True)
            
        # Data
        for row_num, rate in enumerate(rates, 2):
            ws.cell(row=row_num, column=1, value=rate.effective_date)
            ws.cell(row=row_num, column=2, value=rate.item.name if rate.item else "")
            ws.cell(row=row_num, column=3, value=float(rate.rate_percentage))
            ws.cell(row=row_num, column=4, value=rate.remarks)

        # Adjust column widths
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws.column_dimensions[column].width = max_length + 2

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=Tax_Rate_History_{timezone.now().strftime("%Y%m%d")}.xlsx'
        wb.save(response)
        return response


class CompanyProfileViewSet(viewsets.ModelViewSet):
    queryset = CompanyProfile.objects.all()
    serializer_class = CompanyProfileSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            permission_classes = [permissions.IsAuthenticated]
        else:
            permission_classes = [CanManageCompanyProfile]
        return [permission() for permission in permission_classes]


class ShippingLocationViewSet(viewsets.ModelViewSet):
    queryset = ShippingLocation.objects.all()
    serializer_class = ShippingLocationSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            permission_classes = [permissions.IsAuthenticated]
        else:
            permission_classes = [CanManageLocations]
        return [permission() for permission in permission_classes]

class CustomSummaryConfigViewSet(viewsets.ModelViewSet):
    queryset = CustomSummaryConfig.objects.all().order_by('display_order')
    serializer_class = CustomSummaryConfigSerializer
    permission_classes = [CanManageSummaryConfig]


class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [permissions.IsAuthenticated]


class RoleViewSet(viewsets.ModelViewSet):
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    # Only superusers or those with specific management permissions should manage roles
    # For now, let's say only staff can manage roles
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

class UserManagementViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    @action(detail=True, methods=['post'])
    def set_role(self, request, pk=None):
        user = self.get_object()
        role_id = request.data.get('role_id')
        if not role_id:
            # Allow clearing role
            if hasattr(user, 'profile'):
                user.profile.role = None
                user.profile.save()
            return Response({"status": "Role cleared"})
        try:
            role = Role.objects.get(id=role_id)
            profile, created = Profile.objects.get_or_create(user=user)
            profile.role = role
            profile.save()
            return Response({"status": "Role assigned successfully"})
        except Role.DoesNotExist:
            return Response({"error": "Role not found"}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'])
    def set_departments(self, request, pk=None):
        user = self.get_object()
        department_ids = request.data.get('department_ids', [])
        profile, _ = Profile.objects.get_or_create(user=user)
        profile.departments.set(department_ids)
        return Response({"status": "Departments updated successfully"})

    @action(detail=False, methods=['post'])
    def create_user(self, request):
        username = request.data.get('username', '').strip()
        password = request.data.get('password', '').strip()
        first_name = request.data.get('first_name', '').strip()
        last_name = request.data.get('last_name', '').strip()
        email = request.data.get('email', '').strip()
        role_id = request.data.get('role_id')

        if not username or not password:
            return Response(
                {"error": "Username and password are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if User.objects.filter(username=username).exists():
            return Response(
                {"error": "A user with that username already exists."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            email=email,
        )
        profile, _ = Profile.objects.get_or_create(user=user)
        profile.plain_password = password
        if role_id:
            try:
                role = Role.objects.get(id=role_id)
                profile.role = role
            except Role.DoesNotExist:
                pass
        
        landing_page = request.data.get('landing_page')
        if landing_page:
            profile.landing_page = landing_page
        
        department_ids = request.data.get('department_ids', [])
        if department_ids:
            profile.departments.set(department_ids)
            
        auto_approve = request.data.get('auto_approve')
        auto_approve_threshold = request.data.get('auto_approve_threshold')
        can_verify_mr = request.data.get('can_verify_mr')
        if auto_approve is not None:
            profile.auto_approve = auto_approve
        if auto_approve_threshold is not None:
            profile.auto_approve_threshold = auto_approve_threshold
        if can_verify_mr is not None:
            profile.can_verify_mr = can_verify_mr
            
        show_switch_dept = request.data.get('show_switch_dept')
        if show_switch_dept is not None:
            profile.show_switch_dept = show_switch_dept
            
        profile.save()

        return Response(
            UserSerializer(user).data,
            status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=['post'])
    def set_password(self, request, pk=None):
        user = self.get_object()
        new_password = request.data.get('password', '').strip()
        if not new_password:
            return Response(
                {"error": "Password is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        user.set_password(new_password)
        user.save()
        profile, _ = Profile.objects.get_or_create(user=user)
        profile.plain_password = new_password
        profile.save()
        return Response({"status": "Password updated successfully"})

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        
        # Extract profile related fields if they exist
        role_id = request.data.get('role_id')
        department_ids = request.data.get('department_ids')
        landing_page = request.data.get('landing_page')
        
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        
        # Update profile if needed
        profile, _ = Profile.objects.get_or_create(user=instance)
        if role_id is not None:
            if role_id == "" or role_id is None:
                profile.role = None
            else:
                try:
                    role = Role.objects.get(id=role_id)
                    profile.role = role
                except Role.DoesNotExist:
                    pass
        
        if department_ids is not None:
            # department_ids could be a list of IDs
            profile.departments.set(department_ids)
        
        if landing_page is not None:
            profile.landing_page = landing_page
        
        auto_approve = request.data.get('auto_approve')
        auto_approve_threshold = request.data.get('auto_approve_threshold')
        can_verify_mr = request.data.get('can_verify_mr')
        show_switch_dept = request.data.get('show_switch_dept')
        
        if auto_approve is not None:
            profile.auto_approve = auto_approve
        if auto_approve_threshold is not None:
            profile.auto_approve_threshold = auto_approve_threshold
        if can_verify_mr is not None:
            profile.can_verify_mr = can_verify_mr
        if show_switch_dept is not None:
            profile.show_switch_dept = show_switch_dept
            
        profile.save()
        
        # Re-serialize to get updated profile info
        return Response(self.get_serializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if user.is_superuser:
            return Response(
                {"error": "Cannot delete a superuser/root admin account."}, 
                status=status.HTTP_403_FORBIDDEN
            )
        if user == request.user:
            return Response(
                {"error": "You cannot delete your own account."}, 
                status=status.HTTP_403_FORBIDDEN
            )
        return super().destroy(request, *args, **kwargs)



class HealthCheckView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response({"status": "ok"})


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]  # Must be public
    authentication_classes = []  # No authentication needed to log in

    def post(self, request):
        username = request.data.get("username")
        password = request.data.get("password")
        user = authenticate(username=username, password=password)
        if user:
            token, created = Token.objects.get_or_create(user=user)
            return Response({
                "token": token.key,
                "user": UserSerializer(user).data
            })
        return Response({"error": "Invalid Credentials"}, status=status.HTTP_400_BAD_REQUEST)

class UserInfoView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request):
        return Response(UserSerializer(request.user).data)

class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def post(self, request):
        request.user.auth_token.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)



class PurchaseRequestViewSet(viewsets.ModelViewSet):
    queryset = PurchaseRequest.objects.all().order_by('-order_date', '-pr_reference')
    serializer_class = PurchaseRequestSerializer
    pagination_class = PRPagination
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        
        from django.db.models import Sum
        from .models import Procurement
        grand_total = Procurement.objects.filter(purchase_request__in=queryset).aggregate(total=Sum('total_price'))['total'] or 0

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data['grand_total_received'] = float(grand_total)
            return response

        serializer = self.get_serializer(queryset, many=True)
        from rest_framework.response import Response
        return Response({
            'results': serializer.data,
            'grand_total_received': float(grand_total)
        })

    def get_queryset(self):
        queryset = PurchaseRequest.objects.all().order_by('-order_date', '-pr_reference')
        status_filter = self.request.query_params.get('status')
        vendor_filter = self.request.query_params.get('vendor')
        order_date = self.request.query_params.get('order_date')
        delivery_date = self.request.query_params.get('delivery_date')
        delivery_date_after = self.request.query_params.get('delivery_date_after')
        delivery_date_before = self.request.query_params.get('delivery_date_before')
        has_bill = self.request.query_params.get('has_bill')
        invoice_number = self.request.query_params.get('invoice_number')
        purchase_type = self.request.query_params.get('purchase_type')
        category_filter = self.request.query_params.get('category')
        category_filter = self.request.query_params.get('category')
        has_returnable_stock = self.request.query_params.get('has_returnable_stock')

        pr_reference = self.request.query_params.get('pr_reference')
        if status_filter:
            if status_filter == 'AWAITING_APPROVAL':
                queryset = queryset.filter(status__in=['AWAITING_APPROVAL', 'AWAITING_L1', 'AWAITING_L2'])
            else:
                queryset = queryset.filter(status=status_filter)
        if vendor_filter:
            queryset = queryset.filter(vendor_id=vendor_filter)
        if purchase_type:
            queryset = queryset.filter(purchase_type=purchase_type)
        if order_date:
            queryset = queryset.filter(order_date=order_date)
        if delivery_date:
            queryset = queryset.filter(delivery_date=delivery_date)
        if delivery_date_after:
            queryset = queryset.filter(delivery_date__gte=delivery_date_after)
        if delivery_date_before:
            queryset = queryset.filter(delivery_date__lte=delivery_date_before)
        if pr_reference:
            # Handle wildcard search
            if '*' in pr_reference:
                # Convert '*' to '.*' for regex and escape other special chars
                import re
                regex_pattern = re.escape(pr_reference).replace(r'\*', '.*')
                queryset = queryset.filter(pr_reference__iregex=f'^{regex_pattern}$')
            else:
                queryset = queryset.filter(pr_reference__icontains=pr_reference)
        if has_bill:
            if has_bill.lower() == 'true':
                queryset = queryset.exclude(bill_image='')
            elif has_bill.lower() == 'false':
                queryset = queryset.filter(bill_image='')
        if invoice_number:
            queryset = queryset.filter(receipts__invoice_number__icontains=invoice_number).distinct()
        if category_filter:
            queryset = queryset.filter(items__item__category_id=category_filter).distinct()
        if has_returnable_stock and has_returnable_stock.lower() == 'true':
            from django.db.models import Sum
            queryset = queryset.annotate(
                total_remaining=Sum('procurements__stock_batches__remaining_quantity')
            ).filter(total_remaining__gt=0).distinct()

        return queryset

    def perform_create(self, serializer):
        pr = serializer.save()
        if pr.status != 'DRAFT':
            pr.status = 'AWAITING_APPROVAL'
            pr.save()
            self._apply_stage_auto_approvals(pr, submittor=self.request.user)

    def perform_update(self, serializer):
        pr = serializer.save()
        if pr.status != 'DRAFT':
            if pr.status == 'PLACED' and (not pr.approved_by_l1 or not pr.approved_by_l2):
                pr.status = 'AWAITING_APPROVAL'
                pr.save()
            self._apply_stage_auto_approvals(pr, submittor=self.request.user)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        is_admin = request.user.is_superuser or (hasattr(request.user, 'profile') and request.user.profile.role and request.user.profile.role.name in ['Admin', 'Super Admin'])
        
        # 1. Permission and status checks for non-DRAFT edits
        updated_fields = set(request.data.keys())
        core_fields = {'pr_reference', 'vendor', 'order_date', 'delivery_date', 'status', 'items', 'ship_to'}
        has_core_field_changes = any(field in updated_fields for field in core_fields)
        is_bill_only_update = 'bill_image' in updated_fields and not has_core_field_changes

        if not is_bill_only_update and instance.status not in ['DRAFT']:
            return Response(
                {"error": f"Cannot edit a PR with status {instance.status}. Only DRAFT entries can be edited to preserve audit trails."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        if 'bill_image' in updated_fields:
            has_upload_perm = self.request.user.is_superuser or (
                hasattr(self.request.user, 'profile') and 
                self.request.user.profile.role and 
                self.request.user.profile.role.can_upload_bill
            )
            if not has_upload_perm:
                return Response(
                    {"error": "You do not have role permission to upload or delete bills."}, 
                    status=status.HTTP_403_FORBIDDEN
                )

        # 2. Inventory reversion logic for Admins
        old_status = instance.status
        new_status = request.data.get('status', old_status)
        
        if is_admin and old_status in ['RECEIVED', 'COMPLETED'] and new_status not in ['RECEIVED', 'COMPLETED']:
            try:
                with transaction.atomic():
                    self._revert_pr_entries(instance)
            except Exception as e:
                return Response(
                    {"error": f"Failed to revert inventory: {str(e)}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        return super().update(request, *args, **kwargs)



    def destroy(self, request, *args, **kwargs):
        return Response(
            {"error": "For audit compliance and tracking, Purchase Requests cannot be deleted from the database. Please reject or short-close the request instead."}, 
            status=status.HTTP_400_BAD_REQUEST
        )

    def _revert_pr_entries(self, pr):
        """
        Helper to reverse all database and filesystem effects of a PR.
        Used for Admin Force Delete and Revert Status.
        """
        # 1. Cleanup Logs (Stock Movements) - Use exact match and remarks for thorough cleanup
        StockMovement.objects.filter(
            Q(reference_id=pr.pr_reference) | 
            Q(remarks__icontains=pr.pr_reference)
        ).delete()

        # 2. Process linked Procurement records
        procurements = Procurement.objects.filter(purchase_request=pr)
        for proc in procurements:
            # A. Revert Stock Batches and Kitchen Allocations
            batches = StockBatch.objects.filter(procurement=proc)
            for batch in batches:
                allocations = MRItemBatchAllocation.objects.filter(stock_batch=batch)
                for alloc in allocations:
                    # 1. Move quantity back to "Deficit" for the kitchen MR item
                    mr_item = alloc.mr_item
                    mr_item.quantity_issued -= alloc.quantity_allocated
                    mr_item.deficit_quantity += alloc.quantity_allocated
                    
                    # 2. Recalculate MR item cost using specific allocation price
                    allocation_cost = (alloc.quantity_allocated * batch.unit_price).quantize(Decimal('0.00'))
                    mr_item.consumption_total_cost -= allocation_cost
                    
                    if mr_item.quantity_issued > 0:
                        mr_item.consumption_unit_cost = (mr_item.consumption_total_cost / mr_item.quantity_issued).quantize(Decimal('0.00'))
                    else:
                        mr_item.consumption_total_cost = Decimal('0.00')
                        mr_item.consumption_unit_cost = Decimal('0.00')
                    
                    mr_item.save()

                    # 3. CRITICAL: Revert Inventory levels (Store and Kitchen)
                    try:
                        kitchen_dept = Department.objects.get(code='kitchen')
                        procurement_dept = Department.objects.get(code='procurement')
                        
                        # A. Deduct from Kitchen
                        kitchen_inv, _ = Inventory.objects.get_or_create(
                            item=mr_item.item,
                            department=kitchen_dept
                        )
                        kitchen_inv.current_quantity -= alloc.quantity_allocated
                        kitchen_inv.inventory_value -= allocation_cost
                        if kitchen_inv.current_quantity > 0:
                            kitchen_inv.average_price = (kitchen_inv.inventory_value / kitchen_inv.current_quantity).quantize(Decimal('0.00'))
                        else:
                            kitchen_inv.average_price = Decimal('0.00')
                            kitchen_inv.inventory_value = max(Decimal('0.00'), kitchen_inv.inventory_value)
                        kitchen_inv.save()

                        # B. Restore to Store (concepts: un-issue)
                        store_inv, _ = Inventory.objects.get_or_create(
                            item=mr_item.item,
                            department=procurement_dept
                        )
                        store_inv.current_quantity += alloc.quantity_allocated
                        store_inv.inventory_value += allocation_cost
                        if store_inv.current_quantity > 0:
                            store_inv.average_price = (store_inv.inventory_value / store_inv.current_quantity).quantize(Decimal('0.00'))
                        store_inv.save()

                    except Exception as e:
                        print(f"Error reverting inventories during PR delete: {e}")

                    # 4. Cleanup Store-to-Kitchen movement for this specific allocation
                    StockMovement.objects.filter(
                        item=mr_item.item,
                        type='STORE_TO_KITCHEN',
                        reference_id=mr_item.material_request.mr_number,
                        quantity=alloc.quantity_allocated
                    ).delete()
                    
                    alloc.delete()
                
                batch.delete()

            # B. Delete Procurement record 
            # Note: The post_delete signal on Procurement handles Inventory quantity/value reversion automatically.
            # This avoids the double-subtraction bug.
            proc.delete()

        # 3. Cleanup Files and Receipts
        receipts = PurchaseRequestReceipt.objects.filter(purchase_request=pr)
        for receipt in receipts:
            files = PurchaseRequestReceiptFile.objects.filter(receipt=receipt)
            for f in files:
                if f.file and os.path.isfile(f.file.path):
                    try:
                        os.remove(f.file.path)
                    except Exception:
                        pass
                f.delete()
            receipt.delete()
        
        # Reset item received quantities
        pr.items.all().update(quantity_received=0)


    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'next_pr_number', 'settlable']:
            self.permission_classes = [CanViewPREntries]
        elif self.action == 'create':
            self.permission_classes = [CanPlacePO]
        elif self.action in ['update', 'partial_update', 'revert_status']:
            self.permission_classes = [CanEditPO]
        elif self.action == 'destroy':
            self.permission_classes = [CanDeletePO]
        elif self.action == 'receive':
            self.permission_classes = [CanConfirmReceive]
        elif self.action == 'submit_for_approval':
            self.permission_classes = [CanPlacePO]
        elif self.action == 'approve_l1':
            self.permission_classes = [CanApprovePRL1]
        elif self.action == 'approve_l2':
            self.permission_classes = [CanApprovePRL2]
        elif self.action == 'reject_approval':
            self.permission_classes = [permissions.IsAuthenticated]
        else:
            self.permission_classes = [permissions.IsAuthenticated]
        return super().get_permissions()

    @action(detail=False, methods=['get'])
    def settlable(self, request):
        vendor_id = request.query_params.get('vendor')
        include_opex_id = request.query_params.get('include_opex_id')
        
        # PRs that are in appropriate status and NOT yet linked to any OpexEntry
        # OR are linked to the specific OPEX entry we are currently editing
        from django.db.models import Q
        query = Q(opex_entries__isnull=True)
        if include_opex_id:
            query |= Q(opex_entries__id=include_opex_id)
            
        queryset = PurchaseRequest.objects.filter(
            query,
            status__in=['PLACED', 'RECEIVED', 'COMPLETED']
        )
        
        if vendor_id:
            queryset = queryset.filter(vendor_id=vendor_id)
        
        serializer = self.get_serializer(queryset.distinct(), many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def next_pr_number(self, request):
        order_date_str = request.query_params.get('order_date')
        if order_date_str:
            try:
                # Expected format from frontend: YYYY-MM-DD
                date_obj = timezone.datetime.strptime(order_date_str, '%Y-%m-%d')
                formatted_date = date_obj.strftime('%Y%m%d')
            except (ValueError, TypeError):
                formatted_date = timezone.now().strftime('%Y%m%d')
        else:
            formatted_date = timezone.now().strftime('%Y%m%d')
            
        prefix = f"PR-{formatted_date}-"
        
        # Get all PR references for today and find the max sequence
        last_pr = PurchaseRequest.objects.filter(
            pr_reference__startswith=prefix
        ).order_by('-pr_reference').first()
        
        if last_pr:
            try:
                # Extract the last 4 digits and increment
                last_seq = int(last_pr.pr_reference.split('-')[-1])
                new_seq = last_seq + 1
            except (ValueError, IndexError):
                new_seq = 1
        else:
            new_seq = 1
            
        next_ref = f"{prefix}{str(new_seq).zfill(4)}"
        return Response({"next_pr_number": next_ref})

    @action(detail=True, methods=['post'])
    def receive(self, request, pk=None):
        # Handle both JSON and FormData
        data = request.data
        if isinstance(data, QueryDict):
            items_json = data.get('items')
            try:
                items_data = json.loads(items_json) if items_json else []
            except json.JSONDecodeError:
                items_data = []
            invoice_number = data.get('invoice_number')
            purchase_date = data.get('purchase_date')
            short_close_val = data.get('short_close', False)
            purchase_type = data.get('purchase_type', 'REGULAR')
            event_name = data.get('event_name')
        else:
            items_data = data.get('items', [])
            invoice_number = data.get('invoice_number')
            purchase_date = data.get('purchase_date')
            short_close_val = data.get('short_close', False)
            purchase_type = data.get('purchase_type', 'REGULAR')
            event_name = data.get('event_name')
        
        # Robust boolean parsing
        if isinstance(short_close_val, str):
            short_close = short_close_val.lower() == 'true'
        else:
            short_close = bool(short_close_val)
        
        # Date parsing
        if isinstance(purchase_date, str) and purchase_date:
            try:
                purchase_date = datetime.strptime(purchase_date, '%Y-%m-%d').date()
            except ValueError:
                purchase_date = timezone.now().date()
        elif not purchase_date:
            purchase_date = timezone.now().date()

        bill_files = request.FILES.getlist('files')

        try:
            with transaction.atomic():
                purchase_request = PurchaseRequest.objects.select_for_update().get(pk=pk)

                if purchase_request.status in ['COMPLETED', 'CANCELLED']:
                    return Response(
                        {"error": f"Cannot receive items for a request with status {purchase_request.status}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                    
                if purchase_request.order_date and purchase_date < purchase_request.order_date:
                    return Response(
                        {"error": f"Receive date ({purchase_date}) cannot be before the order date ({purchase_request.order_date})."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Create a Receipt record if invoice or files are provided
                receipt = None
                if invoice_number or bill_files:
                    receipt = PurchaseRequestReceipt.objects.create(
                        purchase_request=purchase_request,
                        invoice_number=invoice_number,
                        purchase_date=purchase_date if purchase_date else timezone.now().date()
                    )
                    
                    # Save files
                    for f in bill_files:
                        PurchaseRequestReceiptFile.objects.create(receipt=receipt, file=f)

                total_received_amount = 0
                for item_data in items_data:
                    item_id = item_data.get('item_id')
                    try:
                        received_qty = Decimal(str(item_data.get('received_qty', 0)))
                        received_price = Decimal(str(item_data.get('received_price', 0)))
                    except (InvalidOperation, TypeError):
                        continue
                    
                    if received_qty <= 0:
                        continue

                    pr_item = purchase_request.items.get(item_id=item_id)
                    item = pr_item.item

                    # 1. Calculate costs for this line
                    discount_type = item_data.get('discount_type', pr_item.discount_type)
                    discount_value = Decimal(str(item_data.get('discount_value', pr_item.discount_value)))
                    
                    # Manual tax rate from receipt entry, fallback to pr_item.tax_rate
                    manual_tax_rate = item_data.get('tax_rate')
                    if manual_tax_rate is not None:
                        tax_rate = Decimal(str(manual_tax_rate))
                    else:
                        tax_rate = pr_item.tax_rate

                    base_price = received_price * received_qty
                    if discount_type == 'PERCENTAGE':
                        discount_amount = base_price * (discount_value / Decimal('100'))
                    else:
                        discount_amount = discount_value
                    
                    discounted_price = base_price - discount_amount
                    line_tax_factor = Decimal('1') + (tax_rate / Decimal('100'))
                    line_total = (discounted_price * line_tax_factor).quantize(Decimal('0.00'))

                    # 2. Update Inventory (Target Procurement Department)
                    procurement_dept = Department.objects.get(code='procurement')
                    inventory, created = Inventory.objects.get_or_create(
                        item=item, 
                        department=procurement_dept
                    )
                    
                    inventory.current_quantity += received_qty
                    inventory.inventory_value += line_total
                    if inventory.current_quantity > 0:
                        inventory.average_price = (inventory.inventory_value / inventory.current_quantity).quantize(Decimal('0.00'))
                    inventory.save()

                    # 2a. Create a StockBatch record for FIFO tracking
                    batch_unit_price = (line_total / received_qty).quantize(Decimal('0.0000'))
                    
                    # Calculate if there's a negative stock "debt" to satisfy from this new batch
                    # inventory.current_quantity has already been updated with += received_qty
                    old_qty = inventory.current_quantity - received_qty
                    total_debt = abs(min(Decimal('0'), old_qty))
                    # We can only satisfy debt up to what we just received in this batch
                    debt = min(total_debt, received_qty)
                    batch_remaining = received_qty - debt

                    # 2a. Create the StockBatch record first
                    batch = StockBatch.objects.create(
                        item=item,
                        department=procurement_dept,
                        initial_quantity=received_qty,
                        remaining_quantity=received_qty, # Will be adjusted if debt exists
                        unit_price=batch_unit_price,
                        batch_date=purchase_date if purchase_date else (purchase_request.delivery_date or timezone.now().date()),
                    )

                    # --- SATISFY DEBT (Retroactive Pricing & Allocation Update) ---
                    if debt > 0:
                        remaining_debt = debt
                        # Find Material Request items with debt (FIFO order)
                        debt_items = MaterialRequestItem.objects.filter(
                            item=item, 
                            deficit_quantity__gt=0
                        ).order_by('material_request__created_at')
                        
                        for mr_item in debt_items:
                            if remaining_debt <= 0:
                                break
                            
                            to_fill = min(remaining_debt, mr_item.deficit_quantity)
                            
                            # 1. Price adjustment: (actual price - estimate used) * quantity filled
                            price_diff = (to_fill * (batch_unit_price - mr_item.deficit_estimated_unit_cost)).quantize(Decimal('0.00'))
                            
                            mr_item.consumption_total_cost += price_diff
                            # Recalculate average unit cost for the whole MR item
                            if mr_item.quantity_issued > 0:
                                mr_item.consumption_unit_cost = (mr_item.consumption_total_cost / mr_item.quantity_issued).quantize(Decimal('0.00'))
                            
                            mr_item.deficit_quantity -= to_fill
                            mr_item.save()
                            
                            # 2. Create the visual link in the Ledger (Allocation)
                            MRItemBatchAllocation.objects.create(
                                mr_item=mr_item,
                                stock_batch=batch,
                                quantity_allocated=to_fill
                            )
                            
                            # 3. Adjust Batch and Inventory values
                            batch.remaining_quantity -= to_fill
                            inventory.inventory_value -= price_diff
                            remaining_debt -= to_fill
                        
                        batch.save()
                        
                        # Recalculate Store average price after adjustments
                        if inventory.current_quantity > 0:
                            inventory.average_price = (inventory.inventory_value / inventory.current_quantity).quantize(Decimal('0.00'))
                        inventory.save()

                    procurement_record = Procurement.objects.create(
                        item=item,
                        vendor=purchase_request.vendor,
                        purchase_request=purchase_request,
                        receipt=receipt,
                        quantity=received_qty,
                        unit_price=received_price,
                        tax_rate=tax_rate,
                        discount_type=discount_type,
                        discount_value=discount_value,
                        total_price=line_total,
                        unit=pr_item.unit,
                        purchase_date=purchase_date if purchase_date else (purchase_request.delivery_date or timezone.now().date()),
                        purchase_type=purchase_type,
                        event_name=event_name,
                        description_of_work=f"Received against PR: {purchase_request.pr_reference}"
                    )
                    
                    # 3a. Link to OpexEntry if PR is already associated with one
                    # We link to the most recent unlocked OPEX entry if multiple exist
                    opex_entry = purchase_request.opex_entries.filter(is_locked=False).order_by('-created_at').first()
                    if opex_entry:
                        procurement_record.opex_entry = opex_entry
                        procurement_record.save()
                    
                    # Link procurement record to the batch we created earlier
                    batch.procurement = procurement_record
                    batch.save()
                    
                    # Update quantity received
                    pr_item.quantity_received += received_qty
                    pr_item.save()


                    # 4. Log Movement
                    StockMovement.objects.create(
                        item=item,
                        quantity=received_qty,
                        type='STORE_INWARD',
                        reference_id=purchase_request.pr_reference,
                        user=request.user,
                        remarks=f"Received via PR: {purchase_request.pr_reference}"
                    )

                    total_received_amount += line_total

                # Update PR status
                if short_close:
                    purchase_request.status = 'COMPLETED'
                else:
                    all_fulfilled = True
                    for item in purchase_request.items.all():
                        if item.quantity_received < item.quantity_ordered:
                            all_fulfilled = False
                            break
                    purchase_request.status = 'COMPLETED' if all_fulfilled else 'RECEIVED'
                
                purchase_request.save()

            return Response({"status": "Purchase received successfully"}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def short_close(self, request, pk=None):
        purchase_request = self.get_object()
        
        if purchase_request.status not in ['PLACED', 'RECEIVED']:
            return Response(
                {"error": f"Cannot short-close a request with status {purchase_request.status}. Only PLACED or RECEIVED orders can be closed."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            from django.utils import timezone
            purchase_request.status = 'COMPLETED'
            purchase_request.closed_by = request.user
            purchase_request.closed_at = timezone.now()
            purchase_request.save()
            return Response({"status": "Purchase Request short-closed successfully"}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def revert_status(self, request, pk=None):
        return Response(
            {"error": "For audit compliance and tracking, Purchase Requests that have been received cannot be reverted. Please use the Vendor Return or Inventory Adjustment modules to make corrections."},
            status=status.HTTP_400_BAD_REQUEST
        )

    def _apply_stage_auto_approvals(self, pr, submittor=None):
        """
        Helper to automatically check if there are designated auto-approvers
        or if the submittor has auto-approval enabled for their permissions.
        """
        changed = False

        # Step 1: Check L1 Auto-Approval
        if not pr.approved_by_l1:
            # Case A: Submittor has auto-approval
            if submittor and hasattr(submittor, 'profile') and submittor.profile.auto_approve:
                threshold = submittor.profile.auto_approve_threshold
                if threshold == 0 or pr.total_amount <= threshold:
                    # Superuser or has L1 permission in role
                    has_l1_perm = submittor.is_superuser or (
                        submittor.profile.role and submittor.profile.role.can_approve_pr_l1
                    )
                    if has_l1_perm:
                        pr.approved_by_l1 = submittor
                        pr.approved_at_l1 = timezone.now()
                        changed = True

            # Case B: Global/designated L1 auto-approver
            if not pr.approved_by_l1:
                # Find profiles with auto_approve and (superuser or L1 permission)
                l1_approvers = Profile.objects.filter(
                    auto_approve=True
                ).filter(
                    Q(auto_approve_threshold=0) | Q(auto_approve_threshold__gte=pr.total_amount)
                ).filter(
                    Q(user__is_superuser=True) | Q(role__can_approve_pr_l1=True)
                ).select_related('user').distinct()

                l1_approver = l1_approvers.first()
                if l1_approver:
                    pr.approved_by_l1 = l1_approver.user
                    pr.approved_at_l1 = timezone.now()
                    changed = True

        # Step 2: Check L2 Auto-Approval
        if not pr.approved_by_l2:
            # Case A: Submittor has auto-approval
            if submittor and hasattr(submittor, 'profile') and submittor.profile.auto_approve:
                threshold = submittor.profile.auto_approve_threshold
                if threshold == 0 or pr.total_amount <= threshold:
                    # Superuser or has L2 permission in role
                    has_l2_perm = submittor.is_superuser or (
                        submittor.profile.role and submittor.profile.role.can_approve_pr_l2
                    )
                    if has_l2_perm:
                        # Skip if submittor already approved L1 (unless superuser)
                        if pr.approved_by_l1 != submittor or submittor.is_superuser:
                            pr.approved_by_l2 = submittor
                            pr.approved_at_l2 = timezone.now()
                            changed = True

            # Case B: Global/designated L2 auto-approver
            if not pr.approved_by_l2:
                l2_approvers = Profile.objects.filter(
                    auto_approve=True
                ).filter(
                    Q(auto_approve_threshold=0) | Q(auto_approve_threshold__gte=pr.total_amount)
                ).filter(
                    Q(user__is_superuser=True) | Q(role__can_approve_pr_l2=True)
                ).select_related('user').distinct()

                l2_approver = l2_approvers.first()
                if l2_approver:
                    # Ensure L2 is different from L1 unless it's a superuser
                    if pr.approved_by_l1 != l2_approver.user or l2_approver.user.is_superuser:
                        pr.approved_by_l2 = l2_approver.user
                        pr.approved_at_l2 = timezone.now()
                        changed = True
        
        if changed:
            # Re-evaluate status based on what approvals we now have
            if pr.approved_by_l1 and pr.approved_by_l2:
                pr.status = 'PLACED'
            elif pr.approved_by_l1 or pr.approved_by_l2:
                # If only one is approved, it stays in AWAITING_APPROVAL or moves to it
                pr.status = 'AWAITING_APPROVAL'
            pr.save()
        
        return changed

    @action(detail=True, methods=['post'])
    def submit_for_approval(self, request, pk=None):
        pr = self.get_object()
        if pr.status != 'DRAFT':
            return Response({"error": "Only drafts can be submitted for approval"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Transition to main approval status
        pr.status = 'AWAITING_APPROVAL'
        pr.save()

        # Apply auto-approvals (including the submittor if they have auto-approve ON)
        self._apply_stage_auto_approvals(pr, submittor=request.user)

        if pr.status == 'PLACED':
            return Response({"status": "Auto-approved at all levels. PR has been PLACED."})
        
        return Response({"status": "Awaiting necessary approvals"})

    @action(detail=True, methods=['post'])
    def approve_l1(self, request, pk=None):
        pr = self.get_object()
        if pr.status not in ['AWAITING_APPROVAL', 'AWAITING_L1', 'AWAITING_L2']:
            return Response({"error": "PR is not in an approval stage"}, status=status.HTTP_400_BAD_REQUEST)
        
        if pr.approved_by_l1:
            return Response({"error": "PR is already approved for Level 1"}, status=status.HTTP_400_BAD_REQUEST)
            
        pr.approved_by_l1 = request.user
        pr.approved_at_l1 = timezone.now()
        
        if pr.approved_by_l1 and pr.approved_by_l2:
            pr.status = 'PLACED'
        else:
            pr.status = 'AWAITING_APPROVAL'
            
        pr.save()
        self._apply_stage_auto_approvals(pr)

        if pr.status == 'PLACED':
            return Response({"status": "Level 1 Approved. PR has been PLACED."})

        return Response({"status": "Level 1 Approved. Still awaiting other approvals."})

    @action(detail=True, methods=['post'])
    def approve_l2(self, request, pk=None):
        pr = self.get_object()
        if pr.status not in ['AWAITING_APPROVAL', 'AWAITING_L1', 'AWAITING_L2']:
            return Response({"error": "PR is not in an approval stage"}, status=status.HTTP_400_BAD_REQUEST)
        
        if pr.approved_by_l2:
            return Response({"error": "PR is already approved for Level 2"}, status=status.HTTP_400_BAD_REQUEST)
        
        if pr.approved_by_l1 == request.user and not request.user.is_superuser:
            return Response({"error": "Level 2 approver must be different from Level 1 approver"}, status=status.HTTP_400_BAD_REQUEST)

        pr.approved_by_l2 = request.user
        pr.approved_at_l2 = timezone.now()
        
        if pr.approved_by_l1 and pr.approved_by_l2:
            pr.status = 'PLACED'
        else:
            pr.status = 'AWAITING_APPROVAL'
            
        pr.save()
        self._apply_stage_auto_approvals(pr)

        if pr.status == 'PLACED':
            return Response({"status": "Level 2 Approved. PR has been PLACED."})
            
        return Response({"status": "Level 2 Approved. Still awaiting other approvals."})

    @action(detail=True, methods=['post'])
    def reject_approval(self, request, pk=None):
        pr = self.get_object()
        if pr.status not in ['AWAITING_L1', 'AWAITING_L2', 'AWAITING_APPROVAL']:
            return Response({"error": "PR is not in an approval stage"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Check permissions
        is_l1_approver = hasattr(request.user, 'profile') and request.user.profile.role.can_approve_pr_l1
        is_l2_approver = hasattr(request.user, 'profile') and request.user.profile.role.can_approve_pr_l2
        
        if pr.status == 'AWAITING_L1' and not (is_l1_approver or request.user.is_superuser):
            return Response({"error": "Unauthorized to reject L1 approval"}, status=status.HTTP_401_UNAUTHORIZED)
        if pr.status == 'AWAITING_L2' and not (is_l2_approver or request.user.is_superuser):
            return Response({"error": "Unauthorized to reject L2 approval"}, status=status.HTTP_401_UNAUTHORIZED)

        pr.status = 'DRAFT'
        pr.approved_by_l1 = None
        pr.approved_at_l1 = None
        pr.save()
        return Response({"status": "Rejected back to Draft"})

    @action(detail=False, methods=['get'])
    def export_excel(self, request):
        queryset = self.get_queryset()
        
        # Create workbook and sheet
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Purchase Requests"
        
        # Define headers
        headers = [
            'Order Date', 'PR Reference', 'Invoice No', 'Vendor', 'Type', 'Event', 'Category', 'Item', 
            'Qty Ordered', 'Qty Received', 'Unit', 
            'Approved Unit Price (Base)', 'Approved Tax %', 'Approved Discount', 'Approved Line Total',
            'Received Unit Price (Base)', 'Received Tax %', 'Received Tax Amt (Per Unit)', 'Received Unit Price (w/ Tax)', 'Actual Received Total', 
            'Status', 'Target Delivery'
        ]
        
        # Style headers
        header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        alignment = Alignment(horizontal='center', vertical='center')
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = alignment
            
        # Add data
        row_num = 2
        for pr in queryset:
            for item in pr.items.all():
                discount_display = f"{item.discount_value}%" if item.discount_type == 'PERCENTAGE' else f"₹{item.discount_value}"
                
                # Calculate actual received details from Procurement records
                from django.db.models import Sum, Avg
                from .models import Procurement
                from decimal import Decimal
                
                proc_stats = Procurement.objects.filter(
                    purchase_request=pr, 
                    item=item.item
                ).aggregate(
                    total=Sum('total_price'),
                    avg_unit_price=Avg('unit_price'),
                    avg_tax_rate=Avg('tax_rate')
                )
                
                actual_received_total = proc_stats['total'] or Decimal('0')
                received_unit_price = proc_stats['avg_unit_price'] or Decimal('0')
                received_tax_rate = proc_stats['avg_tax_rate'] or Decimal('0')
                received_tax_amount = received_unit_price * (received_tax_rate / Decimal('100'))
                received_unit_price_with_tax = received_unit_price + received_tax_amount
                
                invoice_nos = ", ".join(filter(None, pr.receipts.values_list('invoice_number', flat=True).distinct()))

                ws.cell(row=row_num, column=1, value=pr.order_date)
                ws.cell(row=row_num, column=2, value=pr.pr_reference)
                ws.cell(row=row_num, column=3, value=invoice_nos)
                ws.cell(row=row_num, column=4, value=pr.vendor.name)
                ws.cell(row=row_num, column=5, value=pr.get_purchase_type_display())
                ws.cell(row=row_num, column=6, value=pr.event_name or "")
                ws.cell(row=row_num, column=7, value=item.item.category.name if item.item.category else "")
                ws.cell(row=row_num, column=8, value=item.item.name)
                ws.cell(row=row_num, column=9, value=float(item.quantity_ordered))
                ws.cell(row=row_num, column=10, value=float(item.quantity_received))
                ws.cell(row=row_num, column=11, value=item.unit or item.item.unit)
                ws.cell(row=row_num, column=12, value=float(item.unit_price))
                ws.cell(row=row_num, column=13, value=float(item.tax_rate))
                ws.cell(row=row_num, column=14, value=discount_display)
                ws.cell(row=row_num, column=15, value=float(item.line_total))
                ws.cell(row=row_num, column=16, value=float(received_unit_price))
                ws.cell(row=row_num, column=17, value=float(received_tax_rate))
                ws.cell(row=row_num, column=18, value=float(received_tax_amount))
                ws.cell(row=row_num, column=19, value=float(received_unit_price_with_tax))
                ws.cell(row=row_num, column=20, value=float(actual_received_total))
                ws.cell(row=row_num, column=21, value=pr.status)
                ws.cell(row=row_num, column=22, value=pr.delivery_date)
                row_num += 1
                
        # Auto-adjust column widths
        for col_idx, col in enumerate(ws.columns, 1):
            max_length = 0
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            for cell in col:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws.column_dimensions[column_letter].width = max_length + 2

        # Prepare response
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename=PR_Entries_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        
        wb.save(response)
        return response

    @action(detail=False, methods=['get'])
    def download_bulk_template(self, request):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Bulk PR Template"
        
        headers = [
            'Item Name', 'Quantity', 'UOM', 'Unit Price', 
            'Discount Value', 'Discount Type (PERCENTAGE/AMOUNT)'
        ]
        
        # Style headers
        header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        alignment = Alignment(horizontal='center', vertical='center')
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = alignment
            
        # Pre-fill with items from the registry
        items = Item.objects.all().order_by('name')
        for row_num, item in enumerate(items, 2):
            ws.cell(row=row_num, column=1, value=item.name)
            ws.cell(row=row_num, column=2, value=0)
            ws.cell(row=row_num, column=3, value=item.unit)
            ws.cell(row=row_num, column=4, value=0)
            ws.cell(row=row_num, column=5, value=0)
            ws.cell(row=row_num, column=6, value='PERCENTAGE')

        # Auto-adjust column widths
        for col_idx, col in enumerate(ws.columns, 1):
            max_length = 0
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            for cell in col:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws.column_dimensions[column_letter].width = max_length + 2

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = 'attachment; filename=pr_bulk_template.xlsx'
        wb.save(response)
        return response

    @action(detail=False, methods=['post'])
    def parse_bulk_excel(self, request):
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            wb = openpyxl.load_workbook(file_obj, data_only=True)
            ws = wb.active
            
            parsed_items = []
            skipped_items = []
            
            # Assuming headers are in row 1
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not row[0]: # Skip rows with no item name
                    continue
                    
                item_name = str(row[0]).strip()
                try:
                    quantity = float(row[1] or 0)
                except (ValueError, TypeError):
                    quantity = 0
                
                uom = str(row[2] or "").strip()
                
                try:
                    unit_price = float(row[3] or 0)
                except (ValueError, TypeError):
                    unit_price = 0
                    
                try:
                    discount_value = float(row[4] or 0)
                except (ValueError, TypeError):
                    discount_value = 0
                    
                discount_type = str(row[5] or 'PERCENTAGE').strip().upper()
                
                if 'AMOUNT' in discount_type:
                    discount_type = 'AMOUNT'
                else:
                    discount_type = 'PERCENTAGE'
                
                # Basic validation: skip rows where nothing is specified
                if quantity == 0 and unit_price == 0 and discount_value == 0:
                    continue

                item = Item.objects.filter(name__iexact=item_name).first()
                if item:
                    latest_tax = item.tax_history.first()
                    tax_rate = float(latest_tax.rate_percentage) if latest_tax else 0
                    
                    parsed_items.append({
                        "id": item.id,
                        "name": item.name,
                        "category_name": item.category.name if item.category else "",
                        "quantity": quantity,
                        "unit": uom or item.unit,
                        "unit_price": unit_price,
                        "discount_type": discount_type,
                        "discount_value": discount_value,
                        "current_tax_rate": tax_rate
                    })
                else:
                    skipped_items.append(item_name)
                    
            return Response({
                "items": parsed_items,
                "skipped": skipped_items
            })
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


    @action(detail=False, methods=['get'])
    def deviation_report(self, request):
        """
        Compare approved PR prices vs actual received prices to surface
        vendor price deviations. Also links OPEX settlement for full
        3-way reconciliation: Approved → Received → Billed.
        """
        from .models import Procurement, OpexEntry

        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        vendor_id = request.query_params.get('vendor')
        pr_ref = request.query_params.get('pr_reference')
        opex_no = request.query_params.get('opex_no')
        deviation_type = request.query_params.get('deviation_type', 'ALL')

        # Only PRs that have been received (Procurement records exist)
        prs = PurchaseRequest.objects.filter(
            status__in=['COMPLETED', 'RECEIVED']
        ).prefetch_related('items', 'procurements', 'opex_entries')

        if start_date:
            prs = prs.filter(procurements__purchase_date__gte=start_date).distinct()
        if end_date:
            prs = prs.filter(procurements__purchase_date__lte=end_date).distinct()
        if vendor_id:
            prs = prs.filter(vendor_id=vendor_id)
        if pr_ref:
            prs = prs.filter(pr_reference__icontains=pr_ref)
        if opex_no:
            prs = prs.filter(opex_entries__opex_no__icontains=opex_no).distinct()

        results = []
        total_overcharge = Decimal('0.00')
        total_undercharge = Decimal('0.00')
        prs_with_deviation = 0

        for pr in prs.order_by('-order_date'):
            # Collect Procurement records keyed by item_id
            procurements_by_item = {}
            receipt_date = None
            for proc in pr.procurements.all():
                procurements_by_item.setdefault(proc.item_id, []).append(proc)
                if receipt_date is None or proc.purchase_date > receipt_date:
                    receipt_date = proc.purchase_date

            # Link OPEX entries (M2M)
            opex_entries = list(pr.opex_entries.all())
            opex_no = ', '.join(e.opex_no for e in opex_entries) if opex_entries else None
            opex_value = sum(
                e.opex_value for e in opex_entries
            ) if opex_entries else Decimal('0.00')

            pr_approved_total = Decimal('0.00')
            pr_received_total = Decimal('0.00')
            item_rows = []

            for pr_item in pr.items.all():
                item = pr_item.item

                # --- Approved side (Tax-inclusive for business reconciliation) ---
                # approved_line_total (property) includes tax and discount for the full ordered qty
                if pr_item.quantity_ordered > 0:
                    approved_unit_price_incl = Decimal(str(pr_item.line_total)) / pr_item.quantity_ordered
                else:
                    approved_unit_price_incl = Decimal('0.00')

                # --- Received side (sum all Procurement records for this item) ---
                procs = procurements_by_item.get(item.id, [])
                received_qty = sum(p.quantity for p in procs)
                received_line_total_incl = sum(p.total_price for p in procs)

                if received_qty > 0:
                    received_unit_price_incl = (
                        received_line_total_incl / received_qty
                    ).quantize(Decimal('0.00'))
                else:
                    received_unit_price_incl = Decimal('0.00')

                # --- Deviation Analysis (Inclusive of Tax) ---
                expected_total_for_received = (approved_unit_price_incl * received_qty).quantize(Decimal('0.00'))
                
                deviation_amount = (
                    received_line_total_incl - expected_total_for_received
                ).quantize(Decimal('0.00'))
                
                deviation_pct = (
                    (deviation_amount / expected_total_for_received * Decimal('100')).quantize(
                        Decimal('0.01')
                    ) if expected_total_for_received != 0 else Decimal('0.00')
                )
                

                pr_approved_total += expected_total_for_received
                pr_received_total += received_line_total_incl

                item_rows.append({
                    'item_id': item.id,
                    'item_name': item.name,
                    'unit': pr_item.unit or item.unit,
                    'ordered_qty': str(pr_item.quantity_ordered),
                    'approved_unit_price': str(approved_unit_price_incl),
                    'approved_line_total': str(expected_total_for_received),
                    'received_qty': str(received_qty),
                    'received_unit_price': str(received_unit_price_incl),
                    'received_line_total': str(received_line_total_incl),
                    'deviation_per_unit': str(received_unit_price_incl - approved_unit_price_incl),
                    'deviation_amount': str(deviation_amount),
                    'deviation_pct': str(deviation_pct),
                    'tax_rate': str(pr_item.tax_rate),
                    'discount_type': pr_item.discount_type,
                    'discount_value': str(pr_item.discount_value),
                })

            pr_deviation = (
                pr_received_total - pr_approved_total
            ).quantize(Decimal('0.00'))
            pr_deviation_pct = (
                (pr_deviation / pr_approved_total * Decimal('100')).quantize(
                    Decimal('0.01')
                ) if pr_approved_total != 0 else Decimal('0.00')
            )

            # Filter by deviation type
            if deviation_type == 'OVER' and pr_deviation <= 0:
                continue
            if deviation_type == 'UNDER' and pr_deviation >= 0:
                continue
            if deviation_type == 'NONE' and pr_deviation != 0:
                continue

            if pr_deviation > 0:
                total_overcharge += pr_deviation
                prs_with_deviation += 1
            elif pr_deviation < 0:
                total_undercharge += abs(pr_deviation)
                prs_with_deviation += 1

            results.append({
                'pr_reference': pr.pr_reference,
                'pr_id': pr.id,
                'vendor': pr.vendor.name,
                'vendor_id': pr.vendor_id,
                'order_date': str(pr.order_date),
                'receipt_date': str(receipt_date) if receipt_date else None,
                'approved_total': str(pr_approved_total),
                'received_total': str(pr_received_total),
                'deviation_amount': str(pr_deviation),
                'deviation_pct': str(pr_deviation_pct),
                'opex_no': opex_no,
                'opex_value': str(opex_value),
                'items': item_rows,
            })

        # Pagination
        try:
            page = int(request.query_params.get('page', 1))
            page_size = request.query_params.get('page_size', '20')
            
            if page_size.lower() == 'all':
                paginated_results = results
                total_pages = 1
            else:
                page_size = int(page_size)
                start_idx = (page - 1) * page_size
                end_idx = start_idx + page_size
                paginated_results = results[start_idx:end_idx]
                total_pages = (len(results) + page_size - 1) // page_size
        except (ValueError, TypeError):
            paginated_results = results
            page = 1
            total_pages = 1

        return Response({
            'summary': {
                'total_prs': len(results),
                'prs_with_deviation': prs_with_deviation,
                'total_overcharge': str(total_overcharge),
                'total_undercharge': str(total_undercharge),
            },
            'results': paginated_results,
            'pagination': {
                'count': len(results),
                'page': page,
                'total_pages': total_pages,
                'has_next': page < total_pages,
                'has_previous': page > 1
            }
        })


class OpexEntryViewSet(viewsets.ModelViewSet):
    queryset = OpexEntry.objects.all().order_by('-opex_date')
    serializer_class = OpexEntrySerializer
    pagination_class = PRPagination

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        if hasattr(data, 'getlist'):
            # FormData sends multiple values for the same key. QueryDict.get() only gets the last one.
            # We need to explicitly get the list and set it back.
            pr_ids = data.getlist('purchase_requests')
            if pr_ids:
                data.setlist('purchase_requests', pr_ids)
        
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.get('partial', False)
        instance = self.get_object()
        data = request.data.copy()
        if hasattr(data, 'getlist'):
            pr_ids = data.getlist('purchase_requests')
            if pr_ids:
                data.setlist('purchase_requests', pr_ids)
        
        # Explicitly handle image clearing if flag exists
        if data.get('clear_bill_image') == 'true':
            instance.bill_image.delete(save=False)
            instance.bill_image = None
            instance.save()
        
        serializer = self.get_serializer(instance, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    def get_queryset(self):
        queryset = OpexEntry.objects.all().order_by('-opex_date')
        opex_no = self.request.query_params.get('opex_no')
        payment_status = self.request.query_params.get('payment_status')
        date_after = self.request.query_params.get('date_after')
        date_before = self.request.query_params.get('date_before')
        
        if opex_no:
            queryset = queryset.filter(opex_no__icontains=opex_no)
        if payment_status:
            queryset = queryset.filter(payment_status=payment_status)
        if date_after:
            queryset = queryset.filter(opex_date__gte=date_after)
        if date_before:
            queryset = queryset.filter(opex_date__lte=date_before)
            
        return queryset

    def get_permissions(self):
        # Specific reports
        if self.action in ['opex_report', 'export_opex_report_excel']:
            self.permission_classes = [CanViewOpexReport]
        # Re-use CanViewPREntries or CanPlacePO for now
        elif self.action in ['list', 'retrieve']:
            self.permission_classes = [CanViewPREntries]
        else:
            self.permission_classes = [CanPlacePO]
        return super().get_permissions()

    def perform_create(self, serializer):
        # Create the OPEX Entry
        opex_entry = serializer.save()
        
        # Auto-lock if payment status is PAID
        if opex_entry.payment_status == 'PAID':
            opex_entry.is_locked = True
            opex_entry.save()
            
        # Link all unlinked procurement records for ALL associated PRs
        prs = opex_entry.purchase_requests.all()
        for pr in prs:
            pr.procurements.filter(opex_entry__isnull=True).update(opex_entry=opex_entry)

    def perform_update(self, serializer):
        # Get original set of PRs
        old_instance = self.get_object()
        
        # Check if already locked (prevent updates to locked entries unless it's just unlocking)
        # However, the frontend will hide edit buttons. 
        # For API security, we should check it here too.
        if old_instance.is_locked and not self.request.user.is_superuser:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("This entry is locked and cannot be modified.")

        old_prs = set(old_instance.purchase_requests.all())
        
        # Save updated entry
        opex_entry = serializer.save()
        
        # Auto-lock if payment status becomes PAID
        if opex_entry.payment_status == 'PAID' and not opex_entry.is_locked:
            opex_entry.is_locked = True
            opex_entry.save()

        new_prs = set(opex_entry.purchase_requests.all())
        
        # Handle re-linking of procurements
        removed_prs = old_prs - new_prs
        added_prs = new_prs - old_prs
        
        for pr in removed_prs:
            pr.procurements.filter(opex_entry=opex_entry).update(opex_entry=None)
        
        for pr in added_prs:
            pr.procurements.filter(opex_entry__isnull=True).update(opex_entry=opex_entry)

    def perform_destroy(self, instance):
        if instance.is_locked and not self.request.user.is_superuser:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("This entry is locked and cannot be deleted.")
        instance.delete()

    @action(detail=True, methods=['post'])
    def toggle_lock(self, request, pk=None):
        if not request.user.is_superuser:
            return Response({"error": "Only administrators can lock/unlock entries."}, status=403)
        
        instance = self.get_object()
        instance.is_locked = not instance.is_locked
        instance.save()
        return Response({"status": "success", "is_locked": instance.is_locked})

    @action(detail=False, methods=['get'])
    def export_excel(self, request):
        queryset = self.get_queryset()

        # Create workbook and sheet
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "OPEX Entries"
        
        # Define headers
        headers = [
            'OPEX NO', 'Date', 'Type', 'Event', 'PR Reference(s)', 'Vendor', 
            'Bill No', 'Bill Date', 'Value', 
            'Bill Status', 'Payment Status', 'Remarks'
        ]
        
        # Style headers
        header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        alignment = Alignment(horizontal='center', vertical='center')
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = alignment
            
        # Add data
        row_num = 2
        for entry in queryset:
            prs = entry.purchase_requests.all()
            pr_refs = ", ".join([p.pr_reference for p in prs])
            vendor_name = prs.first().vendor.name if prs.exists() else "N/A"
            
            ws.cell(row=row_num, column=1, value=entry.opex_no)
            ws.cell(row=row_num, column=2, value=entry.opex_date)
            ws.cell(row=row_num, column=3, value=entry.purchase_type)
            ws.cell(row=row_num, column=4, value=entry.event_name or "-")
            ws.cell(row=row_num, column=5, value=pr_refs)
            ws.cell(row=row_num, column=6, value=vendor_name)
            ws.cell(row=row_num, column=7, value=entry.bill_no)
            ws.cell(row=row_num, column=8, value=entry.bill_date)
            ws.cell(row=row_num, column=9, value=float(entry.opex_value))
            ws.cell(row=row_num, column=10, value=entry.bill_status)
            ws.cell(row=row_num, column=11, value=entry.payment_status)
            ws.cell(row=row_num, column=12, value=entry.remarks or "")
            row_num += 1
            
        # Auto-adjust column widths
        for col_idx, col in enumerate(ws.columns, 1):
            max_length = 0
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            for cell in col:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws.column_dimensions[column_letter].width = max_length + 2

        # Prepare response
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename=OPEX_Entries_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        
        wb.save(response)
        return response

    @action(detail=False, methods=['get'])
    def opex_report(self, request):
        queryset = OpexEntry.objects.all().select_related().prefetch_related(
            'purchase_requests',
            'purchase_requests__vendor',
            'purchase_requests__items__item'
        )

        item_id = request.query_params.get('item')
        category_id = request.query_params.get('vendor_category')
        vendor_id = request.query_params.get('vendor')
        payment_status = request.query_params.get('payment_status')
        opex_no = request.query_params.get('opex_no')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        purchase_type = request.query_params.get('purchase_type')
        event_name = request.query_params.get('event_name')

        if purchase_type:
            queryset = queryset.filter(purchase_type=purchase_type)
        if event_name:
            queryset = queryset.filter(event_name__icontains=event_name)

        if item_id:
            queryset = queryset.filter(purchase_requests__items__item_id=item_id)
        if category_id:
            queryset = queryset.filter(purchase_requests__vendor__category_id=category_id)
        if vendor_id:
            queryset = queryset.filter(purchase_requests__vendor_id=vendor_id)
        if payment_status:
            queryset = queryset.filter(payment_status=payment_status)
        if opex_no:
            queryset = queryset.filter(opex_no__icontains=opex_no)
        if start_date:
            queryset = queryset.filter(opex_date__gte=start_date)
        if end_date:
            queryset = queryset.filter(opex_date__lte=end_date)

        full_results = []
        for entry in queryset.distinct():
            prs = entry.purchase_requests.all()
            total_tax = Decimal('0')
            base_value = Decimal('0')
            total_pr_amount = Decimal('0')
            pr_refs = []
            pr_dates = []
            vendor_name = "N/A"

            for pr in prs:
                pr_refs.append(pr.pr_reference)
                pr_dates.append(pr.created_at.strftime('%Y-%m-%d'))
                vendor_name = pr.vendor.name
                total_pr_amount += pr.total_amount
                
                for item in pr.items.all():
                    b_val = item.quantity_ordered * item.unit_price
                    if item.discount_type == 'PERCENTAGE':
                        disc = b_val * (item.discount_value / Decimal('100'))
                    else:
                        disc = item.discount_value
                    
                    d_price = b_val - disc
                    tax_amt = d_price * (item.tax_rate / Decimal('100'))
                    
                    base_value += d_price
                    total_tax += tax_amt

            full_results.append({
                'id': entry.id,
                'pr_no': ", ".join(pr_refs),
                'pr_date': ", ".join(pr_dates),
                'vendor_name': vendor_name,
                'pr_value': float(base_value),
                'bill_no': entry.bill_no,
                'bill_date': entry.bill_date,
                'tax': float(total_tax),
                'total_amount': float(total_pr_amount),
                'bill_status': entry.bill_status,
                'remarks': entry.remarks,
                'opex_no': entry.opex_no,
                'opex_date': entry.opex_date,
                'opex_value': float(entry.opex_value),
                'payment_status': entry.payment_status,
                'purchase_type': entry.purchase_type,
                'event_name': entry.event_name,
                'is_locked': entry.is_locked
            })

        # Calculate Summary
        summary = {
            'total_pr_value': sum(r['pr_value'] for r in full_results),
            'total_tax': sum(r['tax'] for r in full_results),
            'total_amount': sum(r['total_amount'] for r in full_results),
            'total_opex_value': sum(r['opex_value'] for r in full_results),
        }

        # Pagination
        try:
            page = int(request.query_params.get('page', 1))
            page_size = request.query_params.get('page_size', '20')
            
            if str(page_size).lower() == 'all':
                paginated_results = full_results
                total_pages = 1
            else:
                page_size = int(page_size)
                start_idx = (page - 1) * page_size
                end_idx = start_idx + page_size
                paginated_results = full_results[start_idx:end_idx]
                total_pages = (len(full_results) + page_size - 1) // page_size
        except (ValueError, TypeError):
            paginated_results = full_results
            page = 1
            total_pages = 1

        return Response({
            'count': len(full_results),
            'results': paginated_results,
            'total_pages': total_pages,
            'next': page < total_pages,
            'previous': page > 1,
            'summary': summary
        })

    @action(detail=False, methods=['get'])
    def export_opex_report_excel(self, request):
        queryset = OpexEntry.objects.all().select_related().prefetch_related(
            'purchase_requests', 
            'purchase_requests__vendor',
            'purchase_requests__vendor__category',
            'purchase_requests__items__item'
        )

        item_id = request.query_params.get('item')
        category_id = request.query_params.get('vendor_category')
        vendor_id = request.query_params.get('vendor')
        payment_status = request.query_params.get('payment_status')
        opex_no = request.query_params.get('opex_no')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        purchase_type = request.query_params.get('purchase_type')
        event_name = request.query_params.get('event_name')

        if purchase_type:
            queryset = queryset.filter(purchase_type=purchase_type)
        if event_name:
            queryset = queryset.filter(event_name__icontains=event_name)

        if item_id:
            queryset = queryset.filter(purchase_requests__items__item_id=item_id)
        if category_id:
            queryset = queryset.filter(purchase_requests__vendor__category_id=category_id)
        if vendor_id:
            queryset = queryset.filter(purchase_requests__vendor_id=vendor_id)
        if payment_status:
            queryset = queryset.filter(payment_status=payment_status)
        if opex_no:
            queryset = queryset.filter(opex_no__icontains=opex_no)
        if start_date:
            queryset = queryset.filter(opex_date__gte=start_date)
        if end_date:
            queryset = queryset.filter(opex_date__lte=end_date)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Opex Report"

        headers = [
            'Sl. No', 'PR No(s)', 'Vendor Name', 'PR Value (Base)',
            'Bill No', 'Bill Date', 'Tax Amount', 'Total Amount',
            'Bill Status', 'Type', 'Event', 'OPEX No', 'OPEX Date', 'OPEX Value', 'Payment Status', 'Lock Status', 'Remarks'
        ]

        header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        center_align = Alignment(horizontal='center', vertical='center')
        currency_format = '₹ #,##0.00'

        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_align

        row_num = 2
        grand_total_base = Decimal('0')
        grand_total_tax = Decimal('0')
        grand_total_pr = Decimal('0')
        grand_total_opex = Decimal('0')

        for entry in queryset.distinct():
            prs = entry.purchase_requests.all()
            total_tax = Decimal('0')
            base_value = Decimal('0')
            total_pr_amount = Decimal('0')
            pr_refs = [p.pr_reference for p in prs]
            vendor_name = prs.first().vendor.name if prs.exists() else "N/A"
            
            for pr in prs:
                total_pr_amount += pr.total_amount
                for item in pr.items.all():
                    b_val = item.quantity_ordered * item.unit_price
                    if item.discount_type == 'PERCENTAGE':
                        disc = b_val * (item.discount_value / Decimal('100'))
                    else:
                        disc = item.discount_value
                    
                    d_price = b_val - disc
                    tax_amt = d_price * (item.tax_rate / Decimal('100'))
                    
                    base_value += d_price
                    total_tax += tax_amt

            ws.cell(row=row_num, column=1, value=row_num - 1)
            ws.cell(row=row_num, column=2, value=", ".join(pr_refs))
            ws.cell(row=row_num, column=3, value=vendor_name)
            
            cell_pr_val = ws.cell(row=row_num, column=4, value=float(base_value))
            cell_pr_val.number_format = currency_format
            
            ws.cell(row=row_num, column=5, value=entry.bill_no)
            ws.cell(row=row_num, column=6, value=entry.bill_date)
            
            cell_tax = ws.cell(row=row_num, column=7, value=float(total_tax))
            cell_tax.number_format = currency_format
            
            cell_total = ws.cell(row=row_num, column=8, value=float(total_pr_amount))
            cell_total.number_format = currency_format
            
            ws.cell(row=row_num, column=9, value=entry.bill_status)
            ws.cell(row=row_num, column=10, value=entry.purchase_type)
            ws.cell(row=row_num, column=11, value=entry.event_name or "-")
            ws.cell(row=row_num, column=12, value=entry.opex_no)
            ws.cell(row=row_num, column=13, value=entry.opex_date)
            
            cell_opex_val = ws.cell(row=row_num, column=14, value=float(entry.opex_value))
            cell_opex_val.number_format = currency_format
            
            ws.cell(row=row_num, column=15, value=entry.payment_status)
            ws.cell(row=row_num, column=16, value="Locked" if entry.is_locked else "Open")
            ws.cell(row=row_num, column=17, value=entry.remarks or "-")

            grand_total_base += base_value
            grand_total_tax += total_tax
            grand_total_pr += total_pr_amount
            grand_total_opex += entry.opex_value
            row_num += 1

        total_fill = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
        total_font = Font(bold=True)
        final_total_font = Font(bold=True, color='FF0000')

        ws.cell(row=row_num, column=1, value="GRAND TOTAL")
        ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=3)
        
        cells_to_style = [
            (4, float(grand_total_base), total_font),
            (7, float(grand_total_tax), total_font),
            (8, float(grand_total_pr), total_font),
            (12, float(grand_total_opex), final_total_font)
        ]

        for col, val, font in cells_to_style:
            cell = ws.cell(row=row_num, column=col, value=val)
            cell.font = font
            cell.number_format = currency_format
            cell.fill = total_fill
            cell.alignment = center_align

        for col_idx, col in enumerate(ws.columns, 1):
            max_length = 0
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            for cell in col:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except: pass
            ws.column_dimensions[column_letter].width = max_length + 2

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=Opex_Report_{timezone.now().strftime("%Y%m%d")}.xlsx'
        wb.save(response)
        return response

class PurchaseRequestItemViewSet(viewsets.ModelViewSet):

    queryset = PurchaseRequestItem.objects.all()
    serializer_class = PurchaseRequestItemSerializer

    @action(detail=False, methods=['get'], url_path='price_trend')
    def price_trend(self, request):
        """
        Returns the last 5 purchase prices for each item across all received PRs.
        Used for the Management dashboard price trend widget.
        Supports ?category=<id> filter.
        """
        category_id = request.query_params.get('category')

        # Base queryset: only received/completed PRs with a valid date
        base_qs = PurchaseRequestItem.objects.select_related(
            'item', 'item__category', 'purchase_request'
        ).filter(
            purchase_request__status__in=['RECEIVED', 'COMPLETED']
        ).order_by('item__name', '-purchase_request__order_date')

        if category_id:
            base_qs = base_qs.filter(item__category__id=category_id)

        # Group last 5 entries per item
        item_map = {}
        for pr_item in base_qs:
            item_id = pr_item.item.id
            if item_id not in item_map:
                item_map[item_id] = {
                    'item_id': item_id,
                    'item_name': pr_item.item.name,
                    'unit': pr_item.unit or pr_item.item.unit,
                    'category': pr_item.item.category.name if pr_item.item.category else '',
                    'prices': []
                }
            if len(item_map[item_id]['prices']) < 5:
                item_map[item_id]['prices'].append({
                    'date': str(pr_item.purchase_request.order_date),
                    'price': float(pr_item.unit_price),
                    'quantity': float(pr_item.quantity_ordered),
                })

        result = sorted(item_map.values(), key=lambda x: x['item_name'])
        return Response(result)

class InventoryViewSet(viewsets.ModelViewSet):
    queryset = Inventory.objects.all().order_by('item__name')
    serializer_class = InventorySerializer
    pagination_class = PRPagination

    def get_queryset(self):
        queryset = Inventory.objects.all().order_by('item__name')
        category = self.request.query_params.get('category')
        search = self.request.query_params.get('search')
        dept = self.request.query_params.get('department_code')
        status_filter = self.request.query_params.get('status')
        sort = self.request.query_params.get('sort')

        if dept:
            queryset = queryset.filter(department__code=dept)
        if category:
            queryset = queryset.filter(item__category_id=category)
        if search:
            queryset = queryset.filter(
                Q(item__name__icontains=search) | 
                Q(item__category__name__icontains=search)
            )

        if status_filter:
            if status_filter == 'OUT_STOCK':
                queryset = queryset.filter(current_quantity__lte=0)
            elif status_filter == 'LOW_STOCK':
                queryset = queryset.filter(current_quantity__gt=0, current_quantity__lte=F('min_stock_level'))
            elif status_filter == 'IN_STOCK':
                queryset = queryset.filter(current_quantity__gt=F('min_stock_level'))

        if sort == 'stock_high':
            queryset = queryset.order_by('-current_quantity')
        elif sort == 'stock_low':
            queryset = queryset.order_by('current_quantity')
        elif sort == 'value_high':
            queryset = queryset.order_by('-inventory_value')
        
        return queryset

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'export_excel', 'wastage_analytics', 'stock_balance_report']:
            self.permission_classes = [CanViewStock]
        else:
            self.permission_classes = [CanManageItems]
        return super().get_permissions()

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        
        from django.db.models import Sum
        total_value = queryset.aggregate(total=Sum('inventory_value'))['total'] or Decimal('0')
        
        # Calculate total tax by querying the actual active batches of these items
        from .models import StockBatch
        total_tax = Decimal('0')
        
        active_batches = StockBatch.objects.filter(
            item_id__in=queryset.values_list('item_id', flat=True),
            department_id__in=queryset.values_list('department_id', flat=True),
            remaining_quantity__gt=0
        ).select_related('procurement', 'item')

        for batch in active_batches:
            tax_rate = Decimal('0')
            if batch.procurement:
                tax_rate = batch.procurement.tax_rate
            else:
                # Fallback to tax history
                latest_tax_hist = batch.item.tax_history.first()
                tax_rate = latest_tax_hist.rate_percentage if latest_tax_hist else Decimal('0')
            
            # Batch value = remaining quantity * unit price
            batch_value = batch.remaining_quantity * batch.unit_price
            total_tax += (batch_value * tax_rate) / (100 + tax_rate)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data['total_inventory_value'] = float(total_value)
            response.data['total_tax_value'] = float(total_tax.quantize(Decimal('0.01')))
            return response

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            'results': serializer.data,
            'total_inventory_value': float(total_value),
            'total_tax_value': float(total_tax)
        })

    @action(detail=False, methods=['post'])
    def adjust_stock(self, request):
        """
        Record wastage, breakage, or manual stock adjustment.
        """
        item_id = request.data.get('item_id')
        qty = Decimal(str(request.data.get('quantity', 0)))
        adjustment_type = request.data.get('type', 'WASTAGE')
        remarks = request.data.get('remarks', '')
        batch_id = request.data.get('batch_id')
        action = request.data.get('action', 'REDUCE')  # 'REDUCE' or 'ADD'
        manual_price = request.data.get('unit_price')

        if not item_id or qty <= 0:
            return Response({"error": "Item ID and a positive quantity are required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                item = Item.objects.get(id=item_id)
                # Wastage always happens in Procurement (Store) department for now
                store_dept = Department.objects.get(code='procurement')
                inventory, _ = Inventory.objects.get_or_create(item=item, department=store_dept)

                if action == 'ADD':
                    if manual_price is not None and manual_price != '':
                        unit_price = Decimal(str(manual_price))
                    else:
                        unit_price = inventory.average_price or Decimal('0.00')
                    
                    inventory.current_quantity += qty
                    inventory.inventory_value += (qty * unit_price).quantize(Decimal('0.00'))
                    if inventory.current_quantity > 0:
                        inventory.average_price = (inventory.inventory_value / inventory.current_quantity).quantize(Decimal('0.00'))
                    inventory.save()

                    from django.utils import timezone
                    StockBatch.objects.create(
                        item=item,
                        department=store_dept,
                        initial_quantity=qty,
                        remaining_quantity=qty,
                        unit_price=unit_price,
                        batch_date=timezone.now().date()
                    )

                    StockMovement.objects.create(
                        item=item,
                        quantity=qty,
                        type=adjustment_type if adjustment_type in ['ADJUSTMENT', 'AUDIT'] else 'ADJUSTMENT',
                        user=request.user,
                        remarks=remarks or 'Manual Stock Addition'
                    )

                    return Response({"status": "SUCCESS", "message": f"Stock increased by {qty} {item.unit}."})

                # REDUCE Action
                if inventory.current_quantity < qty:
                    return Response({"error": f"Insufficient stock. Current: {inventory.current_quantity}"}, status=status.HTTP_400_BAD_REQUEST)

                if not batch_id:
                    return Response({"error": "Batch ID is strictly required for stock reductions to maintain audit tracking."}, status=status.HTTP_400_BAD_REQUEST)

                # 1. Update Batch Stock (LIFO or specific batch)
                total_value_to_reduce = Decimal('0.00')
                reduced_batches = []
                if batch_id:
                    batch = StockBatch.objects.get(id=batch_id, item=item, department=store_dept)
                    if batch.remaining_quantity < qty:
                        return Response({"error": "Insufficient quantity in selected batch."}, status=status.HTTP_400_BAD_REQUEST)
                    
                    batch.remaining_quantity -= qty
                    total_value_to_reduce = qty * batch.unit_price
                    batch.save()
                    reduced_batches.append(f"Batch {batch.id}")
                else:
                    # FIFO reduction (Spend oldest first)
                    remaining_to_reduce = qty
                    batches = StockBatch.objects.filter(item=item, department=store_dept, remaining_quantity__gt=0).order_by('batch_date', 'created_at')
                    
                    for b in batches:
                        if remaining_to_reduce <= 0:
                            break
                        
                        take = min(remaining_to_reduce, b.remaining_quantity)
                        b.remaining_quantity -= take
                        # Ensure value has standard 2 decimal places
                        total_value_to_reduce += (take * b.unit_price).quantize(Decimal('0.00'))
                        remaining_to_reduce -= take
                        b.save()
                        reduced_batches.append(f"Batch {b.id}")
                    
                    # Use a small epsilon for float/decimal comparison safety
                    if remaining_to_reduce > Decimal('0.0001'):
                        return Response({"error": f"Could not find enough batches to fulfill adjustment. Missing: {remaining_to_reduce}"}, status=status.HTTP_400_BAD_REQUEST)

                # 2. Update Inventory
                inventory.current_quantity -= qty
                
                if manual_price is not None and manual_price != '':
                    total_value_to_reduce = (qty * Decimal(str(manual_price))).quantize(Decimal('0.00'))
                
                inventory.inventory_value -= total_value_to_reduce
                if inventory.current_quantity > 0:
                    inventory.average_price = (inventory.inventory_value / inventory.current_quantity).quantize(Decimal('0.00'))
                else:
                    inventory.inventory_value = Decimal('0.00')
                    inventory.average_price = Decimal('0.00')
                inventory.save()

                # 3. Create Stock Movement
                StockMovement.objects.create(
                    item=item,
                    quantity=qty,
                    type=adjustment_type,
                    user=request.user,
                    remarks=remarks,
                    reference_id=", ".join(reduced_batches)
                )

                return Response({"status": "SUCCESS", "message": f"Stock adjusted by {qty} {item.unit}."})

        except Item.DoesNotExist:
            return Response({"error": "Item not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def check_discrepancies(self, request):
        from django.db.models import Sum
        procurement_dept = Department.objects.get(code='procurement')
        items = Item.objects.all()
        discrepancies = []

        for item in items:
            inv = Inventory.objects.filter(item=item, department=procurement_dept).first()
            if not inv:
                continue
                
            inv_qty = inv.current_quantity
            batch_qs = StockBatch.objects.filter(item=item, department=procurement_dept).aggregate(Sum('remaining_quantity'))
            batch_qty = batch_qs['remaining_quantity__sum'] or Decimal('0.00')
            
            diff = inv_qty - batch_qty
            
            if abs(diff) > Decimal('0.001'):
                discrepancies.append({
                    "item_id": item.id,
                    "item_name": item.name,
                    "inv_qty": float(inv_qty),
                    "batch_qty": float(batch_qty),
                    "diff": float(diff)
                })
                
        return Response({"discrepancies": discrepancies})

    @action(detail=False, methods=['post'])
    def fix_discrepancy(self, request):
        item_id = request.data.get('item_id')
        if not item_id:
            return Response({"error": "item_id is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            with transaction.atomic():
                from django.db.models import Sum
                from django.utils import timezone
                
                item = Item.objects.get(id=item_id)
                procurement_dept = Department.objects.get(code='procurement')
                inv = Inventory.objects.filter(item=item, department=procurement_dept).first()
                if not inv:
                    return Response({"error": "No inventory record found."}, status=status.HTTP_400_BAD_REQUEST)
                    
                inv_qty = inv.current_quantity
                batch_qs = StockBatch.objects.filter(item=item, department=procurement_dept).aggregate(Sum('remaining_quantity'))
                batch_qty = batch_qs['remaining_quantity__sum'] or Decimal('0.00')
                diff = inv_qty - batch_qty
                
                if abs(diff) <= Decimal('0.001'):
                    return Response({"status": "SUCCESS", "message": "No discrepancy found."})
                    
                if diff > 0:
                    # Missing batches: create a system adjustment batch
                    price = inv.average_price or Decimal('0.00')
                    if price == 0:
                        last_batch = StockBatch.objects.filter(item=item, department=procurement_dept).order_by('-batch_date').first()
                        if last_batch:
                            price = last_batch.unit_price
                    
                    StockBatch.objects.create(
                        item=item,
                        department=procurement_dept,
                        initial_quantity=diff,
                        remaining_quantity=diff,
                        unit_price=price,
                        batch_date=timezone.now().date()
                    )
                    msg = f"Created missing batch for {diff} {item.unit}"
                else:
                    # Ghost batches: reduce remaining quantities of existing batches (LIFO)
                    to_reduce = abs(diff)
                    batches = StockBatch.objects.filter(item=item, department=procurement_dept, remaining_quantity__gt=0).order_by('batch_date')
                    for batch in batches:
                        if to_reduce <= 0:
                            break
                        if batch.remaining_quantity >= to_reduce:
                            batch.remaining_quantity -= to_reduce
                            batch.save()
                            to_reduce = Decimal('0')
                        else:
                            to_reduce -= batch.remaining_quantity
                            batch.remaining_quantity = Decimal('0')
                            batch.save()
                    msg = f"Reduced ghost batches by {abs(diff)} {item.unit}"
                
                return Response({"status": "SUCCESS", "message": msg})
        except Item.DoesNotExist:
            return Response({"error": "Item not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
    def hard_reset(self, request):
        """
        Hard resets all inventory to 0 and clears the StockMovement ledger.
        WARNING: Highly destructive. Use only when database is in a ghostly state.
        """
        if not request.user.is_superuser:
            return Response({"error": "Unauthorized. Only superusers can perform a full system reset."}, status=status.HTTP_403_FORBIDDEN)

        try:
            with transaction.atomic():
                # 1. Clear All Transactions & History
                from .models import StockBatch, MRItemBatchAllocation, VendorReturn, VendorReturnItem, OpexEntry, VendorCategory
                VendorReturnItem.objects.all().delete()
                VendorReturn.objects.all().delete()
                MRItemBatchAllocation.objects.all().delete()
                StockBatch.objects.all().delete()
                MaterialRequest.objects.all().delete()
                PurchaseRequest.objects.all().delete()
                Procurement.objects.all().delete()
                StockMovement.objects.all().delete()
                Inventory.objects.all().delete()
                OpexEntry.objects.all().delete()
                
                # 2. Clear Master Data
                Item.objects.all().delete()
                Category.objects.all().delete()
                Vendor.objects.all().delete()
                VendorCategory.objects.all().delete()
                TaxRate.objects.all().delete()
                ShippingLocation.objects.all().delete()
                CompanyProfile.objects.all().delete()
                CustomSummaryConfig.objects.all().delete()
                
                # 3. Access Control & Users are now PRESERVED based on user request
                # Role.objects.all().delete()
                # User.objects.exclude(username='admin').delete()
                
                return Response({"status": "SUCCESS: System data reset to factory state. Inventory and history wiped. Roles and Users preserved."})
        except Exception as e:
            return Response({"error": f"Reset Failed: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def export_excel(self, request):
        queryset = self.get_queryset()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Stock Availability"
        
        headers = ["Item Name", "Category", "Current Stock", "UOM", "Min Stock Level", "Tax %", "Unit Price", "Total Value", "Status"]
        header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font
            
        for row_num, obj in enumerate(queryset, 2):
            ws.cell(row=row_num, column=1, value=obj.item.name)
            ws.cell(row=row_num, column=2, value=obj.item.category.name if obj.item.category else "")
            ws.cell(row=row_num, column=3, value=float(obj.current_quantity))
            ws.cell(row=row_num, column=4, value=obj.item.unit)
            ws.cell(row=row_num, column=5, value=float(obj.min_stock_level))
            
            # Get Active Tax Rate (Latest Procurement or fallback to Tax History)
            latest_proc = Procurement.objects.filter(item=obj.item).order_by('-purchase_date', '-id').first()
            if latest_proc:
                active_tax_rate = latest_proc.tax_rate
            else:
                latest_tax_hist = obj.item.tax_history.first()
                active_tax_rate = latest_tax_hist.rate_percentage if latest_tax_hist else 0
            
            ws.cell(row=row_num, column=6, value=float(active_tax_rate))
            ws.cell(row=row_num, column=7, value=float(obj.average_price))
            ws.cell(row=row_num, column=8, value=float(obj.inventory_value))
            
            status_text = "LOW" if obj.current_quantity <= obj.min_stock_level else "OK"
            ws.cell(row=row_num, column=9, value=status_text)
            
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=Stock_Availability_{timezone.now().strftime("%Y%m%d")}.xlsx'
        wb.save(response)
        return response

    @action(detail=False, methods=['get'])
    def stock_balance_report(self, request):
        """
        Returns a consolidated report for all items in the store, 
        using exact Procurement-based calculation logic for parity with Excel Audit.
        """
        from .models import Item, Procurement
        from django.db.models import Sum
        from decimal import Decimal

        as_of_date = request.query_params.get('as_of_date')
        item_id = request.query_params.get('item')
        
        start_date = '2026-04-30'
        
        procs_qs = Procurement.objects.filter(purchase_date__gte=start_date).order_by('item__name', 'purchase_date', 'created_at')
        if item_id:
            procs_qs = procs_qs.filter(item_id=item_id)
        if as_of_date:
            procs_qs = procs_qs.filter(purchase_date__lte=as_of_date)
            
        procs_qs = procs_qs.select_related(
            'item', 'item__category', 'purchase_request'
        ).prefetch_related(
            'stock_batches', 'stock_batches__allocations'
        )
        
        item_summaries = {}
        
        for p in procs_qs:
            item = p.item
            bought_qty = p.quantity or Decimal('0.000')
            unit_price = p.unit_price or Decimal('0.00')
            total_price = p.total_price or Decimal('0.00')
            
            remaining_in_stock = sum([batch.remaining_quantity for batch in p.stock_batches.all()]) or Decimal('0.000')
            pushed_from_this_pr = sum([alloc.quantity_allocated for batch in p.stock_batches.all() for alloc in batch.allocations.all()]) or Decimal('0.000')
            
            vendor_returned_qty = Decimal('0.000')
            if p.purchase_request:
                from .models import VendorReturnItem
                batch_ids = [batch.id for batch in p.stock_batches.all()]
                if batch_ids:
                    vendor_returned_qty = VendorReturnItem.objects.filter(
                        vendor_return__purchase_request=p.purchase_request,
                        batch_id__in=batch_ids,
                        item=item
                    ).aggregate(s=Sum('quantity'))['s'] or Decimal('0.000')
                
            adjusted_wasted = bought_qty - remaining_in_stock - pushed_from_this_pr - vendor_returned_qty
            
            avg_unit_incl_tax = total_price / bought_qty if bought_qty > 0 else Decimal('0.00')
            remaining_value = remaining_in_stock * avg_unit_incl_tax
            adjusted_value = adjusted_wasted * avg_unit_incl_tax
            pushed_value = pushed_from_this_pr * avg_unit_incl_tax
            vendor_returned_value = vendor_returned_qty * avg_unit_incl_tax
            
            if item.id not in item_summaries:
                item_summaries[item.id] = {
                    "item_id": item.id,
                    "item_name": item.name,
                    "category_name": item.category.name if item.category else "Uncategorized",
                    "unit": item.unit,
                    "purchased_qty": Decimal('0.000'),
                    "purchased_val": Decimal('0.00'),
                    "pushed_qty": Decimal('0.000'),
                    "pushed_val": Decimal('0.00'),
                    "adjusted_qty": Decimal('0.000'),
                    "adjusted_val": Decimal('0.00'),
                    "returned_qty": Decimal('0.000'),
                    "returned_val": Decimal('0.00'),
                    "remaining_qty": Decimal('0.000'),
                    "remaining_val": Decimal('0.00')
                }
                
            item_summaries[item.id]['purchased_qty'] += bought_qty
            item_summaries[item.id]['purchased_val'] += total_price
            item_summaries[item.id]['pushed_qty'] += pushed_from_this_pr
            item_summaries[item.id]['pushed_val'] += pushed_value
            item_summaries[item.id]['adjusted_qty'] += adjusted_wasted
            item_summaries[item.id]['adjusted_val'] += adjusted_value
            item_summaries[item.id]['returned_qty'] += vendor_returned_qty
            item_summaries[item.id]['returned_val'] += vendor_returned_value
            item_summaries[item.id]['remaining_qty'] += remaining_in_stock
            item_summaries[item.id]['remaining_val'] += remaining_value
            
        result = []
        for item_id, data in sorted(item_summaries.items(), key=lambda x: x[1]['item_name']):
            result.append({
                "item_id": data['item_id'],
                "item_name": data['item_name'],
                "category_name": data['category_name'],
                "unit": data['unit'],
                "purchased_qty": float(data['purchased_qty']),
                "purchased_val": float(data['purchased_val']),
                "pushed_qty": float(data['pushed_qty']),
                "pushed_val": float(data['pushed_val']),
                "adjusted_qty": float(data['adjusted_qty']),
                "adjusted_val": float(data['adjusted_val']),
                "returned_qty": float(data['returned_qty']),
                "returned_val": float(data['returned_val']),
                "remaining_qty": float(data['remaining_qty']),
                "remaining_val": float(data['remaining_val'])
            })
            
        return Response(result)

    @action(detail=False, methods=['get'])
    def wastage_analytics(self, request):
        """
        Returns wastage summary for the dashboard.
        """
        from django.db.models import Sum, F, Q
        from django.utils import timezone
        from datetime import timedelta
        import traceback
        
        try:
            now = timezone.now()
            this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_month_end = this_month_start - timedelta(seconds=1)
            last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            wastage_qs = StockMovement.objects.filter(type__in=['WASTAGE', 'BREAKAGE', 'EXPIRED'])

            # Limit to procurement department to avoid duplicate results from multiple department join
            this_month_val = wastage_qs.filter(
                movement_date__gte=this_month_start,
                item__inventory_records__department__code='procurement'
            ).aggregate(
                total=Sum(F('quantity') * F('item__inventory_records__average_price'))
            )['total'] or 0
            
            last_month_val = wastage_qs.filter(
                movement_date__gte=last_month_start, 
                movement_date__lte=last_month_end,
                item__inventory_records__department__code='procurement'
            ).aggregate(
                total=Sum(F('quantity') * F('item__inventory_records__average_price'))
            )['total'] or 0

            top_items = wastage_qs.filter(
                movement_date__gte=this_month_start,
                item__inventory_records__department__code='procurement'
            ).values(
                'item__name'
            ).annotate(
                total_qty=Sum('quantity'),
                total_value=Sum(F('quantity') * F('item__inventory_records__average_price'))
            ).order_by('-total_value')[:5]

            top_categories = wastage_qs.filter(
                movement_date__gte=this_month_start,
                item__inventory_records__department__code='procurement'
            ).values(
                'item__category__name'
            ).annotate(
                total_value=Sum(F('quantity') * F('item__inventory_records__average_price'))
            ).order_by('-total_value')[:5]

            return Response({
                'comparison': {
                    'this_month': float(this_month_val),
                    'last_month': float(last_month_val),
                },
                'top_items': [
                    {'name': x['item__name'], 'value': float(x['total_value']), 'qty': float(x['total_qty'])} for x in top_items
                ],
                'top_categories': [
                    {'name': x['item__category__name'] or 'Uncategorized', 'value': float(x['total_value'])} for x in top_categories
                ]
            })
        except Exception as e:
            return Response({
                "error": str(e),
                "traceback": traceback.format_exc()
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def export_balance_report_excel(self, request):
        """Exports the stock balance report to Excel with a summary dashboard."""
        from django.db.models import Sum
        from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
        
        response_data = self.stock_balance_report(request).data
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Stock Balance Report"

        # Calculate totals first
        totals = {
            'purchased_val': sum(float(item['purchased_val']) for item in response_data),
            'adjusted_val': sum(float(item['adjusted_val']) for item in response_data),
            'pushed_val': sum(float(item['pushed_val']) for item in response_data),
            'returned_val': sum(float(item['returned_val']) for item in response_data),
            'remaining_val': sum(float(item['remaining_val']) for item in response_data)
        }

        # Styling Definitions
        title_font = Font(size=16, bold=True, color='FFFFFF')
        card_header_font = Font(bold=True, color='FFFFFF')
        card_value_font = Font(bold=True, size=14)
        center_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
        thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

        # 1. Main Title
        ws.merge_cells('B2:K2')
        title_cell = ws['B2']
        title_cell.value = "STOCK BALANCE SUMMARY DASHBOARD"
        title_cell.font = title_font
        title_cell.alignment = center_align
        title_cell.fill = PatternFill(start_color='2C3E50', end_color='2C3E50', fill_type='solid')

        # 2. Cards Headers
        cards = [
            ("Total PR Purchase", 'purchased_val', '3498DB'),   # Blue
            ("Total Pushed to Kitchen", 'pushed_val', 'E67E22'), # Orange
            ("Total Adjusted/Wasted", 'adjusted_val', 'E74C3C'), # Red
            ("Total Returned Value", 'returned_val', '9B59B6'),  # Purple
            ("Total Value in Procurement", 'remaining_val', '2ECC71') # Green
        ]

        col_idx = 2
        for title, key, color in cards:
            ws.merge_cells(start_row=4, start_column=col_idx, end_row=4, end_column=col_idx+1)
            header_cell = ws.cell(row=4, column=col_idx, value=title)
            header_cell.font = card_header_font
            header_cell.alignment = center_align
            header_cell.fill = PatternFill(start_color=color, end_color=color, fill_type='solid')
            header_cell.border = thin_border
            ws.cell(row=4, column=col_idx+1).border = thin_border

            ws.merge_cells(start_row=5, start_column=col_idx, end_row=5, end_column=col_idx+1)
            val_cell = ws.cell(row=5, column=col_idx, value=totals[key])
            val_cell.font = card_value_font
            val_cell.alignment = center_align
            val_cell.number_format = '"₹"#,##0.00'
            val_cell.fill = PatternFill(start_color='F8F9F9', end_color='F8F9F9', fill_type='solid')
            val_cell.border = thin_border
            ws.cell(row=5, column=col_idx+1).border = thin_border
            
            col_idx += 2

        # 3. Data Table Headers
        table_start_row = 8
        headers = [
            "Item Name", "UOM", "Category", 
            "Purchased Qty (+)", "Purchased Value (₹)",
            "Adjusted/Wasted Qty (-)", "Adjusted Value (₹)",
            "Pushed to Kitchen Qty (-)", "Pushed Value (₹)",
            "Vendor Returns Qty (-)", "Returned Value (₹)", 
            "Closing Balance", "Closing Value (₹)"
        ]
        
        header_fill = PatternFill(start_color='34495E', end_color='34495E', fill_type='solid')
        table_header_font = Font(color='FFFFFF', bold=True)
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=table_start_row, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = table_header_font
            cell.alignment = center_align
            cell.border = thin_border

        # 4. Data Rows
        row_num = table_start_row + 1
        for item in response_data:
            ws.cell(row=row_num, column=1, value=item['item_name']).border = thin_border
            ws.cell(row=row_num, column=2, value=item['unit']).border = thin_border
            ws.cell(row=row_num, column=3, value=item['category_name']).border = thin_border
            
            # Purchased
            ws.cell(row=row_num, column=4, value=float(item['purchased_qty'])).border = thin_border
            ws.cell(row=row_num, column=5, value=float(item['purchased_val'])).number_format = '#,##0.00'
            ws.cell(row=row_num, column=5).border = thin_border
            
            # Adjusted
            ws.cell(row=row_num, column=6, value=float(item['adjusted_qty'])).border = thin_border
            ws.cell(row=row_num, column=7, value=float(item['adjusted_val'])).number_format = '#,##0.00'
            ws.cell(row=row_num, column=7).border = thin_border
            
            # Pushed
            ws.cell(row=row_num, column=8, value=float(item['pushed_qty'])).border = thin_border
            ws.cell(row=row_num, column=9, value=float(item['pushed_val'])).number_format = '#,##0.00'
            ws.cell(row=row_num, column=9).border = thin_border
            
            # Vendor Returns
            ws.cell(row=row_num, column=10, value=float(item['returned_qty'])).border = thin_border
            ws.cell(row=row_num, column=11, value=float(item['returned_val'])).number_format = '#,##0.00'
            ws.cell(row=row_num, column=11).border = thin_border
            
            # Remaining / Closing Balance
            balance_cell = ws.cell(row=row_num, column=12, value=float(item['remaining_qty']))
            balance_cell.font = Font(bold=True)
            balance_cell.border = thin_border
            
            value_cell = ws.cell(row=row_num, column=13, value=float(item['remaining_val']))
            value_cell.font = Font(bold=True)
            value_cell.number_format = '#,##0.00'
            value_cell.border = thin_border
            
            row_num += 1

        # Add Grand Total Row at bottom
        ws.cell(row=row_num, column=1, value="Grand Total").font = Font(bold=True)
        ws.cell(row=row_num, column=1).border = thin_border
        
        ws.cell(row=row_num, column=5, value=totals['purchased_val']).number_format = '#,##0.00'
        ws.cell(row=row_num, column=7, value=totals['adjusted_val']).number_format = '#,##0.00'
        ws.cell(row=row_num, column=9, value=totals['pushed_val']).number_format = '#,##0.00'
        ws.cell(row=row_num, column=11, value=totals['returned_val']).number_format = '#,##0.00'
        ws.cell(row=row_num, column=13, value=totals['remaining_val']).number_format = '#,##0.00'
        
        for col in range(1, 14):
            ws.cell(row=row_num, column=col).font = Font(bold=True)
            ws.cell(row=row_num, column=col).border = thin_border

        # Adjust column widths
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except: pass
            ws.column_dimensions[column].width = min(max_length + 2, 25)

        # Freeze the top 8 rows (cards and table headers)
        ws.freeze_panes = 'A9'

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="stock_balance_report.xlsx"'
        wb.save(response)
        return response

class ProcurementViewSet(viewsets.ModelViewSet):
    queryset = Procurement.objects.all().order_by('-purchase_date', '-id')
    serializer_class = ProcurementSerializer

    def get_permissions(self):
        if self.action == 'custom_monthly_summary':
            self.permission_classes = [CanViewCustomMonthlySummary]
        else:
            self.permission_classes = [CanViewReports]
        return super().get_permissions()

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        
        # Extract date filters from query params to match returns
        month = request.query_params.get('month')
        year = request.query_params.get('year')
        day = request.query_params.get('day')
        
        # Apply manual filters if provided
        if month:
            queryset = queryset.filter(purchase_date__month=month)
        if year:
            queryset = queryset.filter(purchase_date__year=year)
        if day:
            queryset = queryset.filter(purchase_date__day=day)
            
        date_str = request.query_params.get('date')
        if date_str:
            queryset = queryset.filter(purchase_date=date_str)
            
        vendor_id = request.query_params.get('vendor')
        item_id = request.query_params.get('item')
        category_id = request.query_params.get('category')
        purchase_type = request.query_params.get('purchase_type')
        event_name = request.query_params.get('event_name')

        if vendor_id:
            queryset = queryset.filter(vendor_id=vendor_id)
        if item_id:
            queryset = queryset.filter(item_id=item_id)
        if category_id:
            queryset = queryset.filter(item__category_id=category_id)
        if purchase_type:
            queryset = queryset.filter(purchase_type=purchase_type)
        if event_name:
            queryset = queryset.filter(event_name__icontains=event_name)

        # Calculate summary for the ENTIRE filtered set (before pagination)
        from django.db.models import Sum, F
        
        summary_agg = queryset.aggregate(
            total_qty=Sum('quantity'),
            gross_total_price=Sum('total_price'),
            total_tax=Sum(F('total_price') * F('tax_rate') / (100 + F('tax_rate')))
        )
        total_qty = summary_agg['total_qty'] or Decimal('0')
        gross_total_price = summary_agg['gross_total_price'] or Decimal('0')
        total_tax = (summary_agg['total_tax'] or Decimal('0')).quantize(Decimal('0.00'))

        # --- SUBTRACT VENDOR RETURNS ---
        returns_qs = VendorReturn.objects.all()
        if month:
            returns_qs = returns_qs.filter(return_date__month=month)
        if year:
            returns_qs = returns_qs.filter(return_date__year=year)
        if day:
            returns_qs = returns_qs.filter(return_date__day=day)
        if date_str:
            returns_qs = returns_qs.filter(return_date=date_str)
        if vendor_id:
            returns_qs = returns_qs.filter(vendor_id=vendor_id)
            
        if item_id or category_id:
            return_items_qs = VendorReturnItem.objects.filter(vendor_return__in=returns_qs)
            if item_id:
                return_items_qs = return_items_qs.filter(item_id=item_id)
            if category_id:
                return_items_qs = return_items_qs.filter(item__category_id=category_id)
            
            from django.db.models import Case, When, DecimalField
            return_summary = return_items_qs.annotate(
                discounted_price=Case(
                    When(discount_type='PERCENTAGE', then=F('unit_price') * (1 - F('discount_value') / 100)),
                    default=F('unit_price') - F('discount_value'),
                    output_field=DecimalField()
                )
            ).aggregate(
                total=Sum(F('quantity') * F('discounted_price') * (1 + F('tax_rate') / 100))
            )
            total_returned = (return_summary['total'] or Decimal('0')).quantize(Decimal('0.00'))
        else:
            total_returned = returns_qs.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
            
        net_total_price = gross_total_price - total_returned

        # Pagination
        page_size = request.query_params.get('page_size', '20')
        if page_size.lower() == 'all':
            paginated_queryset = queryset
            count = queryset.count()
        else:
            self.pagination_class = PRPagination
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                paginated_response = self.get_paginated_response(serializer.data)
                
                # Merge summary into the paginated response
                response_data = paginated_response.data
                response_data['summary'] = {
                    'total_qty': str(total_qty),
                    'total_tax': str(total_tax),
                    'gross_total_price': str(gross_total_price),
                    'total_returned': str(total_returned),
                    'total_price': str(net_total_price), # Front-end uses total_price as the main figure
                    'count': queryset.count()
                }
                return Response(response_data)
            
            paginated_queryset = queryset
            count = queryset.count()

        serializer = self.get_serializer(paginated_queryset, many=True)
        return Response({
            'count': count,
            'results': serializer.data,
            'summary': {
                'total_qty': str(total_qty),
                'total_tax': str(total_tax),
                'gross_total_price': str(gross_total_price),
                'total_returned': str(total_returned),
                'total_price': str(net_total_price),
                'count': count
            }
        })

    @action(detail=False, methods=['get'])
    def export_excel(self, request):
        from django.db.models import Sum
        queryset = self.get_queryset()
        month = request.query_params.get('month')
        year = request.query_params.get('year')
        vendor_id = request.query_params.get('vendor')
        item_id = request.query_params.get('item')
        category_id = request.query_params.get('category')
        purchase_type = request.query_params.get('purchase_type')
        event_name = request.query_params.get('event_name')

        # Apply manual filters
        if month:
            queryset = queryset.filter(purchase_date__month=month)
        if year:
            queryset = queryset.filter(purchase_date__year=year)
        if vendor_id:
            queryset = queryset.filter(vendor_id=vendor_id)
        if item_id:
            queryset = queryset.filter(item_id=item_id)
        if category_id:
            queryset = queryset.filter(item__category_id=category_id)
        if purchase_type:
            queryset = queryset.filter(purchase_type=purchase_type)
        if event_name:
            queryset = queryset.filter(event_name__icontains=event_name)
        
        # Create workbook and sheet
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Purchased Report"
        
        # Add Header Row (Month/Year)
        months_names = {
            '1': 'January', '2': 'February', '3': 'March', '4': 'April',
            '5': 'May', '6': 'June', '7': 'July', '8': 'August',
            '9': 'September', '10': 'October', '11': 'November', '12': 'December'
        }
        report_title = "Purchased Report"
        if month and year:
            report_title += f" for {months_names.get(month, month)} {year}"
        elif year:
            report_title += f" for Year {year}"
            
        header_cell = ws.cell(row=1, column=1, value=report_title)
        header_cell.font = Font(size=14, bold=True)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=10)
        header_cell.alignment = Alignment(horizontal='center')
        
        # Define table headers
        headers = [
            'Date', 'Vendor', 'Invoice No', 'Type', 'Event', 'Item', 'Qty', 'UOM', 'Unit Price', 
            'Tax %', 'Tax Amt', 'Total'
        ]
        
        # Style table headers (Row 3)
        header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        center_alignment = Alignment(horizontal='center', vertical='center')
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=3, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_alignment
            
        # Add data starting from Row 4
        row_num = 4
        grand_total_qty = Decimal('0')
        grand_total_tax = Decimal('0')
        grand_total_price = Decimal('0')
        
        for proc in queryset:
            # Calculate tax amount as done in frontend
            # total_price / (1 + tax_rate/100) * (tax_rate/100)
            tax_rate_dec = proc.tax_rate / Decimal('100')
            tax_amt = (proc.total_price / (Decimal('1') + tax_rate_dec)) * tax_rate_dec
            
            ws.cell(row=row_num, column=1, value=proc.purchase_date)
            ws.cell(row=row_num, column=2, value=proc.vendor.name)
            ws.cell(row=row_num, column=3, value=proc.receipt.invoice_number if proc.receipt else "N/A")
            ws.cell(row=row_num, column=4, value=proc.get_purchase_type_display())
            ws.cell(row=row_num, column=5, value=proc.event_name or "")
            ws.cell(row=row_num, column=6, value=proc.item.name)
            ws.cell(row=row_num, column=7, value=float(proc.quantity))
            ws.cell(row=row_num, column=8, value=proc.unit or proc.item.unit)
            ws.cell(row=row_num, column=9, value=float(proc.unit_price))
            ws.cell(row=row_num, column=10, value=float(proc.tax_rate))
            ws.cell(row=row_num, column=11, value=float(tax_amt))
            ws.cell(row=row_num, column=12, value=float(proc.total_price))
            
            grand_total_qty += proc.quantity
            grand_total_tax += tax_amt
            grand_total_price += proc.total_price
            row_num += 1

        # --- ADD VENDOR RETURNS DEDUCTION ---
        returns_qs = VendorReturn.objects.all()
        if month:
            returns_qs = returns_qs.filter(return_date__month=month)
        if year:
            returns_qs = returns_qs.filter(return_date__year=year)
        if vendor_id:
            returns_qs = returns_qs.filter(vendor_id=vendor_id)
            
        if item_id or category_id:
            return_items_qs = VendorReturnItem.objects.filter(vendor_return__in=returns_qs)
            if item_id:
                return_items_qs = return_items_qs.filter(item_id=item_id)
            if category_id:
                return_items_qs = return_items_qs.filter(item__category_id=category_id)
            
            total_returned = Decimal('0')
            for ri in return_items_qs:
                price = ri.unit_price
                if ri.discount_type == 'PERCENTAGE':
                    price -= (price * ri.discount_value / Decimal('100'))
                else:
                    price -= ri.discount_value
                
                item_total = ri.quantity * price
                tax_amt = item_total * (ri.tax_rate / Decimal('100'))
                total_returned += (item_total + tax_amt)
        else:
            total_returned = returns_qs.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
        
        if total_returned > 0:
            ws.cell(row=row_num, column=1, value="LESS: VENDOR RETURNS")
            ws.cell(row=row_num, column=1).font = Font(italic=True, color='FF0000')
            ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=11)
            ws.cell(row=row_num, column=12, value=-float(total_returned)).font = Font(italic=True, color='FF0000')
            row_num += 1
            
        # Add Grand Total Row
        ws.cell(row=row_num, column=1, value="NET TOTAL EXPENDITURE")
        ws.cell(row=row_num, column=1).font = Font(bold=True)
        ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=6)
        
        ws.cell(row=row_num, column=7, value=float(grand_total_qty)).font = Font(bold=True)
        ws.cell(row=row_num, column=11, value=float(grand_total_tax)).font = Font(bold=True)
        
        net_total = grand_total_price - total_returned
        ws.cell(row=row_num, column=12, value=float(net_total)).font = Font(bold=True, size=12, color='008000')
        
        # Style grand total row
        total_fill = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
        for col_num in range(1, 11):
            ws.cell(row=row_num, column=col_num).fill = total_fill

        # Auto-adjust column widths
        for col_idx, col in enumerate(ws.columns, 1):
            max_length = 0
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            for cell in col:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws.column_dimensions[column_letter].width = max_length + 2

        # Prepare response
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        file_name = f"Purchased_Report_{timezone.now().strftime('%Y%m%d')}.xlsx"
        response['Content-Disposition'] = f'attachment; filename={file_name}'
        
        wb.save(response)
        return response

    @action(detail=False, methods=['get'])
    def export_tracker_excel(self, request):
        queryset = self.get_queryset()
        month = request.query_params.get('month')
        year = request.query_params.get('year')
        category_id = request.query_params.get('category')
        purchase_type = request.query_params.get('purchase_type')
        
        if month:
            queryset = queryset.filter(purchase_date__month=month)
        if year:
            queryset = queryset.filter(purchase_date__year=year)
        if category_id:
            queryset = queryset.filter(item__category_id=category_id)
        if purchase_type:
            queryset = queryset.filter(purchase_type=purchase_type)
        
        # Get category name if provided
        category_name = "All Categories"
        if category_id:
            try:
                from core.models import ItemCategory
                category_name = ItemCategory.objects.get(id=category_id).name
            except:
                pass

        # Create workbook and sheet
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Purchase Tracker"

        # Determine number of days in the month
        if month and year:
            import calendar
            num_days = calendar.monthrange(int(year), int(month))[1]
        else:
            num_days = 31 # Default

        months_names = {
            '1': 'January', '2': 'February', '3': 'March', '4': 'April',
            '5': 'May', '6': 'June', '7': 'July', '8': 'August',
            '9': 'September', '10': 'October', '11': 'November', '12': 'December'
        }
        
        # Add Header Row
        report_title = f"Purchase Tracker: {category_name}"
        if month and year:
            report_title += f" for {months_names.get(month, month)} {year}"
            
        header_cell = ws.cell(row=1, column=1, value=report_title)
        header_cell.font = Font(size=14, bold=True)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=num_days + 3)
        header_cell.alignment = Alignment(horizontal='center')

        # Define table headers (Double row header)
        ws.cell(row=3, column=1, value="Purchased Items")
        ws.merge_cells(start_row=3, start_column=1, end_row=4, end_column=1)
        
        ws.cell(row=3, column=2, value="Tax %")
        ws.merge_cells(start_row=3, start_column=2, end_row=4, end_column=2)

        for day in range(1, num_days + 1):
            # Row 3: Day Name (Simplified for Excel)
            from datetime import date
            try:
                d = date(int(year), int(month), day)
                day_name = d.strftime('%a')
            except:
                day_name = "-"
            ws.cell(row=3, column=day + 2, value=day_name)
            # Row 4: Day Number
            ws.cell(row=4, column=day + 2, value=day)

        ws.cell(row=3, column=num_days + 3, value="Item Total")
        ws.merge_cells(start_row=3, start_column=num_days + 3, end_row=4, end_column=num_days + 3)

        # Style headers
        header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        center_alignment = Alignment(horizontal='center', vertical='center')

        for r in [3, 4]:
            for c in range(1, num_days + 4):
                cell = ws.cell(row=r, column=c)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = center_alignment

        # Group data
        grouped_data = {}
        for proc in queryset:
            item_name = proc.item.name
            if item_name not in grouped_data:
                grouped_data[item_name] = {
                    'tax': float(proc.tax_rate),
                    'unit': proc.unit or proc.item.unit or 'UOM',
                    'days': {},
                    'itemMonthlyTotal': 0,
                    'itemMonthlyQty': 0
                }
            day = proc.purchase_date.day
            if day not in grouped_data[item_name]['days']:
                grouped_data[item_name]['days'][day] = {'total': 0, 'qty': 0, 'price': 0}
            
            qty = float(proc.quantity)
            total_price = float(proc.total_price)
            unit_price = float(proc.unit_price) * (1 + (float(proc.tax_rate) / 100))
            
            grouped_data[item_name]['days'][day]['total'] += total_price
            grouped_data[item_name]['days'][day]['qty'] += qty
            grouped_data[item_name]['days'][day]['price'] = unit_price # Latest price for the day
            
            grouped_data[item_name]['itemMonthlyTotal'] += total_price
            grouped_data[item_name]['itemMonthlyQty'] += qty

        # Add data rows
        from openpyxl.styles import Border, Side
        
        ws.sheet_view.showGridLines = False

        thin_side = Side(border_style="thin", color="D4D4D4")
        item_border_top = Border(top=thin_side, left=thin_side, right=thin_side)
        item_border_mid = Border(left=thin_side, right=thin_side)
        item_border_bot = Border(bottom=thin_side, left=thin_side, right=thin_side)
        
        qty_font = Font(size=9, color='666666')
        rate_font = Font(size=8, color='0066CC')

        row_num = 5
        daily_totals = [0] * (num_days + 1)
        grand_total = 0
        
        item_name_alignment = Alignment(horizontal='left', vertical='center', wrapText=True)

        for item_name, data in sorted(grouped_data.items()):
            ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num+2, end_column=1)
            ws.merge_cells(start_row=row_num, start_column=2, end_row=row_num+2, end_column=2)

            name_cell = ws.cell(row=row_num, column=1, value=item_name)
            name_cell.font = Font(bold=True)
            name_cell.alignment = item_name_alignment
            
            tax_cell = ws.cell(row=row_num, column=2, value=f"{data['tax']}%")
            tax_cell.alignment = Alignment(horizontal='center', vertical='center')
            
            for day in range(1, num_days + 1):
                d_data = data['days'].get(day)
                cell_amt = ws.cell(row=row_num, column=day + 2)
                cell_qty = ws.cell(row=row_num+1, column=day + 2)
                cell_rate = ws.cell(row=row_num+2, column=day + 2)
                
                cell_amt.border = item_border_top
                cell_qty.border = item_border_mid
                cell_rate.border = item_border_bot
                
                cell_amt.alignment = Alignment(horizontal='center', vertical='bottom')
                cell_qty.alignment = Alignment(horizontal='center', vertical='center')
                cell_rate.alignment = Alignment(horizontal='center', vertical='top')

                if d_data:
                    cell_amt.value = d_data['total']
                    cell_amt.number_format = '₹#,##0.00'
                    cell_amt.font = Font(bold=True, color='D32F2F' if d_data['total'] < 0 else '2E7D32')
                    
                    cell_qty.value = d_data['qty']
                    cell_qty.number_format = f'0.00 "{data["unit"]}"'
                    cell_qty.font = qty_font
                    
                    if d_data['price'] > 0:
                        cell_rate.value = d_data['price']
                        cell_rate.number_format = '₹#,##0.00"/U"'
                        cell_rate.font = rate_font
                    
                    daily_totals[day] += d_data['total']
            
            # Item Total Column
            avg_rate = data['itemMonthlyTotal'] / data['itemMonthlyQty'] if data['itemMonthlyQty'] > 0 else 0
            
            t_amt = ws.cell(row=row_num, column=num_days + 3, value=data['itemMonthlyTotal'])
            t_amt.number_format = '₹#,##0.00'
            t_amt.font = Font(bold=True)
            t_amt.alignment = Alignment(horizontal='center', vertical='bottom')
            t_amt.border = item_border_top
            
            t_qty = ws.cell(row=row_num+1, column=num_days + 3, value=data['itemMonthlyQty'])
            t_qty.number_format = f'0.00 "{data["unit"]}"'
            t_qty.font = qty_font
            t_qty.alignment = Alignment(horizontal='center', vertical='center')
            t_qty.border = item_border_mid
            
            t_rate = ws.cell(row=row_num+2, column=num_days + 3, value=avg_rate)
            t_rate.number_format = '"Rate: "₹#,##0.00'
            t_rate.font = rate_font
            t_rate.alignment = Alignment(horizontal='center', vertical='top')
            t_rate.border = item_border_bot
            
            # Apply borders to the merged columns (1 and 2)
            for r in range(row_num, row_num+3):
                for c in (1, 2):
                    b = item_border_mid
                    if r == row_num: b = item_border_top
                    elif r == row_num+2: b = item_border_bot
                    ws.cell(row=r, column=c).border = b
            
            grand_total += data['itemMonthlyTotal']
            
            ws.row_dimensions[row_num].height = 20
            ws.row_dimensions[row_num+1].height = 15
            ws.row_dimensions[row_num+2].height = 15
            
            row_num += 3

        # Add Footer (Daily Totals)
        ws.cell(row=row_num, column=1, value="DAILY EXPENDITURE (₹)")
        ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=2)
        ws.cell(row=row_num, column=1).font = Font(bold=True)
        ws.cell(row=row_num, column=1).alignment = Alignment(vertical='center')
        
        for day in range(1, num_days + 1):
            if daily_totals[day] > 0:
                cell = ws.cell(row=row_num, column=day + 2, value=daily_totals[day])
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.number_format = '#,##0'

        grand_total_cell = ws.cell(row=row_num, column=num_days + 3, value=grand_total)
        grand_total_cell.font = Font(bold=True, size=12, color='FF0000')
        grand_total_cell.alignment = Alignment(horizontal='center', vertical='center')
        grand_total_cell.number_format = '₹#,##0.00'
        ws.row_dimensions[row_num].height = 30

        # Style footer
        footer_fill = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
        for c in range(1, num_days + 4):
            ws.cell(row=row_num, column=c).fill = footer_fill

        # Auto-adjust column widths
        for col_idx in range(1, num_days + 4):
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            if col_idx == 1:
                ws.column_dimensions[column_letter].width = 25
            else:
                ws.column_dimensions[column_letter].width = 10

        # Prepare response
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        file_name = f"Purchase_Tracker_{timezone.now().strftime('%Y%m%d')}.xlsx"
        response['Content-Disposition'] = f'attachment; filename={file_name}'
        
        wb.save(response)
        return response

    def _get_month_wise_summary_data(self, request):
        if request.method == 'POST':
            months_count = int(request.data.get('months', request.query_params.get('months', 3)))
            category_ids = request.data.get('categories', [])
        else:
            try:
                months_count = int(request.query_params.get('months', 3))
            except (ValueError, TypeError):
                months_count = 3
            category_ids = request.query_params.getlist('categories[]')
            purchase_type = request.query_params.get('purchase_type')
            
        # Calculate start date (rolling period)
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=months_count * 30)
        start_date = start_date.replace(day=1)
        
        queryset = Procurement.objects.filter(
            purchase_date__gte=start_date, 
            purchase_date__lte=end_date
        )
        
        if category_ids:
            queryset = queryset.filter(item__category_id__in=category_ids)
        if purchase_type:
            queryset = queryset.filter(purchase_type=purchase_type)
            
        # Group by item and month/year
        summary = queryset.values(
            'item__id', 
            'item__name', 
            'item__unit',
            'purchase_date__month',
            'purchase_date__year'
        ).annotate(
            qty=Sum('quantity'),
            amount=Sum('total_price')
        ).order_by('item__name', 'purchase_date__year', 'purchase_date__month')
        
        # Structure for the frontend
        data_map = {}
        for entry in summary:
            item_id = entry['item__id']
            m = entry['purchase_date__month']
            y = entry['purchase_date__year']
            
            if item_id not in data_map:
                data_map[item_id] = {
                    "item_id": item_id,
                    "item_name": entry['item__name'],
                    "unit": entry['item__unit'] or 'Unit',
                    "monthly_data": {}
                }
            
            # Subtract returns for this specific item and month
            returns_entry = VendorReturnItem.objects.filter(
                item_id=item_id,
                vendor_return__return_date__month=m,
                vendor_return__return_date__year=y
            ).aggregate(
                total_amt=Sum(F('quantity') * F('unit_price')),
                total_qty=Sum('quantity')
            )
            returns_total = returns_entry['total_amt'] or Decimal('0')
            returns_qty = returns_entry['total_qty'] or Decimal('0')
            
            net_amount = entry['amount'] - returns_total
            
            month_key = f"{y}-{str(m).zfill(2)}"
            data_map[item_id]["monthly_data"][month_key] = {
                "month": m,
                "year": y,
                "amount": float(net_amount),
                "qty": float(entry['qty'] - returns_qty)
            }
        
        return list(data_map.values())

    @action(detail=False, methods=['get', 'post'])
    def month_wise_summary(self, request):
        data = self._get_month_wise_summary_data(request)
        return Response(data)

    @action(detail=False, methods=['get'])
    def export_month_wise_summary_excel(self, request):
        try:
            # Extract filter params
            try:
                months_count = int(request.query_params.get('months', 3))
            except (ValueError, TypeError):
                months_count = 3
            
            purchase_type = request.query_params.get('purchase_type')
            
            # Get the data using the helper logic
            data = self._get_month_wise_summary_data(request)
            
            # Calculate month columns (same logic as frontend)
            month_columns = []
            curr = timezone.now().date()
            for _ in range(months_count):
                m = curr.month
                y = curr.year
                month_columns.insert(0, f"{y}-{str(m).zfill(2)}")
                # Go back one month
                m -= 1
                if m == 0:
                    m = 12
                    y -= 1
                curr = curr.replace(year=y, month=m, day=1)
                
            # Create Workbook
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Monthly Trend Analysis"
            
            # Title
            title = "MONTHLY TREND ANALYSIS REPORT"
            if purchase_type:
                title += f" - {purchase_type.replace('_', ' ')}"
            
            ws.cell(row=1, column=1, value=title).font = Font(size=14, bold=True)
            ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(month_columns) + 3)
            
            # Headers
            headers = ["Item Name", "UOM"]
            month_names = {
                '01': 'Jan', '02': 'Feb', '03': 'Mar', '04': 'Apr',
                '05': 'May', '06': 'Jun', '07': 'Jul', '08': 'Aug',
                '09': 'Sep', '10': 'Oct', '11': 'Nov', '12': 'Dec'
            }
            for col_key in month_columns:
                y, m = col_key.split('-')
                headers.append(f"{month_names[m]} {y}")
            headers.append("Total Amount")
            
            header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
            header_font = Font(color='FFFFFF', bold=True)
            
            for i, h in enumerate(headers, 1):
                cell = ws.cell(row=3, column=i, value=h)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal='center')
                
            # Data
            row_num = 4
            col_totals = [0] * (len(month_columns) + 1) # month columns + total column
            for item in data:
                ws.cell(row=row_num, column=1, value=item['item_name'])
                ws.cell(row=row_num, column=2, value=item['unit'])
                
                row_total = 0
                for i, col_key in enumerate(month_columns, 3):
                    m_val = item['monthly_data'].get(col_key, {})
                    amt = m_val.get('amount', 0)
                    ws.cell(row=row_num, column=i, value=amt)
                    row_total += amt
                    col_totals[i-3] += amt
                
                ws.cell(row=row_num, column=len(month_columns) + 3, value=row_total)
                col_totals[len(month_columns)] += row_total
                row_num += 1
                
            # Grand Total Row
            ws.cell(row=row_num, column=1, value="GRAND TOTAL")
            ws.cell(row=row_num, column=1).font = Font(bold=True)
            
            grand_total_fill = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
            for i, total in enumerate(col_totals, 3):
                cell = ws.cell(row=row_num, column=i, value=total)
                cell.font = Font(bold=True)
                cell.fill = grand_total_fill
                
            # Column width
            for col in ws.columns:
                max_length = 0
                column = get_column_letter(col[0].column)
                for cell in col:
                    try:
                        if cell.value and len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                ws.column_dimensions[column].width = max_length + 2
                
            # Prepare response
            response = HttpResponse(
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            file_name = f"Monthly_Trend_{timezone.now().strftime('%Y%m%d')}.xlsx"
            response['Content-Disposition'] = f'attachment; filename={file_name}'
            wb.save(response)
            return response
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _get_custom_summary_period_data(self, month, year, months_count, purchase_type=None):
        month = int(month)
        year = int(year)
        
        # Calculate the list of (month, year) tuples for the period
        period = []
        curr_m, curr_y = month, year
        for _ in range(months_count):
            period.append((curr_m, curr_y))
            curr_m -= 1
            if curr_m == 0:
                curr_m = 12
                curr_y -= 1
        period.reverse() # Oldest to newest
            
        configs = CustomSummaryConfig.objects.all().order_by('display_order')
        
        results = []
        for config in configs:
            if config.config_type == 'CATEGORY':
                if config.show_total_only:
                    # Aggregate for the whole category across the period
                    monthly_data = []
                    for m, y in period:
                        proc_filter = {
                            "purchase_date__month": m,
                            "purchase_date__year": y,
                            "item__category": config.category
                        }
                        if purchase_type:
                            proc_filter["purchase_type"] = purchase_type
                            
                        gross_total = Procurement.objects.filter(**proc_filter).aggregate(total=Sum('total_price'))['total'] or Decimal('0')
                        
                        returns_total = VendorReturnItem.objects.filter(
                            vendor_return__return_date__month=m,
                            vendor_return__return_date__year=y,
                            item__category=config.category
                        ).aggregate(total=Sum(F('quantity') * F('unit_price') * (1 + F('tax_rate')/100)))['total'] or Decimal('0')
                        
                        net_total = gross_total - returns_total
                        monthly_data.append({"month": m, "year": y, "total": float(net_total)})
                    
                    results.append({
                        "type": "CATEGORY",
                        "id": config.category.id,
                        "name": config.display_name or config.category.name,
                        "has_override": bool(config.display_name),
                        "monthly_data": monthly_data
                    })
                else:
                    # Aggregate for specific items within category
                    items = config.specific_items.all()
                    itemized_rows = []
                    for item in items:
                        item_monthly_data = []
                        for m, y in period:
                            proc_filter = {
                                "purchase_date__month": m,
                                "purchase_date__year": y,
                                "item": item
                            }
                            if purchase_type:
                                proc_filter["purchase_type"] = purchase_type
                                
                            gross_total = Procurement.objects.filter(**proc_filter).aggregate(total=Sum('total_price'))['total'] or Decimal('0')
                            
                            returns_total = VendorReturnItem.objects.filter(
                                vendor_return__return_date__month=m,
                                vendor_return__return_date__year=y,
                                item=item
                            ).aggregate(total=Sum(F('quantity') * F('unit_price') * (1 + F('tax_rate')/100)))['total'] or Decimal('0')
                            
                            net_total = gross_total - returns_total
                            item_monthly_data.append({"month": m, "year": y, "total": float(net_total)})
                        
                        itemized_rows.append({
                            "id": item.id,
                            "name": item.name,
                            "unit": item.unit,
                            "monthly_data": item_monthly_data
                        })
                    
                    results.append({
                        "type": "ITEMIZED_CATEGORY",
                        "id": config.category.id,
                        "name": config.display_name or config.category.name,
                        "has_override": bool(config.display_name),
                        "sub_items": itemized_rows
                    })
            elif config.config_type == 'ITEM':
                # Standalone ITEM block
                item_monthly_data = []
                for m, y in period:
                    proc_filter = {
                        "purchase_date__month": m,
                        "purchase_date__year": y,
                        "item": config.item
                    }
                    if purchase_type:
                        proc_filter["purchase_type"] = purchase_type
                        
                    gross_total = Procurement.objects.filter(**proc_filter).aggregate(total=Sum('total_price'))['total'] or Decimal('0')
                    
                    returns_total = VendorReturnItem.objects.filter(
                        vendor_return__return_date__month=m,
                        vendor_return__return_date__year=y,
                        item=config.item
                    ).aggregate(total=Sum(F('quantity') * F('unit_price') * (1 + F('tax_rate')/100)))['total'] or Decimal('0')
                    
                    net_total = gross_total - returns_total
                    item_monthly_data.append({"month": m, "year": y, "total": float(net_total)})
                
                results.append({
                    "type": "ITEM",
                    "id": config.item.id,
                    "name": config.display_name or config.item.name,
                    "has_override": bool(config.display_name),
                    "unit": config.item.unit,
                    "monthly_data": item_monthly_data
                })
            elif config.config_type == 'GROUP':
                # Manual GROUP of items
                group_monthly_data = []
                items = config.specific_items.all()
                for m, y in period:
                    proc_filter = {
                        "purchase_date__month": m,
                        "purchase_date__year": y,
                        "item__in": items
                    }
                    if purchase_type:
                        proc_filter["purchase_type"] = purchase_type
                        
                    gross_total = Procurement.objects.filter(**proc_filter).aggregate(total=Sum('total_price'))['total'] or Decimal('0')
                    
                    returns_total = VendorReturnItem.objects.filter(
                        vendor_return__return_date__month=m,
                        vendor_return__return_date__year=y,
                        item__in=items
                    ).aggregate(total=Sum(F('quantity') * F('unit_price') * (1 + F('tax_rate')/100)))['total'] or Decimal('0')
                    
                    net_total = gross_total - returns_total
                    group_monthly_data.append({"month": m, "year": y, "total": float(net_total)})
                
                results.append({
                    "type": "GROUP",
                    "id": config.id,
                    "name": config.display_name or config.custom_name or "Unnamed Group",
                    "has_override": bool(config.display_name),
                    "monthly_data": group_monthly_data
                })
        return {"period": period, "rows": results}

    @action(detail=False, methods=['get'])
    def custom_monthly_summary(self, request):
        month = request.query_params.get('month')
        year = request.query_params.get('year')
        months_count = int(request.query_params.get('months_count', 1))
        
        purchase_type = request.query_params.get('purchase_type')
        
        if not month or not year:
            return Response({"error": "Month and year are required"}, status=status.HTTP_400_BAD_REQUEST)
            
        data = self._get_custom_summary_period_data(month, year, months_count, purchase_type)
        return Response({
            "period": [{"month": m, "year": y} for m, y in data["period"]],
            "rows": data["rows"]
        })

    @action(detail=False, methods=['get'])
    def export_custom_summary_excel(self, request):
        month = request.query_params.get('month')
        year = request.query_params.get('year')
        months_count = int(request.query_params.get('months_count', 1))
        
        purchase_type = request.query_params.get('purchase_type')
        
        if not month or not year:
            return Response({"error": "Month and year are required"}, status=status.HTTP_400_BAD_REQUEST)
            
        data = self._get_custom_summary_period_data(month, year, months_count, purchase_type)
        
        # Build Excel
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Expenditure Analysis"
        
        from openpyxl.styles import Border, Side
        
        # Styles
        title_font = Font(size=16, bold=True, color='1F4E78')
        subtitle_font = Font(size=12, italic=True, color='44546A')
        header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        category_fill = PatternFill(start_color='D9E1F2', end_color='D9E1F2', fill_type='solid')
        category_font = Font(bold=True, color='1F4E78')
        total_fill = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
        grand_total_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        grand_total_font = Font(color='FFFFFF', bold=True, size=12)
        
        border = Border(
            left=Side(style='thin', color='BFBFBF'),
            right=Side(style='thin', color='BFBFBF'),
            top=Side(style='thin', color='BFBFBF'),
            bottom=Side(style='thin', color='BFBFBF')
        )
        
        num_cols = len(data['period']) + 2
        
        # Report Title
        months_names = {
            1: 'January', 2: 'February', 3: 'March', 4: 'April',
            5: 'May', 6: 'June', 7: 'July', 8: 'August',
            9: 'September', 10: 'October', 11: 'November', 12: 'December'
        }
        report_title = "EXPENDITURE ANALYSIS REPORT"
        if purchase_type:
            report_title += f" - {purchase_type.replace('_', ' ')}"
            
        subtitle = f"Period: {months_count} Months ending {months_names.get(int(month))} {year}"
        
        ws.cell(row=1, column=1, value=report_title).font = title_font
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=num_cols)
        ws.cell(row=2, column=1, value=subtitle).font = subtitle_font
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=num_cols)
        ws.cell(row=3, column=1, value=f"Generated: {timezone.now().strftime('%Y-%m-%d %H:%M')}")
        
        # Table Headers (Row 5)
        headers = ["PARTICULARS"]
        for m, y in data['period']:
            headers.append(f"{months_names[m][:3]} '{str(y)[2:]}")
        headers.append("PERIOD TOTAL")
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=5, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = border
            
        row_num = 6
        grand_column_totals = [0] * len(data['period'])
        
        for row in data['rows']:
            if row['type'] in ['CATEGORY', 'ITEM', 'GROUP']:
                # Label
                label = row['name'].upper()
                if not row['has_override']:
                    label += " (TOTAL)" if row['type'] == 'CATEGORY' else " (GROUP)" if row['type'] == 'GROUP' else ""
                
                cell = ws.cell(row=row_num, column=1, value=label)
                cell.font = Font(bold=True) if row['type'] in ['CATEGORY', 'GROUP'] else Font()
                cell.fill = category_fill if row['type'] in ['CATEGORY', 'GROUP'] else PatternFill()
                cell.border = border
                
                # Data
                row_total = 0
                for i, d in enumerate(row['monthly_data'], 2):
                    c = ws.cell(row=row_num, column=i, value=d['total'])
                    c.number_format = '#,##0'
                    c.border = border
                    row_total += d['total']
                    grand_column_totals[i-2] += d['total']
                
                # Row Total
                c = ws.cell(row=row_num, column=num_cols, value=row_total)
                c.font = Font(bold=True)
                c.number_format = '#,##0.00'
                c.border = border
                row_num += 1
                
            elif row['type'] == 'ITEMIZED_CATEGORY':
                # Category Header Row
                cell = ws.cell(row=row_num, column=1, value=row['name'].upper() + " BREAKDOWN")
                cell.fill = category_fill
                cell.font = category_font
                cell.border = border
                ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=num_cols)
                row_num += 1
                
                cat_column_totals = [0] * len(data['period'])
                
                # Sub-items
                for item in row['sub_items']:
                    ws.cell(row=row_num, column=1, value="  " + item['name']).border = border
                    item_total = 0
                    for i, d in enumerate(item['monthly_data'], 2):
                        c = ws.cell(row=row_num, column=i, value=d['total'])
                        c.number_format = '#,##0'
                        c.border = border
                        item_total += d['total']
                        cat_column_totals[i-2] += d['total']
                        grand_column_totals[i-2] += d['total']
                    
                    c = ws.cell(row=row_num, column=num_cols, value=item_total)
                    c.number_format = '#,##0.00'
                    c.border = border
                    row_num += 1
                
                # Category Subtotal Row
                subtotal_label = row['name'].upper() if row['has_override'] else f"SUBTOTAL ({row['name'].upper()})"
                cell = ws.cell(row=row_num, column=1, value=subtotal_label)
                cell.font = Font(bold=True)
                cell.fill = total_fill
                cell.border = border
                
                cat_period_total = 0
                for i, val in enumerate(cat_column_totals, 2):
                    c = ws.cell(row=row_num, column=i, value=val)
                    c.font = Font(bold=True)
                    c.number_format = '#,##0'
                    c.border = border
                    cat_period_total += val
                    
                c = ws.cell(row=row_num, column=num_cols, value=cat_period_total)
                c.font = Font(bold=True)
                c.number_format = '#,##0.00'
                c.fill = total_fill
                c.border = border
                row_num += 1
        
        # Grand Total Row
        cell = ws.cell(row=row_num, column=1, value="GRAND TOTAL")
        cell.font = grand_total_font
        cell.fill = grand_total_fill
        cell.border = border
        
        final_total = 0
        for i, val in enumerate(grand_column_totals, 2):
            c = ws.cell(row=row_num, column=i, value=val)
            c.font = grand_total_font
            c.fill = grand_total_fill
            c.number_format = '#,##0'
            c.border = border
            final_total += val
            
        c = ws.cell(row=row_num, column=num_cols, value=final_total)
        c.font = grand_total_font
        c.fill = grand_total_fill
        c.number_format = '#,##0.00'
        c.border = border
        
        # Final Auto-adjust
        for col_idx in range(1, num_cols + 1):
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            if col_idx == 1:
                ws.column_dimensions[column_letter].width = 35
            else:
                ws.column_dimensions[column_letter].width = 15
                
        # Response
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=Custom_Expenditure_Summary_{timezone.now().strftime("%Y%m%d")}.xlsx'
        wb.save(response)
        return response

    @action(detail=False, methods=['get'])
    def top_purchased_items(self, request):
        category_id = request.query_params.get('category')
        
        queryset = Procurement.objects.all()
        if category_id:
            queryset = queryset.filter(item__category_id=category_id)
            
        # 1. Get gross spend in bulk
        top_items_qs = queryset.values('item__id', 'item__name').annotate(
            gross_spend=Sum('total_price'),
            gross_qty=Sum('quantity')
        )
        
        # 2. Get returns in bulk
        returns_map = {
            r['item_id']: r for r in VendorReturnItem.objects.values('item_id').annotate(
                total_amt=Sum(F('quantity') * F('unit_price') * (1 + F('tax_rate')/100)),
                total_qty=Sum('quantity')
            )
        }
        
        result = []
        for ti in top_items_qs:
            item_id = ti['item__id']
            r = returns_map.get(item_id, {})
            returns_total = r.get('total_amt') or Decimal('0')
            itm_returns_qty = r.get('total_qty') or Decimal('0')
            
            net_spend = ti['gross_spend'] - returns_total
            
            result.append({
                'item_id': item_id,
                'item_name': ti['item__name'],
                'total_spend': net_spend,
                'total_qty': ti['gross_qty'] - itm_returns_qty
            })
            
        # Sort by net spend and take top 10
        result.sort(key=lambda x: x['total_spend'], reverse=True)
        top_10 = result[:10]
        
        # Now fetch prices ONLY for the top 10
        final_result = []
        for item in top_10:
            last_5 = Procurement.objects.filter(item_id=item['item_id']).order_by('-purchase_date')[:5]
            prices = [{'date': p.purchase_date, 'price': float((p.unit_price * (1 + p.tax_rate/100)).quantize(Decimal('0.00')))} for p in last_5]
            
            final_result.append({
                **item,
                'total_spend': float(item['total_spend'].quantize(Decimal('0.01'))),
                'total_qty': float(item['total_qty'].quantize(Decimal('0.001'))),
                'prices': prices
            })
            
        return Response(final_result)

    @action(detail=False, methods=['get'])
    def vendor_spend_analysis(self, request):
        try:
            days = int(request.query_params.get('days', 7))
        except:
            days = 7
            
        try:
            top_n = int(request.query_params.get('top_n', 5))
        except:
            top_n = 5
            
        start_date = timezone.now().date() - timedelta(days=days)
        
        # 1. Fetch all relevant procurements in bulk
        procs = Procurement.objects.filter(
            purchase_date__gte=start_date
        ).values('vendor_id', 'vendor__name', 'item_id', 'item__name').annotate(
            gross_spend=Sum('total_price'),
            gross_qty=Sum('quantity')
        )
        
        # 2. Fetch all relevant returns in bulk
        from django.db.models import Case, When, DecimalField
        returns = VendorReturnItem.objects.filter(
            vendor_return__return_date__gte=start_date
        ).annotate(
            discounted_price=Case(
                When(discount_type='PERCENTAGE', then=F('unit_price') * (1 - F('discount_value') / 100)),
                default=F('unit_price') - F('discount_value'),
                output_field=DecimalField()
            )
        ).values('vendor_return__vendor_id', 'item_id').annotate(
            return_amt=Sum(F('quantity') * F('discounted_price') * (1 + F('tax_rate') / 100)),
            return_qty=Sum('quantity')
        )
        
        # 3. Combine data in Python
        vendor_map = {}
        for p in procs:
            v_id = p['vendor_id']
            if v_id not in vendor_map:
                vendor_map[v_id] = {
                    'name': p['vendor__name'],
                    'items': {},
                    'total_spend': Decimal('0')
                }
            
            i_id = p['item_id']
            if i_id not in vendor_map[v_id]['items']:
                vendor_map[v_id]['items'][i_id] = {
                    'name': p['item__name'],
                    'spend': Decimal('0'),
                    'quantity': Decimal('0')
                }
            
            vendor_map[v_id]['items'][i_id]['spend'] += p['gross_spend']
            vendor_map[v_id]['items'][i_id]['quantity'] += p['gross_qty']
            vendor_map[v_id]['total_spend'] += p['gross_spend']
            
        for r in returns:
            v_id = r['vendor_return__vendor_id']
            i_id = r['item_id']
            if v_id in vendor_map:
                if i_id in vendor_map[v_id]['items']:
                    vendor_map[v_id]['items'][i_id]['spend'] -= r['return_amt']
                    vendor_map[v_id]['items'][i_id]['quantity'] -= r['return_qty']
                    vendor_map[v_id]['total_spend'] -= r['return_amt']
        
        # 4. Format and sort results
        result = []
        for v_id, data in vendor_map.items():
            item_list = []
            for i_id, i_data in data['items'].items():
                if i_data['spend'] > 0 or i_data['quantity'] > 0:
                    item_list.append({
                        "name": i_data['name'],
                        "spend": float(i_data['spend'].quantize(Decimal('0.01'))),
                        "quantity": float(i_data['quantity'].quantize(Decimal('0.001')))
                    })
            
            item_list.sort(key=lambda x: x['spend'], reverse=True)
            result.append({
                "vendor_id": v_id,
                "vendor_name": data['name'],
                "total_spend": float(data['total_spend'].quantize(Decimal('0.01'))),
                "items": item_list
            })
            
        result.sort(key=lambda x: x['total_spend'], reverse=True)
        return Response(result[:top_n])

class MRPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 1000


class MaterialRequestViewSet(viewsets.ModelViewSet):
    queryset = MaterialRequest.objects.all().order_by('-created_at')
    serializer_class = MaterialRequestSerializer
    pagination_class = MRPagination

    def get_queryset(self):
        user = self.request.user
        queryset = MaterialRequest.objects.all().order_by('-created_at')

        # 1. Apply Search/Filter Parameters First (Generic filters)
        status_filter = self.request.query_params.get('status')
        if status_filter:
            if ',' in status_filter:
                queryset = queryset.filter(status__in=status_filter.split(','))
            else:
                queryset = queryset.filter(status=status_filter)

        is_ack = self.request.query_params.get('is_acknowledged')
        if is_ack is not None:
            queryset = queryset.filter(is_acknowledged=is_ack.lower() == 'true')

        mr_number = self.request.query_params.get('mr_number')
        if mr_number:
            queryset = queryset.filter(mr_number__icontains=mr_number)

        # Use from_date and to_date
        from_date = self.request.query_params.get('from_date')
        to_date = self.request.query_params.get('to_date')
        if from_date:
            queryset = queryset.filter(order_date__gte=from_date)
        if to_date:
            queryset = queryset.filter(order_date__lte=to_date)

        # 2. Permission-based record filtering REMOVED as per user request
        # All users with access to this endpoint will see the full global history, 
        # allowing different users (like admins and kitchen staff) to collaborate 
        # on the same requests (e.g., continuing a draft started by someone else).

        return queryset

    def get_permissions(self):
        if self.action == 'create' or self.action == 'next_mr_number':
            self.permission_classes = [CanPlaceMR]
        elif self.action == 'kitchen_accept':
            self.permission_classes = [CanPlaceMR | CanManageMR]
        elif self.action in ['accept', 'reject', 'undo_action', 'acknowledge']:
            self.permission_classes = [CanManageMR]
        elif self.action == 'verify':
            self.permission_classes = [CanVerifyMR]
        elif self.action in ['list', 'retrieve', 'export_excel']:
            # Verifiers and Managers should both be able to see the list
            self.permission_classes = [CanViewMR | CanVerifyMR | CanManageMR]
        elif self.action == 'destroy':
            self.permission_classes = [CanDeleteMR]
        else:
            self.permission_classes = [permissions.IsAuthenticated]
        return super().get_permissions()

    def perform_create(self, serializer):
        user = self.request.user
        # Get the first department the user belongs to as the default
        department = None
        if hasattr(user, 'profile'):
            department = user.profile.departments.first()
        instance = serializer.save(requested_by=user, department=department)
        
        if instance.status == 'PLACED':
            from django.contrib.auth import get_user_model
            from django.db.models import Q
            from django.utils import timezone
            User = get_user_model()
            has_verifiers = User.objects.filter(is_active=True, profile__isnull=False).filter(
                Q(profile__role__can_verify_mr=True) | Q(profile__can_verify_mr=True)
            ).exists()
            if not has_verifiers:
                instance.status = 'MGMT_APPROVED'
                instance.mgmt_remarks = 'Auto-approved: No MR Verifiers configured in the system.'
                instance.mgmt_verified_at = timezone.now()
                instance.save(update_fields=['status', 'mgmt_remarks', 'mgmt_verified_at'])

    def perform_update(self, serializer):
        instance = serializer.save()
        if instance.status == 'PLACED':
            from django.contrib.auth import get_user_model
            from django.db.models import Q
            from django.utils import timezone
            User = get_user_model()
            has_verifiers = User.objects.filter(is_active=True, profile__isnull=False).filter(
                Q(profile__role__can_verify_mr=True) | Q(profile__can_verify_mr=True)
            ).exists()
            if not has_verifiers:
                instance.status = 'MGMT_APPROVED'
                instance.mgmt_remarks = 'Auto-approved: No MR Verifiers configured in the system.'
                instance.mgmt_verified_at = timezone.now()
                instance.save(update_fields=['status', 'mgmt_remarks', 'mgmt_verified_at'])

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status in ['ISSUED', 'ACCEPTED', 'REJECTED'] or instance.is_acknowledged:
            return Response({"error": "Cannot edit a request that is acknowledged or processed."}, status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status in ['ISSUED', 'ACCEPTED', 'REJECTED'] or instance.is_acknowledged:
            return Response({"error": "Cannot edit a request that is acknowledged or processed."}, status=status.HTTP_400_BAD_REQUEST)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        
        return Response({"error": "For audit compliance and tracking, Material Requests cannot be deleted from the database. Please reject or cancel the request instead."}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def next_mr_number(self, request):
        order_date_str = request.query_params.get('order_date')
        if order_date_str:
            try:
                date_obj = timezone.datetime.strptime(order_date_str, '%Y-%m-%d')
                formatted_date = date_obj.strftime('%Y%m%d')
            except (ValueError, TypeError):
                formatted_date = timezone.now().strftime('%Y%m%d')
        else:
            formatted_date = timezone.now().strftime('%Y%m%d')
            
        prefix = f"MR-{formatted_date}-"
        last_mr = MaterialRequest.objects.filter(
            mr_number__startswith=prefix
        ).order_by('-mr_number').first()
        
        if last_mr:
            try:
                last_seq = int(last_mr.mr_number.split('-')[-1])
                new_seq = last_seq + 1
            except (ValueError, IndexError):
                new_seq = 1
        else:
            new_seq = 1
            
        next_ref = f"{prefix}{str(new_seq).zfill(4)}"
        return Response({"next_mr_number": next_ref})

    @action(detail=False, methods=['get'])
    def download_bulk_template(self, request):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Bulk MR Template"
        
        headers = ['Item Name', 'Quantity', 'UOM']
        
        # Style headers
        header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        alignment = Alignment(horizontal='center', vertical='center')
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = alignment
            
        # Pre-fill with items from the registry
        items = Item.objects.all().order_by('name')
        for row_num, item in enumerate(items, 2):
            ws.cell(row=row_num, column=1, value=item.name)
            ws.cell(row=row_num, column=2, value=0)
            ws.cell(row=row_num, column=3, value=item.unit)

        # Auto-adjust column widths
        for col_idx, col in enumerate(ws.columns, 1):
            max_length = 0
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            for cell in col:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws.column_dimensions[column_letter].width = max_length + 2

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = 'attachment; filename=mr_bulk_template.xlsx'
        wb.save(response)
        return response

    @action(detail=False, methods=['post'])
    def parse_bulk_excel(self, request):
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            wb = openpyxl.load_workbook(file_obj, data_only=True)
            ws = wb.active
            
            parsed_items = []
            skipped_items = []
            
            # Assuming headers are in row 1: [Item Name, Quantity, UOM]
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not row[0]: # Skip rows with no item name
                    continue
                    
                item_name = str(row[0]).strip()
                try:
                    quantity = float(row[1] or 0)
                except (ValueError, TypeError):
                    quantity = 0
                
                uom = str(row[2] or "").strip()
                
                # Basic validation: skip rows where quantity is 0
                if quantity == 0:
                    continue

                item = Item.objects.filter(name__iexact=item_name).first()
                if item:
                    parsed_items.append({
                        "id": item.id,
                        "name": item.name,
                        "category_name": item.category.name if item.category else "",
                        "quantity": quantity,
                        "unit": uom or item.unit
                    })
                else:
                    skipped_items.append(item_name)
                    
            return Response({
                "items": parsed_items,
                "skipped": skipped_items
            })
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def place(self, request, pk=None):
        try:
            with transaction.atomic():
                mr = MaterialRequest.objects.select_for_update().get(pk=pk)
                if mr.status != 'DRAFT':
                    return Response({"error": "Only DRAFT requests can be placed"}, status=status.HTTP_400_BAD_REQUEST)
                
                # Run strict stock validation before allowing placement
                serializer = self.get_serializer(mr, data={}, partial=True)
                if not serializer.is_valid():
                    return Response({"error": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
                

                from django.contrib.auth import get_user_model
                from django.db.models import Q
                User = get_user_model()
                
                has_verifiers = User.objects.filter(is_active=True, profile__isnull=False).filter(
                    Q(profile__role__can_verify_mr=True) | Q(profile__can_verify_mr=True)
                ).exists()

                if has_verifiers:
                    mr.status = 'PLACED'
                    status_msg = "Material Request placed successfully"
                else:
                    mr.status = 'MGMT_APPROVED'
                    mr.mgmt_remarks = 'Auto-approved: No MR Verifiers configured in the system.'
                    mr.mgmt_verified_at = timezone.now()
                    status_msg = "Material Request auto-approved successfully (No verifiers configured)"
                
                # Initialize original_quantity if not already set
                for item in mr.items.all():
                    if item.original_quantity is None:
                        item.original_quantity = item.quantity
                        item.save()
                        
                mr.save()
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"status": status_msg})

    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        mr = self.get_object()
        if mr.status == 'DRAFT':
            return Response({"error": "Cannot acknowledge a DRAFT request. It must be PLACED first."}, status=status.HTTP_400_BAD_REQUEST)

        mr.is_acknowledged = True
        mr.save()
        return Response({"status": "Material Request acknowledged successfully"})

    @action(detail=True, methods=['post'])
    def verify(self, request, pk=None):
        """
        Management verification step.
        action=APPROVE: optionally adjust item quantities, then move MR to MGMT_APPROVED.
        action=SEND_BACK: add a management remark and revert MR to PLACED for kitchen to re-submit.
        Requires: can_verify_mr permission.
        """
        mr = self.get_object()

        # Permission check
        profile = getattr(request.user, 'profile', None)
        if not request.user.is_superuser and not (
            (profile and profile.role and profile.role.can_verify_mr) or
            (profile and profile.can_verify_mr)
        ):
            return Response(
                {"error": "You do not have permission to verify kitchen MRs."},
                status=status.HTTP_403_FORBIDDEN
            )

        action_type = request.data.get('action')  # 'APPROVE' or 'SEND_BACK'
        mgmt_remarks = request.data.get('mgmt_remarks', '')
        items_data = request.data.get('items', [])  # [{id, quantity}]

        if action_type not in ('APPROVE', 'SEND_BACK'):
            return Response(
                {"error": "action must be 'APPROVE' or 'SEND_BACK'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if mr.status not in ('PLACED',):
            return Response(
                {"error": f"Only PLACED MRs can be verified. Current status: {mr.status}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            with transaction.atomic():
                if action_type == 'APPROVE':
                    # Apply any quantity adjustments
                    for item_update in items_data:
                        item_id = item_update.get('id')
                        new_qty = item_update.get('quantity')
                        adj_remarks = item_update.get('mgmt_adjustment_remarks', '')
                        
                        if item_id and new_qty is not None:
                            mr_item = mr.items.filter(id=item_id).first()
                            if mr_item:
                                from decimal import Decimal as D
                                
                                # Ensure original_quantity is preserved
                                if mr_item.original_quantity is None:
                                    mr_item.original_quantity = mr_item.quantity
                                
                                mr_item.quantity = D(str(new_qty))
                                mr_item.mgmt_adjustment_remarks = adj_remarks
                                mr_item.save()

                    mr.status = 'MGMT_APPROVED'
                    mr.mgmt_verified_by = request.user
                    mr.mgmt_verified_at = timezone.now()
                    mr.mgmt_remarks = mgmt_remarks
                    mr.save()
                    return Response({"status": "MR approved by management and sent to procurement."})

                else:  # SEND_BACK
                    if not mgmt_remarks:
                        return Response(
                            {"error": "A remark is required when sending an MR back to kitchen."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    mr.status = 'DRAFT'
                    mr.mgmt_remarks = mgmt_remarks
                    mr.mgmt_verified_by = request.user
                    mr.mgmt_verified_at = timezone.now()
                    mr.save()
                    return Response({"status": "MR sent back to kitchen with remarks."})

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        try:
            with transaction.atomic():
                mr = MaterialRequest.objects.select_for_update().get(pk=pk)
                
                if mr.status != 'MGMT_APPROVED':
                    return Response(
                        {"error": "Only Management-Approved requests can be accepted by procurement."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Expect batch_allocations: list of { mr_item_id, allocations: [{batch_id, quantity}] }
                batch_allocations = request.data.get('batch_allocations', [])
                use_batch_mode = len(batch_allocations) > 0
                force_issue = request.data.get('force_issue', False)

                procurement_dept = Department.objects.get(code='procurement')
                kitchen_dept = Department.objects.get(code='kitchen')

                for mr_item in mr.items.all():
                    try:
                        store_inv = Inventory.objects.get(
                            item=mr_item.item,
                            department=procurement_dept
                        )
                    except Inventory.DoesNotExist:
                        # Create empty inventory if not exists
                        store_inv = Inventory.objects.create(
                            item=mr_item.item,
                            department=procurement_dept,
                            current_quantity=0,
                            inventory_value=0,
                            average_price=0
                        )

                    # Strict check for stock sufficiency
                    if store_inv.current_quantity < mr_item.quantity:
                        return Response(
                            {"error": f"Insufficient stock for {mr_item.item.name}. Available: {store_inv.current_quantity}, Requested: {mr_item.quantity}."},
                            status=status.HTTP_400_BAD_REQUEST
                        )

                    total_cost = Decimal('0.00')
                    item_allocation = next(
                        (a for a in batch_allocations if a.get('mr_item_id') == mr_item.id), None
                    )

                    if use_batch_mode and item_allocation and item_allocation.get('allocations'):
                        # --- BATCH (FIFO) MODE ---
                        total_allocated = Decimal('0')
                        for alloc in item_allocation.get('allocations', []):
                            batch_id = alloc.get('batch_id')
                            alloc_qty = Decimal(str(alloc.get('quantity', 0)))
                            if alloc_qty <= 0:
                                continue

                            try:
                                batch = StockBatch.objects.select_for_update().get(
                                    id=batch_id,
                                    item=mr_item.item,
                                    department=procurement_dept
                                )
                            except StockBatch.DoesNotExist:
                                return Response(
                                    {"error": f"Stock batch {batch_id} not found for {mr_item.item.name}."},
                                    status=status.HTTP_400_BAD_REQUEST
                                )

                            if batch.remaining_quantity < alloc_qty:
                                return Response(
                                    {"error": f"Batch {batch_id} has only {batch.remaining_quantity} remaining, but {alloc_qty} was requested."},
                                    status=status.HTTP_400_BAD_REQUEST
                                )

                            batch.remaining_quantity -= alloc_qty
                            batch.save()

                            line_cost = (alloc_qty * batch.unit_price).quantize(Decimal('0.00'))
                            total_cost += line_cost
                            total_allocated += alloc_qty

                            MRItemBatchAllocation.objects.create(
                                mr_item=mr_item,
                                stock_batch=batch,
                                quantity_allocated=alloc_qty
                            )

                        # If we used batches, ensure they covered the whole requested amount
                        if total_allocated != mr_item.quantity:
                            return Response(
                                {"error": f"Allocated {total_allocated} for {mr_item.item.name} but MR requires {mr_item.quantity}."},
                                status=status.HTTP_400_BAD_REQUEST
                            )
                        
                        # Deduct from aggregate inventory
                        store_inv.current_quantity -= mr_item.quantity
                        store_inv.inventory_value -= total_cost
                        if store_inv.current_quantity > 0:
                            store_inv.average_price = (store_inv.inventory_value / store_inv.current_quantity).quantize(Decimal('0.00'))
                        elif store_inv.current_quantity < 0:
                            # Keep last average price for negative valuation if possible
                            pass
                        else:
                            store_inv.average_price = Decimal('0.00')
                        store_inv.save()

                    else:
                        # --- DIRECT DEDUCTION ---
                        # Use average price for cost estimation
                        mr_item.deficit_estimated_unit_cost = store_inv.average_price
                        cost_of_issuance = (mr_item.quantity * mr_item.deficit_estimated_unit_cost).quantize(Decimal('0.00'))

                        store_inv.current_quantity -= mr_item.quantity
                        store_inv.inventory_value -= cost_of_issuance
                        store_inv.save()
                        total_cost = cost_of_issuance

                    # Add to Kitchen Stock
                    kitchen_inv, _ = Inventory.objects.get_or_create(
                        item=mr_item.item,
                        department=kitchen_dept
                    )
                    kitchen_inv.current_quantity += mr_item.quantity
                    kitchen_inv.inventory_value += total_cost
                    if kitchen_inv.current_quantity > 0:
                        kitchen_inv.average_price = (kitchen_inv.inventory_value / kitchen_inv.current_quantity).quantize(Decimal('0.00'))
                    kitchen_inv.save()

                    # Track issued quantity and cost
                    mr_item.quantity_issued = mr_item.quantity
                    mr_item.consumption_unit_cost = (total_cost / mr_item.quantity).quantize(Decimal('0.00')) if mr_item.quantity > 0 else Decimal('0.00')
                    mr_item.consumption_total_cost = total_cost
                    mr_item.save()

                    # Log Movement
                    StockMovement.objects.create(
                        item=mr_item.item,
                        quantity=mr_item.quantity,
                        type='STORE_TO_KITCHEN',
                        reference_id=mr.mr_number,
                        user=request.user,
                        remarks=f"Accepted MR: {mr.mr_number} {'(FORCE ISSUE)' if force_issue else ''}"
                    )

                mr.status = 'ISSUED'
                mr.action_by = request.user
                mr.action_at = timezone.now()
                mr.save()
            return Response({"status": "Material Request issued and stock deducted"})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def kitchen_accept(self, request, pk=None):
        try:
            with transaction.atomic():
                mr = MaterialRequest.objects.select_for_update().get(pk=pk)
                if mr.status != 'ISSUED':
                    return Response({"error": "Only ISSUED requests can be accepted by the kitchen."}, status=status.HTTP_400_BAD_REQUEST)
                
                mr.status = 'ACCEPTED'
                mr.kitchen_accepted_by = request.user
                mr.kitchen_accepted_at = timezone.now()
                mr.save()
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"status": "Stock received and accepted by kitchen."})

    @action(detail=True, methods=['post'])
    def reconcile(self, request, pk=None):
        items_data = request.data.get('items', [])
        actual_user_count = request.data.get('actual_user_count')
        
        try:
            with transaction.atomic():
                mr = MaterialRequest.objects.select_for_update().get(pk=pk)
                if mr.status != 'ACCEPTED':
                    return Response({"error": "Only ACCEPTED requests can be reconciled"}, status=status.HTTP_400_BAD_REQUEST)
                
                if actual_user_count is not None:
                    mr.actual_user_count = int(actual_user_count)
                    
                for item_val in items_data:
                    item_id = item_val.get('id')
                    try:
                        returned_qty = Decimal(str(item_val.get('returned_quantity', 0)))
                    except (InvalidOperation, TypeError):
                        returned_qty = Decimal('0')
                    
                    mr_item = mr.items.get(id=item_id)
                    mr_item.quantity_returned = returned_qty
                    mr_item.save()
                
                mr.status = 'PENDING_RECONCILE'
                mr.save()
            return Response({"status": "Reconciliation proposed and pending store confirmation."})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def confirm_reconcile(self, request, pk=None):
        profile = getattr(request.user, 'profile', None)
        if not request.user.is_superuser and not (profile and profile.role and profile.role.can_manage_mr):
            return Response({"error": "You do not have permission to confirm reconciliations."}, status=status.HTTP_403_FORBIDDEN)
        
        try:
            with transaction.atomic():
                mr = MaterialRequest.objects.select_for_update().get(pk=pk)
                if mr.status != 'PENDING_RECONCILE':
                    return Response({"error": "Only PENDING_RECONCILE requests can be confirmed"}, status=status.HTTP_400_BAD_REQUEST)
                
                procurement_dept = Department.objects.get(code='procurement')
                kitchen_dept = Department.objects.get(code='kitchen')

                for mr_item in mr.items.all():
                    returned_qty = mr_item.quantity_returned

                    # 1. Get store inventory record (needed to update quantity/value on return)
                    store_inv, _ = Inventory.objects.get_or_create(
                        item=mr_item.item,
                        department=procurement_dept
                    )

                    # 2. Calculate and store final consumption costs
                    consumed_qty = mr_item.quantity_issued - returned_qty
                    
                    # Store original issued cost before return adjustments
                    original_issued_cost = mr_item.consumption_total_cost
                    
                    # 3. Add back returned quantity and value to Store (BATCH-SPECIFIC REVERSAL)
                    if returned_qty > 0:
                        # Decrease deficit if this was a return of a negative issuance
                        if mr_item.deficit_quantity > 0:
                            to_reduce_deficit = min(returned_qty, mr_item.deficit_quantity)
                            mr_item.deficit_quantity -= to_reduce_deficit
                            
                        remaining_to_return = returned_qty
                        # Get allocations in LIFO order (latest issued = first returned)
                        allocations = mr_item.batch_allocations.all().order_by('-id')
                        
                        for alloc in allocations:
                            if remaining_to_return <= 0:
                                break
                            
                            # How much can we return to this specific batch?
                            # Usually we can return up to what was allocated
                            can_return_to_batch = min(remaining_to_return, alloc.quantity_allocated)
                            
                            batch = alloc.stock_batch
                            batch_price = batch.unit_price
                            
                            # Restore to batch and inventory
                            batch.remaining_quantity += can_return_to_batch
                            batch.save()
                            
                            # Deduct from allocation so ledger 'total_issued' is accurate
                            original_alloc_qty = alloc.quantity_allocated
                            alloc.quantity_allocated -= can_return_to_batch
                            
                            returned_val = (can_return_to_batch * batch_price).quantize(Decimal('0.00'))
                            store_inv.inventory_value += returned_val
                            
                            # Deduct from MR item's consumption cost
                            mr_item.consumption_total_cost -= returned_val
                            
                            if original_alloc_qty == can_return_to_batch:
                                alloc.delete()
                            else:
                                alloc.save()
                                
                            remaining_to_return -= can_return_to_batch
                        
                        # Fallback: if returned_qty exceeds total recorded allocations (shouldn't happen)
                        if remaining_to_return > 0:
                            fallback_val = (remaining_to_return * store_inv.average_price).quantize(Decimal('0.00'))
                            store_inv.inventory_value += fallback_val
                            mr_item.consumption_total_cost -= fallback_val
                            
                        # Bulletproof safeguard: if returned completely, cost must be 0
                        if mr_item.quantity_returned >= mr_item.quantity_issued:
                            mr_item.consumption_total_cost = Decimal('0.00')
                        else:
                            mr_item.consumption_total_cost = max(Decimal('0.00'), mr_item.consumption_total_cost)
                        
                        store_inv.current_quantity += returned_qty
                        # Recalculate store average price
                        if store_inv.current_quantity > 0:
                            store_inv.average_price = (store_inv.inventory_value / store_inv.current_quantity).quantize(Decimal('0.00'))
                        elif store_inv.current_quantity == 0:
                            store_inv.average_price = Decimal('0.00')
                        
                        store_inv.save()
                        # original_issued_cost now only contains the consumed part? 
                        # No, wait. 3851: mr_item.consumption_total_cost -= returned_val
                        # So original_issued_cost (set at 3823) is actually the FINAL consumption cost?
                        # No, I set it at 3823 before the -= at 3851.
                        # Wait, let's re-read. 3734: mr_item.consumption_total_cost = total_cost (Original)
                        # So at 3823, mr_item.consumption_total_cost is the ORIGINAL cost.
                        # Then at 3851, it's reduced by returned_val.
                        # So original_issued_cost (captured at 3823) IS the original cost.
                        mr_item.save()

                    # 4. Log Movement (Returns)
                    if returned_qty > 0:
                        StockMovement.objects.create(
                            item=mr_item.item,
                            quantity=returned_qty,
                            type='KITCHEN_TO_STORE',
                            reference_id=mr.mr_number,
                            user=request.user,
                            remarks=f"Returned from Kitchen for MR: {mr.mr_number}"
                        )
                    
                    # 5. Deduct total issued from Kitchen (batch processed)
                    kitchen_inv, _ = Inventory.objects.get_or_create(
                        item=mr_item.item, 
                        department=kitchen_dept
                    )
                    kitchen_inv.current_quantity -= mr_item.quantity_issued
                    kitchen_inv.inventory_value -= original_issued_cost
                    if kitchen_inv.current_quantity <= 0:
                        kitchen_inv.inventory_value = max(Decimal('0.00'), kitchen_inv.inventory_value)
                        kitchen_inv.average_price = Decimal('0.00')
                    else:
                        kitchen_inv.average_price = (kitchen_inv.inventory_value / kitchen_inv.current_quantity).quantize(Decimal('0.00'))
                    kitchen_inv.save()
                
                mr.status = 'COMPLETED'
                mr.save()
            return Response({"status": "Material Request reconciled and completed"})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def update_headcount(self, request, pk=None):
        mr = self.get_object()
        
        profile = getattr(request.user, 'profile', None)
        if not request.user.is_superuser and not (profile and profile.role and profile.role.can_manage_mr):
            return Response({"error": "You do not have permission to update headcount."}, status=status.HTTP_403_FORBIDDEN)
            
        actual_user_count = request.data.get('actual_user_count')
        if actual_user_count is None:
            return Response({"error": "actual_user_count is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            mr.actual_user_count = int(actual_user_count)
            mr.save()
            return Response({"status": "Headcount updated successfully", "actual_user_count": mr.actual_user_count})
        except ValueError:
            return Response({"error": "Invalid headcount value"}, status=status.HTTP_400_BAD_REQUEST)

    def _revert_completed_mr_entries(self, mr):
        procurement_dept = Department.objects.get(code='procurement')

        StockMovement.objects.filter(
            Q(reference_id=mr.mr_number) | 
            Q(remarks__icontains=mr.mr_number)
        ).delete()

        for mr_item in mr.items.all():
            if mr_item.quantity_issued > 0:
                consumed_qty = mr_item.quantity_issued - mr_item.quantity_returned
                
                allocations = MRItemBatchAllocation.objects.filter(mr_item=mr_item)
                for alloc in allocations:
                    batch = alloc.stock_batch
                    batch.remaining_quantity += alloc.quantity_allocated
                    batch.save()
                    alloc.delete()

                store_inv, _ = Inventory.objects.get_or_create(item=mr_item.item, department=procurement_dept)
                store_inv.current_quantity += consumed_qty
                store_inv.inventory_value += mr_item.consumption_total_cost
                if store_inv.current_quantity > 0:
                    store_inv.average_price = (store_inv.inventory_value / store_inv.current_quantity).quantize(Decimal('0.00'))
                store_inv.save()

                mr_item.quantity_issued = Decimal('0.000')
                mr_item.quantity_returned = Decimal('0.000')
                mr_item.consumption_total_cost = Decimal('0.00')
                mr_item.consumption_unit_cost = Decimal('0.00')
                mr_item.deficit_quantity = mr_item.quantity
                mr_item.save()

    def _revert_mr_entries(self, mr):
        procurement_dept = Department.objects.get(code='procurement')
        kitchen_dept = Department.objects.get(code='kitchen')

        StockMovement.objects.filter(
            Q(reference_id=mr.mr_number) | 
            Q(remarks__icontains=mr.mr_number)
        ).delete()

        for mr_item in mr.items.all():
            if mr_item.quantity_issued > 0:
                allocations = MRItemBatchAllocation.objects.filter(mr_item=mr_item)
                for alloc in allocations:
                    batch = alloc.stock_batch
                    batch.remaining_quantity += alloc.quantity_allocated
                    batch.save()
                    alloc.delete()

                kitchen_inv, _ = Inventory.objects.get_or_create(item=mr_item.item, department=kitchen_dept)
                kitchen_inv.current_quantity -= mr_item.quantity_issued
                kitchen_inv.inventory_value -= mr_item.consumption_total_cost
                if kitchen_inv.current_quantity > 0:
                    kitchen_inv.average_price = (kitchen_inv.inventory_value / kitchen_inv.current_quantity).quantize(Decimal('0.00'))
                else:
                    kitchen_inv.average_price = Decimal('0.00')
                    kitchen_inv.inventory_value = max(Decimal('0.00'), kitchen_inv.inventory_value)
                kitchen_inv.save()

                store_inv, _ = Inventory.objects.get_or_create(item=mr_item.item, department=procurement_dept)
                store_inv.current_quantity += mr_item.quantity_issued
                store_inv.inventory_value += mr_item.consumption_total_cost
                if store_inv.current_quantity > 0:
                    store_inv.average_price = (store_inv.inventory_value / store_inv.current_quantity).quantize(Decimal('0.00'))
                store_inv.save()

                mr_item.quantity_issued = Decimal('0.000')
                mr_item.consumption_total_cost = Decimal('0.00')
                mr_item.consumption_unit_cost = Decimal('0.00')
                mr_item.deficit_quantity = mr_item.quantity
                mr_item.save()

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        mr = self.get_object()
        if mr.status not in ['PLACED', 'MGMT_APPROVED', 'ISSUED', 'ACCEPTED']:
            return Response({"error": "Only PLACED, MGMT_APPROVED, ISSUED or ACCEPTED requests can be rejected"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            with transaction.atomic():
                if mr.status in ['ISSUED', 'ACCEPTED']:
                    self._revert_mr_entries(mr)

                mr.status = 'REJECTED'
                mr.action_by = request.user
                mr.action_at = timezone.now()
                mr.save()
            return Response({"status": "Material Request rejected (stock restored if previously accepted)"})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def undo_action(self, request, pk=None):
        mr = self.get_object()
        if mr.status not in ['ISSUED', 'ACCEPTED', 'REJECTED', 'PENDING_RECONCILE', 'COMPLETED']:
            return Response({"error": "Can only undo ISSUED, ACCEPTED, REJECTED, PENDING_RECONCILE or COMPLETED requests"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            with transaction.atomic():
                if mr.status == 'COMPLETED':
                    self._revert_completed_mr_entries(mr)
                elif mr.status in ['ISSUED', 'ACCEPTED', 'PENDING_RECONCILE']:
                    self._revert_mr_entries(mr)

                if mr.mgmt_verified_at:
                    mr.status = 'MGMT_APPROVED'
                else:
                    mr.status = 'PLACED'
                    
                mr.action_by = None
                mr.action_at = None
                mr.save()
            return Response({"status": f"Action undone, request reverted to {mr.status} (stock restored if previously accepted)"})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def consumptions(self, request):
        """
        Calculates and returns a line-by-line consumption ledger based on 
        ACCEPTED or COMPLETED Material Requests.
        Consumed = quantity_issued - quantity_returned
        """
        try:
            # We filter by item or date if requested
            item_id = request.query_params.get('item_id')
            start_date = request.query_params.get('start_date')
            end_date = request.query_params.get('end_date')
            meal_type = request.query_params.get('meal_type')

            mrs = MaterialRequest.objects.filter(status__in=['ISSUED', 'ACCEPTED', 'COMPLETED']).order_by('-target_date')

            if start_date:
                mrs = mrs.filter(target_date__gte=start_date)
            if end_date:
                mrs = mrs.filter(target_date__lte=end_date)
            if meal_type:
                mrs = mrs.filter(meal_type=meal_type)

            results = []
            for mr in mrs:
                items_query = mr.items.filter(quantity_issued__gt=0)
                if item_id:
                    items_query = items_query.filter(item_id=item_id)
                
                for mr_item in items_query:
                    consumed = mr_item.quantity_issued - mr_item.quantity_returned
                    if consumed > 0 or mr.status == 'COMPLETED':
                        results.append({
                            'id': f"c_{mr_item.id}_{mr.id}",
                            'date': mr.target_date,
                            'meal_type': mr.meal_type,
                            'mr_number': mr.mr_number,
                            'mr_id': mr.id,
                            'item_id': mr_item.item.id,
                            'item_name': mr_item.item.name,
                            'unit': mr_item.unit,
                            'issued': mr_item.quantity_issued,
                            'returned': mr_item.quantity_returned,
                            'consumed': consumed,
                            'unit_cost': mr_item.consumption_unit_cost,
                            'total_cost': mr_item.consumption_total_cost,
                            'user_count': mr.actual_user_count if mr.actual_user_count is not None else mr.user_count,
                            'expense_per_person': (mr_item.consumption_total_cost / (mr.actual_user_count if mr.actual_user_count is not None else mr.user_count)) if (mr.actual_user_count if mr.actual_user_count is not None else mr.user_count) > 0 else 0,
                            'status': mr.status,
                        })
            # Calculate Summary (on full results)
            total_cost = sum(float(r['total_cost'] or 0) for r in results)
            unique_mrs = {r['mr_number']: int(r['user_count'] or 0) for r in results}
            total_people = sum(unique_mrs.values())

            # Pagination
            try:
                page = int(request.query_params.get('page', 1))
                page_size = request.query_params.get('page_size', '20')
                
                if str(page_size).lower() == 'all':
                    paginated_results = results
                    total_pages = 1
                else:
                    page_size = int(page_size)
                    start_idx = (page - 1) * page_size
                    end_idx = start_idx + page_size
                    paginated_results = results[start_idx:end_idx]
                    total_pages = (len(results) + page_size - 1) // page_size
            except (ValueError, TypeError):
                paginated_results = results
                page = 1
                total_pages = 1

            return Response({
                'count': len(results),
                'results': paginated_results,
                'total_pages': total_pages,
                'next': page < total_pages,
                'previous': page > 1,
                'summary': {
                    'total_cost': total_cost,
                    'total_people': total_people,
                    'avg_per_head': (total_cost / total_people) if total_people > 0 else 0
                }
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def export_consumptions_excel(self, request):
        """
        Exports the consumption ledger to Excel.
        """
        try:
            # Reusing the filter logic from the 'consumptions' method
            item_id = request.query_params.get('item_id')
            start_date = request.query_params.get('start_date')
            end_date = request.query_params.get('end_date')
            meal_type = request.query_params.get('meal_type')

            mrs = MaterialRequest.objects.filter(status__in=['ACCEPTED', 'COMPLETED']).order_by('-target_date')

            if start_date:
                mrs = mrs.filter(target_date__gte=start_date)
            if end_date:
                mrs = mrs.filter(target_date__lte=end_date)
            if meal_type:
                mrs = mrs.filter(meal_type=meal_type)

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Kitchen Consumptions"
            
            headers = ["Date", "Meal Type", "MR Number", "Material", "Total Issued", "Total Returned", "Net Consumed", "Unit", "Unit Cost", "Total Cost", "Count", "Expense/Person"]
            header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
            header_font = Font(color='FFFFFF', bold=True)
            
            for col_num, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_num, value=header)
                cell.fill = header_fill
                cell.font = header_font

            row_num = 2
            total_expenditure = Decimal('0')
            processed_mr_ids = set()
            total_people_served = 0

            for mr in mrs:
                items_query = mr.items.filter(quantity_issued__gt=0)
                if item_id:
                    items_query = items_query.filter(item_id=item_id)
                
                # People count should only be added once per MR
                current_mr_people = mr.actual_user_count if mr.actual_user_count is not None else mr.user_count
                
                has_items_in_report = False
                for mr_item in items_query:
                    consumed = mr_item.quantity_issued - mr_item.quantity_returned
                    if consumed > 0 or mr.status == 'COMPLETED':
                        has_items_in_report = True
                        ws.cell(row=row_num, column=1, value=mr.target_date)
                        ws.cell(row=row_num, column=2, value=mr.meal_type)
                        ws.cell(row=row_num, column=3, value=mr.mr_number)
                        ws.cell(row=row_num, column=4, value=mr_item.item.name)
                        ws.cell(row=row_num, column=5, value=float(mr_item.quantity_issued))
                        ws.cell(row=row_num, column=6, value=float(mr_item.quantity_returned))
                        ws.cell(row=row_num, column=7, value=float(consumed))
                        ws.cell(row=row_num, column=8, value=mr_item.unit)
                        ws.cell(row=row_num, column=9, value=float(mr_item.consumption_unit_cost))
                        ws.cell(row=row_num, column=10, value=float(mr_item.consumption_total_cost))
                        
                        ws.cell(row=row_num, column=11, value=current_mr_people)
                        exp_per_p = float(mr_item.consumption_total_cost / current_mr_people) if current_mr_people > 0 else 0
                        ws.cell(row=row_num, column=12, value=exp_per_p)
                        
                        total_expenditure += mr_item.consumption_total_cost
                        row_num += 1
                
                if has_items_in_report and mr.id not in processed_mr_ids:
                    total_people_served += current_mr_people
                    processed_mr_ids.add(mr.id)

            # Add Summary Block
            row_num += 1 # Add a blank row
            summary_start_row = row_num
            
            summary_labels = [
                ("TOTAL EXPENDITURE", f"₹ {float(total_expenditure):,.2f}"),
                ("TOTAL PEOPLE SERVED", total_people_served),
                ("AVG. EXPENSE / HEAD", f"₹ {float(total_expenditure / total_people_served if total_people_served > 0 else 0):,.2f}")
            ]
            
            summary_fill = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
            label_font = Font(bold=True)
            value_font = Font(bold=True, color='1F4E78')
            
            for i, (label, value) in enumerate(summary_labels):
                current_row = summary_start_row + i
                ws.cell(row=current_row, column=9, value=label).font = label_font
                val_cell = ws.cell(row=current_row, column=10, value=value)
                val_cell.font = value_font
                # Style across
                for col in range(9, 11):
                    ws.cell(row=current_row, column=col).fill = summary_fill

            # Adjust column widths
            for col in ws.columns:
                max_length = 0
                column = col[0].column_letter
                for cell in col:
                    try:
                        if cell.value and len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except: pass
                ws.column_dimensions[column].width = max_length + 2

            response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            response['Content-Disposition'] = f'attachment; filename=Kitchen_Consumption_{timezone.now().strftime("%Y%m%d")}.xlsx'
            wb.save(response)
            return response
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def monthly_consumptions(self, request):
        """
        Monthly Item-wise Kitchen Consumption Report.
        """
        try:
            months_count = request.query_params.get('months_count') # last 1, 3, 6, 12
            start_month = request.query_params.get('start_month') # e.g. "2026-04"
            
            end_date = timezone.now().date()
            if start_month:
                year, month = map(int, start_month.split('-'))
                # If start_month is provided, it's the END of our range
                # We go backward 'count' months
                count = int(months_count) if months_count else 1
                
                # End date is the last day of the selected start_month
                last_day = calendar.monthrange(year, month)[1]
                end_date = timezone.datetime(year, month, last_day).date()
                
                # Calculate start_date by going back (count - 1) months
                start_year = year
                start_month_num = month
                for _ in range(count - 1):
                    start_month_num -= 1
                    if start_month_num == 0:
                        start_month_num = 12
                        start_year -= 1
                start_date = timezone.datetime(start_year, start_month_num, 1).date()
            else:
                if months_count:
                    count = int(months_count)
                    # From current month back
                    start_year = end_date.year
                    start_month_num = end_date.month
                    for _ in range(count - 1):
                        start_month_num -= 1
                        if start_month_num == 0:
                            start_month_num = 12
                            start_year -= 1
                    start_date = timezone.datetime(start_year, start_month_num, 1).date()
                else:
                    # Default to current month
                    start_date = end_date.replace(day=1)

            mrs = MaterialRequest.objects.filter(
                status__in=['ACCEPTED', 'COMPLETED'],
                target_date__gte=start_date,
                target_date__lte=end_date
            ).prefetch_related('items', 'items__item')

            monthly_stats = {}
            for mr in mrs:
                m_key = mr.target_date.strftime('%Y-%m')
                if m_key not in monthly_stats:
                    monthly_stats[m_key] = {
                        'total_people': 0,
                        'processed_mrs': set(),
                        'items': {}
                    }
                
                if mr.id not in monthly_stats[m_key]['processed_mrs']:
                    people = mr.actual_user_count if mr.actual_user_count is not None else mr.user_count
                    monthly_stats[m_key]['total_people'] += (people or 0)
                    monthly_stats[m_key]['processed_mrs'].add(mr.id)
                
                for mr_item in mr.items.all():
                    item_id = mr_item.item.id
                    if item_id not in monthly_stats[m_key]['items']:
                        monthly_stats[m_key]['items'][item_id] = {
                            'name': mr_item.item.name,
                            'unit': mr_item.unit,
                            'net_consumed': Decimal('0'),
                            'total_cost': Decimal('0')
                        }
                    
                    consumed = mr_item.quantity_issued - mr_item.quantity_returned
                    monthly_stats[m_key]['items'][item_id]['net_consumed'] += consumed
                    monthly_stats[m_key]['items'][item_id]['total_cost'] += (mr_item.consumption_total_cost or Decimal('0'))

            results = []
            for m_key, data in monthly_stats.items():
                total_people = data['total_people']
                for item_id, item_data in data['items'].items():
                    total_cost = item_data['total_cost']
                    results.append({
                        'month': m_key,
                        'item_id': item_id,
                        'item_name': item_data['name'],
                        'unit': item_data['unit'],
                        'net_consumed': float(item_data['net_consumed']),
                        'total_cost': float(total_cost),
                        'total_people': total_people,
                        'expense_per_person': float(total_cost / total_people) if total_people > 0 else 0
                    })
            
            # Sort by month (desc) then item name
            results.sort(key=lambda x: (x['month'], x['item_name']), reverse=True)
            
            return Response(results, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def export_monthly_consumptions_excel(self, request):
        """
        Exports the monthly consumption report to Excel in a pivot format.
        """
        try:
            # Re-use logic to get data
            response_data = self.monthly_consumptions(request)
            if response_data.status_code != 200:
                return response_data
            
            results = response_data.data
            if not results:
                return Response({"error": "No data to export"}, status=status.HTTP_400_BAD_REQUEST)

            # Prepare Pivot Data
            months = sorted(list(set(r['month'] for r in results)))
            items_dict = {}
            for r in results:
                i_name = r['item_name']
                if i_name not in items_dict:
                    items_dict[i_name] = {
                        'unit': r['unit'],
                        'months': {},
                        'total_cost': 0
                    }
                items_dict[i_name]['months'][r['month']] = r
                items_dict[i_name]['total_cost'] += r['total_cost']

            sorted_item_names = sorted(items_dict.keys(), key=lambda x: items_dict[x]['total_cost'], reverse=True)

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Monthly Analysis"
            
            # Header Styles
            from openpyxl.styles import Border, Side
            ws.sheet_view.showGridLines = False

            thin_side = Side(border_style="thin", color="D4D4D4")
            item_border_top = Border(top=thin_side, left=thin_side, right=thin_side)
            item_border_mid = Border(left=thin_side, right=thin_side)
            item_border_bot = Border(bottom=thin_side, left=thin_side, right=thin_side)
            
            qty_font = Font(size=9, color='666666')
            rate_font = Font(size=8, color='0066CC')
            
            header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
            header_font = Font(color='FFFFFF', bold=True)
            header_align = Alignment(horizontal='center', vertical='center')
            
            # Header Row
            c1 = ws.cell(row=1, column=1, value="Material")
            c1.fill, c1.font, c1.alignment = header_fill, header_font, header_align
            
            c2 = ws.cell(row=1, column=2, value="UOM")
            c2.fill, c2.font, c2.alignment = header_fill, header_font, header_align
            
            for col_num, month in enumerate(months, 3):
                cell = ws.cell(row=1, column=col_num, value=month)
                cell.fill, cell.font, cell.alignment = header_fill, header_font, header_align
            
            c_tot = ws.cell(row=1, column=len(months) + 3, value="Grand Total")
            c_tot.fill, c_tot.font, c_tot.alignment = header_fill, header_font, header_align
            
            # Data Rows
            row_num = 2
            for i_name in sorted_item_names:
                item_data = items_dict[i_name]
                ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num+2, end_column=1)
                ws.merge_cells(start_row=row_num, start_column=2, end_row=row_num+2, end_column=2)
                
                ws.cell(row=row_num, column=1, value=i_name).font = Font(bold=True)
                ws.cell(row=row_num, column=1).alignment = Alignment(horizontal='left', vertical='center', wrapText=True)
                
                ws.cell(row=row_num, column=2, value=item_data['unit']).alignment = Alignment(horizontal='center', vertical='center')
                
                for col_num, month in enumerate(months, 3):
                    cell_data = item_data['months'].get(month)
                    
                    cell_amt = ws.cell(row=row_num, column=col_num)
                    cell_qty = ws.cell(row=row_num+1, column=col_num)
                    cell_rate = ws.cell(row=row_num+2, column=col_num)
                    
                    cell_amt.border = item_border_top
                    cell_qty.border = item_border_mid
                    cell_rate.border = item_border_bot
                    
                    cell_amt.alignment = Alignment(horizontal='center', vertical='bottom')
                    cell_qty.alignment = Alignment(horizontal='center', vertical='center')
                    cell_rate.alignment = Alignment(horizontal='center', vertical='top')

                    if cell_data:
                        cell_amt.value = cell_data['total_cost']
                        cell_amt.number_format = '₹#,##0'
                        cell_amt.font = Font(bold=True, color='2E7D32')
                        
                        cell_qty.value = cell_data['net_consumed']
                        cell_qty.number_format = f'0.00 "{item_data["unit"]}"'
                        cell_qty.font = qty_font
                        
                        cell_rate.value = cell_data['expense_per_person']
                        cell_rate.number_format = '₹#,##0.00"/head"'
                        cell_rate.font = rate_font
                
                # Grand Total for Item
                tot_amt = ws.cell(row=row_num, column=len(months) + 3, value=item_data['total_cost'])
                tot_amt.number_format = '₹#,##0'
                tot_amt.font = Font(bold=True, color='008000')
                tot_amt.alignment = Alignment(horizontal='center', vertical='bottom')
                tot_amt.border = item_border_top
                
                tot_qty = ws.cell(row=row_num+1, column=len(months) + 3)
                tot_qty.border = item_border_mid
                
                tot_rate = ws.cell(row=row_num+2, column=len(months) + 3)
                tot_rate.border = item_border_bot
                
                for r in range(row_num, row_num+3):
                    for c in (1, 2):
                        b = item_border_mid
                        if r == row_num: b = item_border_top
                        elif r == row_num+2: b = item_border_bot
                        ws.cell(row=r, column=c).border = b
                        
                ws.row_dimensions[row_num].height = 20
                ws.row_dimensions[row_num+1].height = 15
                ws.row_dimensions[row_num+2].height = 15
                
                row_num += 3

            # Adjust Column Widths
            ws.column_dimensions['A'].width = 30
            ws.column_dimensions['B'].width = 10
            for i in range(3, len(months) + 4):
                ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 20

            response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            response['Content-Disposition'] = f'attachment; filename=Monthly_Kitchen_Consumption_{timezone.now().strftime("%Y%m%d")}.xlsx'
            wb.save(response)
            return response
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], url_path='export_excel')
    def export_excel(self, request):
        try:
            import openpyxl
            from openpyxl.styles import Font
            from django.http import HttpResponse
            
            queryset = self.filter_queryset(self.get_queryset())
            
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Kitchen MRs"
            
            headers = [
                'MR Number', 'Order Date', 'Target Date', 'Meal Type',
                'Status', 'Headcount (Target)', 'Headcount (Actual)',
                'Created By', 'Created At', 'Item Name', 'Requested Qty',
                'Issued Qty', 'Returned Qty'
            ]
            
            for col_num, header_title in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_num, value=header_title)
                cell.font = Font(bold=True)
                
            row_num = 2
            for mr in queryset:
                items = mr.items.all()
                if items.exists():
                    for item in items:
                        ws.cell(row=row_num, column=1, value=mr.mr_number)
                        ws.cell(row=row_num, column=2, value=mr.order_date.strftime('%Y-%m-%d') if mr.order_date else '')
                        ws.cell(row=row_num, column=3, value=mr.target_date.strftime('%Y-%m-%d') if mr.target_date else '')
                        ws.cell(row=row_num, column=4, value=mr.meal_type or '')
                        ws.cell(row=row_num, column=5, value=mr.get_status_display() or mr.status)
                        ws.cell(row=row_num, column=6, value=mr.user_count)
                        ws.cell(row=row_num, column=7, value=mr.actual_user_count)
                        ws.cell(row=row_num, column=8, value=mr.requested_by.username if mr.requested_by else '')
                        ws.cell(row=row_num, column=9, value=mr.created_at.strftime('%Y-%m-%d %H:%M') if mr.created_at else '')
                        ws.cell(row=row_num, column=10, value=item.item.name if item.item else '')
                        ws.cell(row=row_num, column=11, value=float(item.quantity) if item.quantity else 0)
                        ws.cell(row=row_num, column=12, value=float(item.quantity_issued) if item.quantity_issued else 0)
                        ws.cell(row=row_num, column=13, value=float(item.quantity_returned) if item.quantity_returned else 0)
                        row_num += 1
                else:
                    ws.cell(row=row_num, column=1, value=mr.mr_number)
                    ws.cell(row=row_num, column=2, value=mr.order_date.strftime('%Y-%m-%d') if mr.order_date else '')
                    ws.cell(row=row_num, column=3, value=mr.target_date.strftime('%Y-%m-%d') if mr.target_date else '')
                    ws.cell(row=row_num, column=4, value=mr.meal_type or '')
                    ws.cell(row=row_num, column=5, value=mr.get_status_display() or mr.status)
                    ws.cell(row=row_num, column=6, value=mr.user_count)
                    ws.cell(row=row_num, column=7, value=mr.actual_user_count)
                    ws.cell(row=row_num, column=8, value=mr.requested_by.username if mr.requested_by else '')
                    ws.cell(row=row_num, column=9, value=mr.created_at.strftime('%Y-%m-%d %H:%M') if mr.created_at else '')
                    row_num += 1
                
            response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            response['Content-Disposition'] = 'attachment; filename=Kitchen_MRs_Export.xlsx'
            wb.save(response)
            return response
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class StockMovementViewSet(viewsets.ModelViewSet):
    queryset = StockMovement.objects.all().order_by('-movement_date')
    serializer_class = StockMovementSerializer
    pagination_class = PRPagination

    def get_queryset(self):
        qs = StockMovement.objects.all().order_by('-movement_date')
        item_id = self.request.query_params.get('item')
        mtype = self.request.query_params.get('type')
        start = self.request.query_params.get('start_date')
        end = self.request.query_params.get('end_date')
        if item_id:
            qs = qs.filter(item_id=item_id)
        if mtype:
            qs = qs.filter(type=mtype)
        if start:
            qs = qs.filter(movement_date__date__gte=start)
        if end:
            qs = qs.filter(movement_date__date__lte=end)
        return qs

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'export_excel']:
            self.permission_classes = [CanViewReports]
        else:
            self.permission_classes = [
                permissions.IsAdminUser
            ]
        return super().get_permissions()

    @action(detail=False, methods=['get'])
    def export_excel(self, request):
        queryset = self.get_queryset().select_related('item', 'user')
        wb = openpyxl.Workbook()
        
        from .models import StockBatch
        all_batches = StockBatch.objects.select_related('procurement__purchase_request').all()
        batch_dict = {b.id: b for b in all_batches}
        latest_batch_dict = {}
        for b in sorted(all_batches, key=lambda x: (x.created_at if x.created_at else x.batch_date)):
            latest_batch_dict[b.item_id] = b.unit_price
            
        # --- Sheet 1: Purchase Data ---
        ws_purchase = wb.active
        ws_purchase.title = "Purchase Data"
        
        from decimal import Decimal
        from django.db.models import Sum
        from .models import Item, Procurement, MaterialRequestItem
        
        purchase_headers = [
            'Item Name', 'PR Number', 'Batch IDs', 'Order Date', 'Purchase Date', 'Vendor Name', 'Purchase Type', 'Purchased Qty', 
            'Unit Price (Base)', 'Tax Rate (%)', 'Discount Value', 'Unit Price (Incl. Tax)', 'Total PR Value (Incl Tax)',
            'Tax Paid', 'Pushed to Kitchen (Net)', 'Kitchen Pushed Value', 'Vendor Returned Qty', 'Vendor Returned Value', 'Adjusted / Wasted Qty', 'Adjusted / Wasted Value', 'Remaining on Procurement', 'Remaining Value', 'Action By (Received/Closed)'
        ]
        
        header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        
        for col_num, header in enumerate(purchase_headers, 1):
            cell = ws_purchase.cell(row=7, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font
            
        item_id = request.query_params.get('item')
        items = Item.objects.all().order_by('name')
        if item_id:
            items = items.filter(id=item_id)
            
        start_date = request.query_params.get('start_date') or '2026-04-30'
        end_date = request.query_params.get('end_date')
        
        grand_total_pr_value = Decimal('0.00')
        grand_total_remaining_value = Decimal('0.00')
        grand_total_adjusted_value = Decimal('0.00')
        grand_total_pushed_value = Decimal('0.00')
        grand_total_vendor_returned_value = Decimal('0.00')
        
        row_num = 8
        item_summaries = {}
        
        procs_qs = Procurement.objects.filter(purchase_date__gte=start_date).order_by('item__name', 'purchase_date', 'created_at')
        if item_id:
            procs_qs = procs_qs.filter(item_id=item_id)
        if end_date:
            procs_qs = procs_qs.filter(purchase_date__lte=end_date)
            
        procs_qs = procs_qs.select_related(
            'item', 'vendor', 'purchase_request', 'receipt',
            'purchase_request__closed_by', 'purchase_request__approved_by_l1', 'purchase_request__approved_by_l2'
        ).prefetch_related(
            'stock_batches', 'stock_batches__allocations'
        )
        
        for p in procs_qs:
            item = p.item
            bought_qty = p.quantity or Decimal('0.000')
            unit_price = p.unit_price or Decimal('0.00')
            total_price = p.total_price or Decimal('0.00')
            
            base_cost = bought_qty * unit_price
            tax_paid = max(Decimal('0.00'), total_price - base_cost)
            
            remaining_in_stock = sum([batch.remaining_quantity for batch in p.stock_batches.all()]) or Decimal('0.000')
            pushed_from_this_pr = sum([alloc.quantity_allocated for batch in p.stock_batches.all() for alloc in batch.allocations.all()]) or Decimal('0.000')
            
            vendor_returned_qty = Decimal('0.000')
            if p.purchase_request:
                from .models import VendorReturnItem
                batch_ids = [batch.id for batch in p.stock_batches.all()]
                if batch_ids:
                    vendor_returned_qty = VendorReturnItem.objects.filter(
                        vendor_return__purchase_request=p.purchase_request,
                        batch_id__in=batch_ids,
                        item=p.item
                    ).aggregate(s=Sum('quantity'))['s'] or Decimal('0.000')
                
            adjusted_wasted = bought_qty - remaining_in_stock - pushed_from_this_pr - vendor_returned_qty
            
            pr_number = p.purchase_request.pr_reference if p.purchase_request else 'Direct Purchase'
            avg_unit_incl_tax = total_price / bought_qty if bought_qty > 0 else Decimal('0.00')
            remaining_value = remaining_in_stock * avg_unit_incl_tax
            adjusted_value = adjusted_wasted * avg_unit_incl_tax
            pushed_value = pushed_from_this_pr * avg_unit_incl_tax
            vendor_returned_value = vendor_returned_qty * avg_unit_incl_tax
            
            vendor_name = p.vendor.name if p.vendor else "N/A"
            purchase_type = p.purchase_type or "N/A"
            tax_rate_str = f"{p.tax_rate}%" if p.tax_rate else "0%"
            discount_val_str = f"{float(p.discount_value)} ({p.discount_type})" if getattr(p, 'discount_value', None) else "None"
            batch_details = ", ".join([f"Batch {batch.id}" for batch in p.stock_batches.all()])
            
            ws_purchase.cell(row=row_num, column=1, value=item.name)
            ws_purchase.cell(row=row_num, column=2, value=pr_number)
            
            od = p.purchase_request.order_date if p.purchase_request else None
            pd = p.receipt.purchase_date if p.receipt else getattr(p, 'purchase_date', None)
            
            ws_purchase.cell(row=row_num, column=3, value=batch_details)
            c4 = ws_purchase.cell(row=row_num, column=4, value=od.date() if hasattr(od, 'date') else od)
            c4.number_format = 'yyyy-mm-dd'
            c5 = ws_purchase.cell(row=row_num, column=5, value=pd.date() if hasattr(pd, 'date') else pd)
            c5.number_format = 'yyyy-mm-dd'
            
            ws_purchase.cell(row=row_num, column=6, value=vendor_name)
            ws_purchase.cell(row=row_num, column=7, value=purchase_type)
            c8 = ws_purchase.cell(row=row_num, column=8, value=float(bought_qty)); c8.number_format = '#,##0.00'
            c9 = ws_purchase.cell(row=row_num, column=9, value=float(unit_price)); c9.number_format = '#,##0.00'
            ws_purchase.cell(row=row_num, column=10, value=tax_rate_str)
            ws_purchase.cell(row=row_num, column=11, value=discount_val_str)
            c12 = ws_purchase.cell(row=row_num, column=12, value=float(avg_unit_incl_tax)); c12.number_format = '#,##0.00'
            c13 = ws_purchase.cell(row=row_num, column=13, value=float(total_price)); c13.number_format = '#,##0.00'
            c14 = ws_purchase.cell(row=row_num, column=14, value=float(tax_paid)); c14.number_format = '#,##0.00'
            c15 = ws_purchase.cell(row=row_num, column=15, value=float(pushed_from_this_pr)); c15.number_format = '#,##0.00'
            c16 = ws_purchase.cell(row=row_num, column=16, value=float(pushed_value)); c16.number_format = '#,##0.00'
            c17 = ws_purchase.cell(row=row_num, column=17, value=float(vendor_returned_qty)); c17.number_format = '#,##0.00'
            c18 = ws_purchase.cell(row=row_num, column=18, value=float(vendor_returned_value)); c18.number_format = '#,##0.00'
            c19 = ws_purchase.cell(row=row_num, column=19, value=float(adjusted_wasted)); c19.number_format = '#,##0.00'
            c20 = ws_purchase.cell(row=row_num, column=20, value=float(adjusted_value)); c20.number_format = '#,##0.00'
            c21 = ws_purchase.cell(row=row_num, column=21, value=float(remaining_in_stock)); c21.number_format = '#,##0.00'
            c22 = ws_purchase.cell(row=row_num, column=22, value=float(remaining_value)); c22.number_format = '#,##0.00'
            
            creator_name = 'N/A'
            if p.purchase_request:
                from .models import StockMovement
                sm = StockMovement.objects.filter(
                    reference_id=p.purchase_request.pr_reference,
                    type='STORE_INWARD'
                ).order_by('-movement_date').first()
                if sm and sm.user:
                    creator_name = sm.user.username
                else:
                    creator_name = (p.purchase_request.closed_by.username if p.purchase_request.closed_by else None) or \
                                   (p.purchase_request.approved_by_l1.username if p.purchase_request.approved_by_l1 else None) or \
                                   (p.purchase_request.approved_by_l2.username if p.purchase_request.approved_by_l2 else None)
                                   
            ws_purchase.cell(row=row_num, column=23, value=creator_name or 'N/A')
            
            grand_total_pr_value += total_price
            grand_total_remaining_value += remaining_value
            grand_total_adjusted_value += adjusted_value
            grand_total_pushed_value += pushed_value
            grand_total_vendor_returned_value += vendor_returned_value
            
            if item.name not in item_summaries:
                item_summaries[item.name] = {
                    'purchased_qty': Decimal('0.00'), 'purchased_val': Decimal('0.00'),
                    'pushed_qty': Decimal('0.00'), 'pushed_val': Decimal('0.00'),
                    'adjusted_qty': Decimal('0.00'), 'adjusted_val': Decimal('0.00'),
                    'returned_qty': Decimal('0.00'), 'returned_val': Decimal('0.00'),
                    'remaining_qty': Decimal('0.00'), 'remaining_val': Decimal('0.00'),
                }
            item_summaries[item.name]['purchased_qty'] += bought_qty
            item_summaries[item.name]['purchased_val'] += total_price
            item_summaries[item.name]['pushed_qty'] += pushed_from_this_pr
            item_summaries[item.name]['pushed_val'] += pushed_value
            item_summaries[item.name]['adjusted_qty'] += adjusted_wasted
            item_summaries[item.name]['adjusted_val'] += adjusted_value
            item_summaries[item.name]['returned_qty'] += vendor_returned_qty
            item_summaries[item.name]['returned_val'] += vendor_returned_value
            item_summaries[item.name]['remaining_qty'] += remaining_in_stock
            item_summaries[item.name]['remaining_val'] += remaining_value
            
            row_num += 1
            
        # --- Append Unfulfilled/Cancelled PRs to avoid audit gaps ---
        from .models import PurchaseRequest
        unfulfilled_prs = PurchaseRequest.objects.filter(
            status__in=['COMPLETED', 'CANCELLED', 'REJECTED'],
            procurements__isnull=True
        ).distinct()
        
        if start_date:
            unfulfilled_prs = unfulfilled_prs.filter(order_date__gte=start_date)
        if end_date:
            unfulfilled_prs = unfulfilled_prs.filter(order_date__lte=end_date)
            
        for pr in unfulfilled_prs:
            for pr_item in pr.items.all():
                if item_id and pr_item.item_id != int(item_id):
                    continue
                    
                ws_purchase.cell(row=row_num, column=1, value=pr_item.item.name)
                ws_purchase.cell(row=row_num, column=2, value=f"{pr.pr_reference} (UNFULFILLED/CLOSED)")
                
                od = pr.order_date
                ws_purchase.cell(row=row_num, column=3, value="")
                c4 = ws_purchase.cell(row=row_num, column=4, value=od.date() if hasattr(od, 'date') else od)
                c4.number_format = 'yyyy-mm-dd'
                c5 = ws_purchase.cell(row=row_num, column=5, value=None)
                c5.number_format = 'yyyy-mm-dd'
                
                ws_purchase.cell(row=row_num, column=6, value=pr.vendor.name if pr.vendor else "N/A")
                ws_purchase.cell(row=row_num, column=7, value=pr.purchase_type or "N/A")
                c8 = ws_purchase.cell(row=row_num, column=8, value=0.0); c8.number_format = '#,##0.00'
                c9 = ws_purchase.cell(row=row_num, column=9, value=0.0); c9.number_format = '#,##0.00'
                ws_purchase.cell(row=row_num, column=10, value="0%")
                ws_purchase.cell(row=row_num, column=11, value="None")
                c12 = ws_purchase.cell(row=row_num, column=12, value=0.0); c12.number_format = '#,##0.00'
                c13 = ws_purchase.cell(row=row_num, column=13, value=0.0); c13.number_format = '#,##0.00'
                c14 = ws_purchase.cell(row=row_num, column=14, value=0.0); c14.number_format = '#,##0.00'
                c15 = ws_purchase.cell(row=row_num, column=15, value=0.0); c15.number_format = '#,##0.00'
                c16 = ws_purchase.cell(row=row_num, column=16, value=0.0); c16.number_format = '#,##0.00'
                c17 = ws_purchase.cell(row=row_num, column=17, value=0.0); c17.number_format = '#,##0.00'
                c18 = ws_purchase.cell(row=row_num, column=18, value=0.0); c18.number_format = '#,##0.00'
                c19 = ws_purchase.cell(row=row_num, column=19, value=0.0); c19.number_format = '#,##0.00'
                c20 = ws_purchase.cell(row=row_num, column=20, value=0.0); c20.number_format = '#,##0.00'
                c21 = ws_purchase.cell(row=row_num, column=21, value=0.0); c21.number_format = '#,##0.00'
                c22 = ws_purchase.cell(row=row_num, column=22, value=0.0); c22.number_format = '#,##0.00'
                
                pr_auth = getattr(pr, 'authorized_by', None) or \
                          (pr.closed_by.username if pr.closed_by else None) or \
                          (pr.approved_by_l1.username if pr.approved_by_l1 else None) or \
                          (pr.approved_by_l2.username if pr.approved_by_l2 else None)
                ws_purchase.cell(row=row_num, column=23, value=f"{pr_auth or 'N/A'} (Approver/Closer)")
                
                row_num += 1
        # Summary Card
        ws_purchase.merge_cells('B2:G2')
        card_title = ws_purchase.cell(row=2, column=2, value="PURCHASE DATA SUMMARY")
        card_title.font = Font(color='FFFFFF', bold=True, size=14)
        card_title.fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        card_title.alignment = Alignment(horizontal='center', vertical='center')
        
        sum_headers = ["Gross Spent", "Total PR Value", "Kitchen Pushed Value", "Vendor Returned Value", "Adjusted/Wasted Value", "Remaining Value"]
        sum_vals = [grand_total_pr_value - grand_total_vendor_returned_value, grand_total_pr_value, grand_total_pushed_value, grand_total_vendor_returned_value, grand_total_adjusted_value, grand_total_remaining_value]
        
        sum_header_fill = PatternFill(start_color='D9E1F2', end_color='D9E1F2', fill_type='solid')
        thin_border = openpyxl.styles.borders.Border(
            left=openpyxl.styles.borders.Side(style='thin'), right=openpyxl.styles.borders.Side(style='thin'), 
            top=openpyxl.styles.borders.Side(style='thin'), bottom=openpyxl.styles.borders.Side(style='thin')
        )
        
        for i, (sh, sv) in enumerate(zip(sum_headers, sum_vals), 2):
            h_cell = ws_purchase.cell(row=3, column=i, value=sh)
            h_cell.font = Font(bold=True); h_cell.fill = sum_header_fill
            h_cell.alignment = Alignment(horizontal='center'); h_cell.border = thin_border
            
            v_cell = ws_purchase.cell(row=4, column=i, value=float(sv))
            v_cell.font = Font(bold=True, size=12); v_cell.number_format = '#,##0.00'
            v_cell.alignment = Alignment(horizontal='center'); v_cell.border = thin_border
                
        for col_idx, col in enumerate(ws_purchase.columns, 1):
            max_length = 0
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            for cell in col:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws_purchase.column_dimensions[column_letter].width = max_length + 2
            
        ws_purchase.auto_filter.ref = f"A7:{openpyxl.utils.get_column_letter(ws_purchase.max_column)}{ws_purchase.max_row}"

        # --- Sheet 1.5: Item Summary ---
        ws_item_summary = wb.create_sheet("Item Summary")
        summary_headers = [
            'Item Name', 'Purchased Qty', 'Purchased Value', 'Pushed Qty', 'Pushed Value', 
            'Adjusted/Wasted Qty', 'Adjusted/Wasted Value', 'Returned Qty', 'Returned Value', 
            'Remaining Qty', 'Remaining Value'
        ]
        for col_num, header in enumerate(summary_headers, 1):
            cell = ws_item_summary.cell(row=1, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font
            
        for row_idx, (item_name, data) in enumerate(sorted(item_summaries.items()), 2):
            ws_item_summary.cell(row=row_idx, column=1, value=item_name)
            ws_item_summary.cell(row=row_idx, column=2, value=float(data['purchased_qty'])).number_format = '#,##0.00'
            ws_item_summary.cell(row=row_idx, column=3, value=float(data['purchased_val'])).number_format = '#,##0.00'
            ws_item_summary.cell(row=row_idx, column=4, value=float(data['pushed_qty'])).number_format = '#,##0.00'
            ws_item_summary.cell(row=row_idx, column=5, value=float(data['pushed_val'])).number_format = '#,##0.00'
            ws_item_summary.cell(row=row_idx, column=6, value=float(data['adjusted_qty'])).number_format = '#,##0.00'
            ws_item_summary.cell(row=row_idx, column=7, value=float(data['adjusted_val'])).number_format = '#,##0.00'
            ws_item_summary.cell(row=row_idx, column=8, value=float(data['returned_qty'])).number_format = '#,##0.00'
            ws_item_summary.cell(row=row_idx, column=9, value=float(data['returned_val'])).number_format = '#,##0.00'
            ws_item_summary.cell(row=row_idx, column=10, value=float(data['remaining_qty'])).number_format = '#,##0.00'
            ws_item_summary.cell(row=row_idx, column=11, value=float(data['remaining_val'])).number_format = '#,##0.00'
            
        for col_idx, col in enumerate(ws_item_summary.columns, 1):
            ws_item_summary.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = 20
        ws_item_summary.auto_filter.ref = f"A1:K{len(item_summaries)+1}"

        # --- Sheet 2: Material Request Data ---
        ws_mr = wb.create_sheet("Material Request Data")
        
        mr_headers = [
            'Item Name', 'MR Number', 'Target Date', 'Status', 'Person Count',
            'Requested Qty', 'Issued Qty', 'Returned Qty', 'Consumed Qty', 'Consumption Cost',
            'Per Head Cost', 'Pushed From PR', 'Batch IDs'
        ]
        
        for col_num, header in enumerate(mr_headers, 1):
            cell = ws_mr.cell(row=7, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font
            
        row_num = 8
        grand_total_consumption_cost = Decimal('0.00')
        unique_mrs = {}
        for item in items:
            mris_qs = MaterialRequestItem.objects.filter(item=item, material_request__order_date__gte=start_date).prefetch_related('batch_allocations__stock_batch__procurement__purchase_request').order_by('material_request__target_date')
            if end_date:
                mris_qs = mris_qs.filter(material_request__order_date__lte=end_date)
                
            mris = list(mris_qs)
            if not mris:
                continue
                
            for mr_item in mris:
                mr = mr_item.material_request
                req_qty = mr_item.quantity or Decimal('0.00')
                iss_qty = mr_item.quantity_issued or Decimal('0.00')
                ret_qty = mr_item.quantity_returned or Decimal('0.00')
                cons_qty = iss_qty - ret_qty
                cost = mr_item.consumption_total_cost or Decimal('0.00')
                if cons_qty <= 0:
                    cost = Decimal('0.00')
                grand_total_consumption_cost += cost
                
                person_count = mr.actual_user_count if mr.actual_user_count else mr.user_count
                if cons_qty <= 0:
                    person_count = 0
                unique_mrs[mr.id] = person_count
                
                pr_sources = []
                batch_sources = []
                for alloc in mr_item.batch_allocations.all():
                    sb = alloc.stock_batch
                    if sb.procurement and sb.procurement.purchase_request:
                        pr_sources.append(sb.procurement.purchase_request.pr_reference)
                        batch_sources.append(f"Batch {sb.id}")
                    elif sb.procurement:
                        pr_sources.append("Direct Purchase")
                        batch_sources.append(f"Batch {sb.id}")
                    else:
                        pr_sources.append("Opening Stock")
                        batch_sources.append(f"Batch {sb.id}")
                pr_sources_str = ", ".join(list(set(pr_sources)))
                batch_sources_str = ", ".join(list(set(batch_sources)))
                
                ws_mr.cell(row=row_num, column=1, value=item.name)
                ws_mr.cell(row=row_num, column=2, value=mr.mr_number)
                ws_mr.cell(row=row_num, column=3, value=mr.target_date.strftime('%Y-%m-%d') if mr.target_date else "")
                ws_mr.cell(row=row_num, column=4, value=mr.status)
                ws_mr.cell(row=row_num, column=5, value=person_count)
                c6 = ws_mr.cell(row=row_num, column=6, value=float(req_qty)); c6.number_format = '#,##0.00'
                c7 = ws_mr.cell(row=row_num, column=7, value=float(iss_qty)); c7.number_format = '#,##0.00'
                c8 = ws_mr.cell(row=row_num, column=8, value=float(ret_qty)); c8.number_format = '#,##0.00'
                c9 = ws_mr.cell(row=row_num, column=9, value=float(cons_qty)); c9.number_format = '#,##0.00'
                c10 = ws_mr.cell(row=row_num, column=10, value=float(cost)); c10.number_format = '#,##0.00'
                
                per_head = float(cost) / person_count if person_count and person_count > 0 else 0.0
                c11 = ws_mr.cell(row=row_num, column=11, value=per_head); c11.number_format = '#,##0.00'
                
                ws_mr.cell(row=row_num, column=12, value=pr_sources_str)
                ws_mr.cell(row=row_num, column=13, value=batch_sources_str)
                row_num += 1
                
        # Summary Card
        ws_mr.merge_cells('B2:D2')
        card_title = ws_mr.cell(row=2, column=2, value="MATERIAL REQUEST SUMMARY")
        card_title.font = Font(color='FFFFFF', bold=True, size=14)
        card_title.fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        card_title.alignment = Alignment(horizontal='center', vertical='center')
        
        total_person_count = sum(unique_mrs.values())
        overall_per_head = float(grand_total_consumption_cost) / total_person_count if total_person_count > 0 else 0.0
        
        sum_headers = ["Total Consumption Cost", "Total Person Count", "Overall Per Head Cost"]
        sum_vals = [grand_total_consumption_cost, total_person_count, overall_per_head]
        
        sum_header_fill = PatternFill(start_color='D9E1F2', end_color='D9E1F2', fill_type='solid')
        thin_border = openpyxl.styles.borders.Border(
            left=openpyxl.styles.borders.Side(style='thin'), right=openpyxl.styles.borders.Side(style='thin'), 
            top=openpyxl.styles.borders.Side(style='thin'), bottom=openpyxl.styles.borders.Side(style='thin')
        )
        
        for i, (sh, sv) in enumerate(zip(sum_headers, sum_vals), 2):
            h_cell = ws_mr.cell(row=3, column=i, value=sh)
            h_cell.font = Font(bold=True); h_cell.fill = sum_header_fill
            h_cell.alignment = Alignment(horizontal='center'); h_cell.border = thin_border
            
            v_cell = ws_mr.cell(row=4, column=i, value=float(sv))
            v_cell.font = Font(bold=True, size=12); v_cell.number_format = '#,##0.00'
            v_cell.alignment = Alignment(horizontal='center'); v_cell.border = thin_border
                
        for col_idx, col in enumerate(ws_mr.columns, 1):
            max_length = 0
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            for cell in col:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws_mr.column_dimensions[column_letter].width = max_length + 2

        ws_mr.auto_filter.ref = f"A7:{openpyxl.utils.get_column_letter(ws_mr.max_column)}{ws_mr.max_row}"

        # --- Sheet 3: Adjustment Data ---
        ws_adj = wb.create_sheet("Adjustment Data")
        
        adj_headers = [
            'Date', 'Item Name', 'Quantity', 'UOM', 'Unit Price (with Tax)', 'Adjustment Value', 'Adjustment Type', 
            'Batch Details (From which PR/Batch)', 'Remarks', 'User'
        ]
        
        for col_num, header in enumerate(adj_headers, 1):
            cell = ws_adj.cell(row=7, column=col_num, value=header)
            cell.fill, cell.font = header_fill, header_font
            
        adj_qs = queryset.filter(type__in=['WASTAGE', 'ADJUSTMENT', 'BREAKAGE', 'EXPIRED', 'AUDIT'])
        
        row_num = 8
        grand_total_adj_value = Decimal('0.00')
        grand_total_adj_count = 0
        
        for adj in adj_qs:
            batch_details = adj.reference_id or "FIFO/Legacy"
            adj_unit_cost = Decimal('0.00')
            
            if "Batch" in batch_details:
                try:
                    import re
                    batch_ids = [int(x) for x in re.findall(r'Batch (\d+)', batch_details)]
                    if batch_ids:
                        from .models import StockBatch
                        sbs = [batch_dict[bid] for bid in batch_ids if bid in batch_dict]
                        if sbs:
                            adj_unit_cost = sum(sb.unit_price for sb in sbs) / len(sbs)
                            batch_details_list = []
                            for sb in sbs:
                                if sb.procurement and sb.procurement.purchase_request:
                                    pr_ref = sb.procurement.purchase_request.pr_reference
                                elif sb.procurement:
                                    pr_ref = "Direct Purchase"
                                else:
                                    pr_ref = "Opening Stock"
                                batch_details_list.append(f"{pr_ref} (Batch {sb.id})")
                            batch_details = ", ".join(batch_details_list)
                except:
                    pass
            
            if adj_unit_cost == Decimal('0.00'):
                adj_unit_cost = latest_batch_dict.get(adj.item_id, Decimal('0.00'))
            
            adj_value = Decimal(adj.quantity) * Decimal(adj_unit_cost)
            grand_total_adj_value += adj_value
            grand_total_adj_count += 1
            
            ws_adj.cell(row=row_num, column=1, value=adj.movement_date.strftime('%Y-%m-%d %H:%M:%S'))
            ws_adj.cell(row=row_num, column=2, value=adj.item.name)
            
            c3 = ws_adj.cell(row=row_num, column=3, value=float(adj.quantity))
            c3.number_format = '#,##0.00'
            
            ws_adj.cell(row=row_num, column=4, value=adj.item.unit)
            
            c5 = ws_adj.cell(row=row_num, column=5, value=float(adj_unit_cost))
            c5.number_format = '#,##0.00'
            
            c6 = ws_adj.cell(row=row_num, column=6, value=float(adj_value))
            c6.number_format = '#,##0.00'
            
            from .models import StockMovement
            movement_types = dict(StockMovement.MOVEMENT_TYPES)
            ws_adj.cell(row=row_num, column=7, value=movement_types.get(adj.type, adj.type))
            ws_adj.cell(row=row_num, column=8, value=batch_details)
            ws_adj.cell(row=row_num, column=9, value=adj.remarks or "")
            ws_adj.cell(row=row_num, column=10, value=adj.user.username if adj.user else "System")
            
            row_num += 1

        # Summary Card
        ws_adj.merge_cells('B2:C2')
        card_title = ws_adj.cell(row=2, column=2, value="ADJUSTMENT SUMMARY")
        card_title.font = Font(color='FFFFFF', bold=True, size=14)
        card_title.fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        card_title.alignment = Alignment(horizontal='center', vertical='center')
        
        sum_headers = ["Total Adjustment Value", "Total Adjustment Count"]
        sum_vals = [grand_total_adj_value, grand_total_adj_count]
        
        sum_header_fill = PatternFill(start_color='D9E1F2', end_color='D9E1F2', fill_type='solid')
        thin_border = openpyxl.styles.borders.Border(
            left=openpyxl.styles.borders.Side(style='thin'), right=openpyxl.styles.borders.Side(style='thin'), 
            top=openpyxl.styles.borders.Side(style='thin'), bottom=openpyxl.styles.borders.Side(style='thin')
        )
        
        for i, (sh, sv) in enumerate(zip(sum_headers, sum_vals), 2):
            h_cell = ws_adj.cell(row=3, column=i, value=sh)
            h_cell.font = Font(bold=True); h_cell.fill = sum_header_fill
            h_cell.alignment = Alignment(horizontal='center'); h_cell.border = thin_border
            
            v_cell = ws_adj.cell(row=4, column=i, value=float(sv))
            v_cell.font = Font(bold=True, size=12); v_cell.number_format = '#,##0.00' if i == 2 else '0'
            v_cell.alignment = Alignment(horizontal='center'); v_cell.border = thin_border
            
        for col_idx, col in enumerate(ws_adj.columns, 1):
            max_length = 0
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            for cell in col:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws_adj.column_dimensions[column_letter].width = max_length + 2

        ws_adj.auto_filter.ref = f"A7:{openpyxl.utils.get_column_letter(ws_adj.max_column)}{ws_adj.max_row}"

        # --- Sheet 4: Stock Transitions ---
        ws = wb.create_sheet("Stock Transitions")
        
        headers = ["Date", "Time", "Item Name", "Quantity", "UOM", "Unit Price (with Tax)", "Value", "Type", "Batch Details", "Ref / Remarks", "User"]
        
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=7, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font

        movement_types = {
            'STORE_INWARD': 'Store Inward',
            'STORE_TO_KITCHEN': 'Store to Kitchen',
            'KITCHEN_TO_STORE': 'Kitchen Return',
            'WASTAGE': 'Wastage / Adjustment',
            'VENDOR_RETURN': 'Vendor Return'
        }
            
        row_num = 8
        grand_total_inward = Decimal('0.00')
        grand_total_outward = Decimal('0.00')
        from .models import MRItemBatchAllocation
        
        for mov in queryset:
            mov_unit_cost = Decimal('0.00')
            batch_details = mov.reference_id or ""
            
            if mov.type == 'STORE_TO_KITCHEN' and batch_details.startswith('MR-'):
                allocs = MRItemBatchAllocation.objects.filter(
                    mr_item__material_request__mr_number=batch_details,
                    stock_batch__item_id=mov.item_id
                ).select_related('stock_batch__procurement__purchase_request')
                
                if allocs.exists():
                    total_value = sum(alloc.quantity_allocated * alloc.stock_batch.unit_price for alloc in allocs)
                    total_qty = sum(alloc.quantity_allocated for alloc in allocs)
                    if total_qty > 0:
                        mov_unit_cost = total_value / total_qty
                        
                    batch_details_list = []
                    for alloc in allocs:
                        pr_ref = alloc.stock_batch.procurement.purchase_request.pr_reference if alloc.stock_batch.procurement and alloc.stock_batch.procurement.purchase_request else "Direct Purchase" if alloc.stock_batch.procurement else "Opening Stock"
                        batch_details_list.append(f"{pr_ref} (Batch {alloc.stock_batch.id})")
                    batch_details = ", ".join(batch_details_list)
                    
            elif "Batch" in batch_details:
                try:
                    import re
                    batch_ids = [int(x) for x in re.findall(r'Batch (\d+)', batch_details)]
                    if batch_ids:
                        sbs = [batch_dict[bid] for bid in batch_ids if bid in batch_dict]
                        if sbs:
                            mov_unit_cost = sum(sb.unit_price for sb in sbs) / len(sbs)
                            batch_details_list = []
                            for sb in sbs:
                                if sb.procurement and sb.procurement.purchase_request:
                                    pr_ref = sb.procurement.purchase_request.pr_reference
                                elif sb.procurement:
                                    pr_ref = "Direct Purchase"
                                else:
                                    pr_ref = "Opening Stock"
                                batch_details_list.append(f"{pr_ref} (Batch {sb.id})")
                            batch_details = ", ".join(batch_details_list)
                except:
                    pass
            elif mov.type == 'STORE_INWARD':
                # For older STORE_INWARD records that do not have "Batch" in the reference string,
                # we can find the matching procurement to get the exact cost.
                from .models import Procurement
                proc = Procurement.objects.filter(
                    item_id=mov.item_id,
                    quantity=mov.quantity,
                    purchase_request__pr_reference=mov.reference_id
                ).order_by('-id').first()
                if proc and proc.quantity > 0:
                    mov_unit_cost = proc.total_price / proc.quantity
            
            if mov_unit_cost == Decimal('0.00'):
                mov_unit_cost = latest_batch_dict.get(mov.item_id, Decimal('0.00'))
                    
            is_positive = mov.type in ['STORE_INWARD', 'KITCHEN_TO_STORE']
            qty_val = float(mov.quantity) if is_positive else -float(mov.quantity)
            val_total = qty_val * float(mov_unit_cost)
            
            if mov.type == 'STORE_INWARD':
                grand_total_inward += Decimal(str(val_total))
            elif mov.type == 'VENDOR_RETURN':
                grand_total_inward -= Decimal(str(abs(val_total)))
            elif mov.type in ['STORE_TO_KITCHEN', 'WASTAGE', 'BREAKAGE', 'EXPIRED']:
                grand_total_outward += Decimal(str(abs(val_total)))
            elif mov.type == 'KITCHEN_TO_STORE':
                grand_total_outward -= Decimal(str(abs(val_total)))
            elif mov.type == 'ADJUSTMENT':
                if val_total > 0:
                    grand_total_inward += Decimal(str(val_total))
                else:
                    grand_total_outward += Decimal(str(abs(val_total)))
            
            ws.cell(row=row_num, column=1, value=mov.movement_date.date())
            ws.cell(row=row_num, column=2, value=mov.movement_date.strftime("%H:%M:%S"))
            ws.cell(row=row_num, column=3, value=mov.item.name)
            
            c4 = ws.cell(row=row_num, column=4, value=qty_val)
            c4.number_format = '#,##0.00'
            
            ws.cell(row=row_num, column=5, value=mov.item.unit)
            
            c6 = ws.cell(row=row_num, column=6, value=float(mov_unit_cost))
            c6.number_format = '#,##0.00'
            
            c7 = ws.cell(row=row_num, column=7, value=val_total)
            c7.number_format = '#,##0.00'
            
            ws.cell(row=row_num, column=8, value=movement_types.get(mov.type, mov.type))
            ws.cell(row=row_num, column=9, value=batch_details)
            
            ref_remarks = f"REF: {mov.reference_id}" if mov.reference_id else ""
            if mov.remarks:
                ref_remarks += f" ({mov.remarks})"
            ws.cell(row=row_num, column=10, value=ref_remarks)
            ws.cell(row=row_num, column=11, value=mov.user.username if mov.user else "System")
            
            row_num += 1

        # Summary Card
        ws.merge_cells('B2:C2')
        card_title = ws.cell(row=2, column=2, value="STOCK TRANSITIONS SUMMARY")
        card_title.font = Font(color='FFFFFF', bold=True, size=14)
        card_title.fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        card_title.alignment = Alignment(horizontal='center', vertical='center')
        
        sum_headers = ["Total Inward Value", "Total Outward Value"]
        sum_vals = [grand_total_inward, grand_total_outward]
        
        sum_header_fill = PatternFill(start_color='D9E1F2', end_color='D9E1F2', fill_type='solid')
        thin_border = openpyxl.styles.borders.Border(
            left=openpyxl.styles.borders.Side(style='thin'), right=openpyxl.styles.borders.Side(style='thin'), 
            top=openpyxl.styles.borders.Side(style='thin'), bottom=openpyxl.styles.borders.Side(style='thin')
        )
        
        for i, (sh, sv) in enumerate(zip(sum_headers, sum_vals), 2):
            h_cell = ws.cell(row=3, column=i, value=sh)
            h_cell.font = Font(bold=True); h_cell.fill = sum_header_fill
            h_cell.alignment = Alignment(horizontal='center'); h_cell.border = thin_border
            
            v_cell = ws.cell(row=4, column=i, value=float(sv))
            v_cell.font = Font(bold=True, size=12); v_cell.number_format = '#,##0.00'
            v_cell.alignment = Alignment(horizontal='center'); v_cell.border = thin_border

        # Adjust column widths
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except: pass
            ws.column_dimensions[column].width = max_length + 2
            
        ws.auto_filter.ref = f"A7:{openpyxl.utils.get_column_letter(ws.max_column)}{ws.max_row}"
        
        # --- Sheet 5: Vendor Returns ---
        ws_vr = wb.create_sheet("Vendor Returns")
        
        vr_headers = [
            'Vendor Name', 'PR Number', 'Return Date', 'Item Name', 'Return Qty',
            'Unit Price (Incl. Tax)', 'Total Return Value', 'Reason', 'Action By', 'Batch IDs'
        ]
        
        for col_num, header in enumerate(vr_headers, 1):
            cell = ws_vr.cell(row=7, column=col_num, value=header)
            cell.fill, cell.font = header_fill, header_font
            
        row_num = 8
        grand_total_vr_value = Decimal('0.00')
        
        from .models import VendorReturnItem
        vr_items = VendorReturnItem.objects.all().order_by('-vendor_return__return_date')
        if start_date:
            vr_items = vr_items.filter(vendor_return__return_date__gte=start_date)
        if end_date:
            vr_items = vr_items.filter(vendor_return__return_date__lte=end_date)
        if item_id:
            vr_items = vr_items.filter(item_id=item_id)
            
        for vri in vr_items:
            vr = vri.vendor_return
            vendor_name = vr.vendor.name if vr.vendor else 'N/A'
            pr_ref = vr.purchase_request.pr_reference if vr.purchase_request else 'N/A'
            
            unit_price_base = vri.unit_price or Decimal('0.00')
            tax_rate = vri.tax_rate or Decimal('0.00')
            unit_price_incl_tax = unit_price_base * (1 + (tax_rate / 100))
            total_value = unit_price_incl_tax * (vri.quantity or Decimal('0.00'))
            
            ws_vr.cell(row=row_num, column=1, value=vendor_name)
            ws_vr.cell(row=row_num, column=2, value=pr_ref)
            ws_vr.cell(row=row_num, column=3, value=vr.return_date.strftime('%Y-%m-%d') if vr.return_date else '')
            ws_vr.cell(row=row_num, column=4, value=vri.item.name if vri.item else 'N/A')
            c5 = ws_vr.cell(row=row_num, column=5, value=float(vri.quantity or 0)); c5.number_format = '#,##0.00'
            c6 = ws_vr.cell(row=row_num, column=6, value=float(unit_price_incl_tax)); c6.number_format = '#,##0.00'
            c7 = ws_vr.cell(row=row_num, column=7, value=float(total_value)); c7.number_format = '#,##0.00'
            ws_vr.cell(row=row_num, column=8, value=vri.reason or vr.remarks or 'N/A')
            ws_vr.cell(row=row_num, column=9, value=vr.created_by.username if vr.created_by else 'N/A')
            
            from .models import StockBatch
            vr_batches = StockBatch.objects.filter(procurement__purchase_request=vr.purchase_request, item=vri.item)
            vr_batch_str = ", ".join([f"Batch {b.id}" for b in vr_batches]) if vr_batches.exists() else "N/A"
            ws_vr.cell(row=row_num, column=10, value=vr_batch_str)
            
            grand_total_vr_value += total_value
            row_num += 1
            
        # Summary Card
        ws_vr.merge_cells('B2:C2')
        card_title = ws_vr.cell(row=2, column=2, value="VENDOR RETURNS SUMMARY")
        card_title.font = Font(color='FFFFFF', bold=True, size=14)
        card_title.fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        card_title.alignment = Alignment(horizontal='center', vertical='center')
        
        sum_headers = ["Total Return Value", "Total Return Count"]
        sum_vals = [grand_total_vr_value, (row_num - 8)]
        
        for i, (sh, sv) in enumerate(zip(sum_headers, sum_vals), 2):
            h_cell = ws_vr.cell(row=3, column=i, value=sh)
            h_cell.font = Font(bold=True); h_cell.fill = sum_header_fill
            h_cell.alignment = Alignment(horizontal='center'); h_cell.border = thin_border
            
            v_cell = ws_vr.cell(row=4, column=i, value=float(sv))
            v_cell.font = Font(bold=True, size=12); v_cell.number_format = '#,##0.00' if i == 2 else '0'
            v_cell.alignment = Alignment(horizontal='center'); v_cell.border = thin_border
        
        for col_idx, col in enumerate(ws_vr.columns, 1):
            max_length = 0
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            for cell in col:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except: pass
            ws_vr.column_dimensions[column_letter].width = max_length + 2
            
        ws_vr.auto_filter.ref = f"A7:{openpyxl.utils.get_column_letter(ws_vr.max_column)}{ws_vr.max_row}"
            
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=Stock_Transitions_{timezone.now().strftime("%Y%m%d")}.xlsx'
        wb.save(response)
        return response

class DatabaseViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    @action(detail=False, methods=['get'])
    def backup(self, request):
        """
        Generates and streams a full PostgreSQL database dump (.sql).
        Incudes schema, data, and constraints (pre-data, data, and post-data).
        """
        db_conf = settings.DATABASES['default']
        db_name = db_conf.get('NAME', 'food_tracker')
        db_user = db_conf.get('USER', 'postgres')
        db_pass = db_conf.get('PASSWORD', '9789474804')
        db_host = db_conf.get('HOST', 'db')
        db_port = db_conf.get('PORT', '5432')

        # PostgreSQL credentials environment
        env = os.environ.copy()
        env['PGPASSWORD'] = str(db_pass)

        # Backup command via pg_dump
        dump_cmd = os.environ.get('PG_BIN_PATH', 'pg_dump')
        if dump_cmd == 'pg_dump' and os.name == 'nt':
            import shutil
            if not shutil.which('pg_dump'):
                for v in range(20, 10, -1):
                    path = fr'C:\Program Files\PostgreSQL\{v}\bin\pg_dump.exe'
                    if os.path.exists(path):
                        dump_cmd = path
                        break

        command = [
            dump_cmd,
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '--no-owner',
            '--no-privileges',
            '--clean',
            '--if-exists'
        ]

        try:
            def stream_dump():
                process = subprocess.Popen(
                    command, 
                    env=env, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE
                )
                # Read in chunks of 8KB
                while True:
                    chunk = process.stdout.read(8192)
                    if not chunk:
                        break
                    yield chunk
                
                _, stderr = process.communicate()
                if process.returncode != 0:
                    # In binary mode, we yield the error as a comment bytes
                    error_msg = f"\n-- ERROR: pg_dump failed --\n/* {stderr.decode('utf-8', 'ignore')} */".encode('utf-8')
                    yield error_msg

            response = StreamingHttpResponse(
                stream_dump(),
                content_type='application/sql'
            )
            filename = f"food_tracker_backup_{timezone.now().strftime('%Y%m%d_%H%M%S')}.sql"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser])
    def restore(self, request):
        """
        Receives a .sql file and applies it to the database using psql.
        Requires explicit maintenance permission.
        """
        sql_file = request.FILES.get('file')
        if not sql_file:
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Double check core administrator permission
        if not request.user.is_superuser:
            profile = getattr(request.user, 'profile', None)
            if not (profile and profile.role and profile.role.can_perform_hard_reset):
                 return Response({"error": "Maintenance permission required for restoration"}, status=status.HTTP_403_FORBIDDEN)

        db_conf = settings.DATABASES['default']
        db_name = db_conf.get('NAME', 'food_tracker')
        db_user = db_conf.get('USER', 'postgres')
        db_pass = db_conf.get('PASSWORD', '9789474804')
        db_host = db_conf.get('HOST', 'db')
        db_port = db_conf.get('PORT', '5432')

        env = os.environ.copy()
        env['PGPASSWORD'] = str(db_pass)

        # Temporarily save uploaded file
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.sql') as tmp_file:
                for chunk in sql_file.chunks():
                    tmp_file.write(chunk)
                tmp_path = tmp_file.name

            # Restoration command via psql
            psql_cmd = os.environ.get('PG_BIN_PATH', 'psql')
            # If path is provided, we need to make sure it points to psql specifically
            if os.path.isdir(psql_cmd):
                psql_cmd = os.path.join(psql_cmd, 'psql.exe')
            elif psql_cmd == 'psql' and os.name == 'nt':
                import shutil
                if not shutil.which('psql'):
                    for v in range(20, 10, -1):
                        path = fr'C:\Program Files\PostgreSQL\{v}\bin\psql.exe'
                        if os.path.exists(path):
                            psql_cmd = path
                            break

            command = [
                psql_cmd,
                '-h', db_host,
                '-p', str(db_port),
                '-U', db_user,
                '-d', db_name,
                '-f', tmp_path
            ]
            
            process = subprocess.run(
                command, 
                env=env, 
                capture_output=True, 
                text=True
            )
            
            if process.returncode != 0:
                print(f"DEBUG: Restore failed with code {process.returncode}")
                print(f"DEBUG: Stderr: {process.stderr}")
                return Response({
                    "error": "Database restoration failed", 
                    "details": process.stderr,
                    "command_used": psql_cmd,
                    "output": process.stdout[-1000:]
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            return Response({"status": "Success", "message": "Database successfully restored from backup."})
        except Exception as e:
            import traceback
            print(f"DEBUG: Exception during restore: {str(e)}")
            print(traceback.format_exc())
            return Response({
                "error": str(e),
                "traceback": traceback.format_exc()
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @action(detail=False, methods=['post'])
    def wipe_entry_tables(self, request):
        """
        Wipes all operational entry tables and resets their auto-increment IDs to 1.
        Preserves master data like Items, Vendors, Users, and Roles.
        """
        # Check permissions
        if not request.user.is_superuser:
            profile = getattr(request.user, 'profile', None)
            if not (profile and profile.role and profile.role.can_perform_hard_reset):
                return Response({"error": "Unauthorized. Requires hard reset permissions."}, status=status.HTTP_403_FORBIDDEN)

        try:
            from django.db import connection, transaction
            from .models import (
                VendorReturnItem, VendorReturn, MRItemBatchAllocation, StockBatch, 
                MaterialRequestItem, MaterialRequest, PurchaseRequestReceiptFile, 
                PurchaseRequestReceipt, PurchaseRequestItem, PurchaseRequest, 
                Procurement, StockMovement, Inventory, OpexEntry
            )
            
            models_to_clear = [
                VendorReturnItem, VendorReturn, MRItemBatchAllocation, StockBatch, 
                MaterialRequestItem, MaterialRequest, PurchaseRequestReceiptFile, 
                PurchaseRequestReceipt, PurchaseRequestItem, PurchaseRequest, 
                Procurement, StockMovement, Inventory, OpexEntry
            ]
            
            with transaction.atomic():
                with connection.cursor() as cursor:
                    if connection.vendor == 'postgresql':
                        # PostgreSQL: TRUNCATE with RESTART IDENTITY
                        table_names = [m._meta.db_table for m in models_to_clear]
                        cursor.execute(f"TRUNCATE {','.join(table_names)} RESTART IDENTITY CASCADE;")
                    elif connection.vendor == 'sqlite':
                        # SQLite: DELETE and reset sqlite_sequence
                        for m in models_to_clear:
                            cursor.execute(f"DELETE FROM {m._meta.db_table};")
                            cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{m._meta.db_table}';")
                    else:
                        # Fallback to standard delete
                        for m in models_to_clear:
                            m.objects.all().delete()
                            
            return Response({"status": "SUCCESS: All entry tables wiped and IDs reset to 1. Master data preserved."})
        except Exception as e:
            return Response({"error": f"Wipe failed: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class StockBatchViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only viewset to list available stock batches for FIFO issuance."""
    serializer_class = StockBatchSerializer
    permission_classes = [CanManageMR | CanViewStock]

    def get_queryset(self):
        qs = StockBatch.objects.filter(remaining_quantity__gt=0).select_related(
            'item', 'department', 'procurement__purchase_request'
        )
        item_id = self.request.query_params.get('item')
        dept_code = self.request.query_params.get('department_code')
        if item_id:
            qs = qs.filter(item_id=item_id)
        if dept_code:
            qs = qs.filter(department__code=dept_code)
        # Default to procurement store batches only
        else:
            qs = qs.filter(department__code='procurement')
            
        max_date = self.request.query_params.get('max_date')
        if max_date:
            qs = qs.filter(batch_date__lte=max_date)
            
        pr_id = self.request.query_params.get('pr_id')
        if pr_id:
            qs = qs.filter(procurement__purchase_request_id=pr_id)
            
        return qs

    @action(detail=False, methods=['get'], url_path='item-ledger')
    def item_ledger(self, request):
        """
        Returns a full stock ledger for a single item:
        All batches (including exhausted), ordered by batch_date ASC,
        with each batch's MR allocations and Vendor Returns embedded.
        Query params: item (required)
        """
        item_id = request.query_params.get('item')
        if not item_id:
            return Response({'error': 'item query param required'}, status=status.HTTP_400_BAD_REQUEST)

        ordering = request.query_params.get('ordering', 'batch_date')
        order_params = ['-batch_date', '-created_at'] if ordering == '-batch_date' else ['batch_date', 'created_at']

        batches = StockBatch.objects.filter(
            item_id=item_id,
            department__code='procurement'
        )

        stock_status = request.query_params.get('stock_status', 'all')
        if stock_status == 'available':
            batches = batches.filter(remaining_quantity__gt=0)
        elif stock_status == 'consumed':
            batches = batches.filter(remaining_quantity__lte=0)

        batches = batches.select_related(
            'item', 'department', 'procurement__purchase_request', 'procurement__vendor'
        ).prefetch_related(
            'allocations__mr_item__material_request'
        ).order_by(*order_params)

        # Also get Vendor Returns for this item linked to these PRs
        vendor_returns = VendorReturnItem.objects.filter(
            item_id=item_id
        ).select_related('vendor_return', 'vendor_return__purchase_request')

        # Group returns by batch_id, fallback to PR for legacy returns
        returns_by_batch = {}
        returns_by_pr = {}
        for vr_item in vendor_returns:
            ret_data = {
                'date': vr_item.vendor_return.return_date.strftime('%Y-%m-%d'),
                'quantity': Decimal(str(vr_item.quantity)),
                'reason': vr_item.reason or ""
            }
            if vr_item.batch_id:
                returns_by_batch.setdefault(vr_item.batch_id, []).append(ret_data)
            elif vr_item.vendor_return.purchase_request:
                pr_ref = vr_item.vendor_return.purchase_request.pr_reference
                returns_by_pr.setdefault(pr_ref, []).append(ret_data)

        result = []
        for batch in batches:
            issued = []
            for alloc in batch.allocations.all():
                mr = alloc.mr_item.material_request
                issued.append({
                    'mr_number': mr.mr_number,
                    'issued_date': mr.action_at.strftime('%Y-%m-%d') if mr.action_at else None,
                    'quantity_issued': str(alloc.quantity_allocated),
                    'unit_price': str(batch.unit_price),
                    'line_total': str((alloc.quantity_allocated * batch.unit_price).quantize(Decimal('0.00'))),
                })

            # Exact batch returns
            batch_returns = list(returns_by_batch.get(batch.id, []))
            
            mr_allocations = batch.allocations.aggregate(s=Sum('quantity_allocated'))['s'] or Decimal('0')
            total_depleted = batch.initial_quantity - batch.remaining_quantity
            
            # Handle Legacy Returns (FIFO distribution)
            pr_ref = batch.procurement.purchase_request.pr_reference if batch.procurement and batch.procurement.purchase_request else None
            legacy_returns = returns_by_pr.get(pr_ref, [])
            
            exact_returned = sum(r['quantity'] for r in batch_returns)
            available_for_legacy = total_depleted - mr_allocations - exact_returned
            
            if available_for_legacy > 0 and legacy_returns:
                for lr in legacy_returns:
                    if lr['quantity'] > 0 and available_for_legacy > 0:
                        take = min(lr['quantity'], available_for_legacy)
                        lr['quantity'] -= take
                        available_for_legacy -= take
                        batch_returns.append({
                            'date': lr['date'],
                            'quantity': take,
                            'reason': lr['reason'] + ' (Legacy FIFO)'
                        })

            # Convert quantities to string for JSON serialization
            final_returns = []
            for r in batch_returns:
                final_returns.append({
                    'date': r['date'],
                    'quantity': str(r['quantity']),
                    'reason': r['reason']
                })

            total_returned_qty = sum(Decimal(r['quantity']) for r in final_returns)
            total_adjusted = total_depleted - mr_allocations - total_returned_qty
            
            if total_adjusted < Decimal('0'):
                total_adjusted = Decimal('0')

            result.append({
                'id': batch.id,
                'batch_date': str(batch.batch_date),
                'pr_reference': pr_ref,
                'vendor_name': batch.procurement.vendor.name if batch.procurement and batch.procurement.vendor else None,
                'initial_quantity': str(batch.initial_quantity),
                'remaining_quantity': str(batch.remaining_quantity),
                'unit_price': str(batch.unit_price),
                'batch_value': str((batch.initial_quantity * batch.unit_price).quantize(Decimal('0.00'))),
                'issued': issued,
                'returns': final_returns,
                'total_issued': str(mr_allocations),
                'total_adjusted': str(total_adjusted),
                'total_returned': str(total_returned_qty)
            })

        # Calculate Quantities and Financial Values using Exact Batch FIFO (to match Audit Excel exactly)
        purchased_true = Decimal('0.000')
        issued_true = Decimal('0.000')
        wastage_true = Decimal('0.000')
        returned_true = Decimal('0.000')
        total_balance = Decimal('0.000')
        
        total_pr_value = Decimal('0.00')
        kitchen_pushed_value = Decimal('0.00')
        adjusted_wasted_value = Decimal('0.00')
        vendor_returned_value = Decimal('0.00')
        
        for b in result:
            purchased_true += Decimal(b['initial_quantity'])
            issued_true += Decimal(b['total_issued'])
            wastage_true += Decimal(b['total_adjusted'])
            returned_true += Decimal(b['total_returned'])
            total_balance += Decimal(b['remaining_quantity'])
            
            u_price = Decimal(b['unit_price'])
            total_pr_value += Decimal(b['initial_quantity']) * u_price
            kitchen_pushed_value += Decimal(b['total_issued']) * u_price
            adjusted_wasted_value += Decimal(b['total_adjusted']) * u_price
            vendor_returned_value += Decimal(b['total_returned']) * u_price
        
        # Pull active inventory value if exists
        inv_value = Inventory.objects.filter(item_id=item_id, department__code='procurement').first()
        total_val = inv_value.inventory_value if inv_value else Decimal('0')

        summary = {
            'total_purchased': purchased_true,
            'total_issued': issued_true,
            'total_returned': returned_true, # Vendor Returns
            'total_adjusted': wastage_true,  # Now explicitly included for the UI
            'total_kitchen_return': Decimal('0.000'), # Issued is already net
            'total_balance': total_balance,
            'total_value': total_val,
            
            'total_pr_value': total_pr_value,
            'kitchen_pushed_value': kitchen_pushed_value,
            'vendor_returned_value': vendor_returned_value,
            'adjusted_wasted_value': adjusted_wasted_value,
            'remaining_on_procurement': total_balance,
            'remaining_value_in_procurement': total_val,
        }

        # Pagination
        try:
            page = int(request.query_params.get('page', 1))
            page_size = request.query_params.get('page_size', '10') # Default 10 for detailed ledger
            
            if str(page_size).lower() == 'all':
                paginated_results = result
                total_pages = 1
            else:
                page_size = int(page_size)
                start_idx = (page - 1) * page_size
                end_idx = start_idx + page_size
                paginated_results = result[start_idx:end_idx]
                total_pages = (len(result) + page_size - 1) // page_size
        except (ValueError, TypeError):
            paginated_results = result
            page = 1
            total_pages = 1

        return Response({
            'item_id': item_id,
            'count': len(result),
            'batches': paginated_results,
            'total_pages': total_pages,
            'next': page < total_pages,
            'previous': page > 1,
            'summary': summary
        })

    @action(detail=False, methods=['get'], url_path='export-excel')
    def export_excel(self, request):
        """
        Exports a full stock ledger for a single item to Excel.
        Two sheets: 1. Batch Summary, 2. Detailed Ledger
        """
        item_id = request.query_params.get('item')
        if not item_id:
            return Response({'error': 'item query param required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            item = Item.objects.get(id=item_id)
        except Item.DoesNotExist:
            return Response({'error': 'Item not found'}, status=status.HTTP_404_NOT_FOUND)

        batches = StockBatch.objects.filter(
            item_id=item_id,
            department__code='procurement'
        ).select_related(
            'item', 'department', 'procurement__purchase_request', 'procurement__vendor'
        ).prefetch_related(
            'allocations__mr_item__material_request'
        ).order_by('batch_date', 'created_at')

        vendor_returns = VendorReturnItem.objects.filter(
            item_id=item_id
        ).select_related('vendor_return', 'vendor_return__purchase_request')

        # Group returns by PR
        returns_by_pr = {}
        for vr_item in vendor_returns:
            pr_ref = vr_item.vendor_return.purchase_request.pr_reference
            if pr_ref not in returns_by_pr:
                returns_by_pr[pr_ref] = []
            returns_by_pr[pr_ref].append(vr_item)

        wb = openpyxl.Workbook()
        
        # --- SHEET 1: BATCH SUMMARY ---
        ws1 = wb.active
        ws1.title = "Batch Summary"
        
        # Styles
        title_font = Font(size=14, bold=True)
        header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True)
        center_alignment = Alignment(horizontal='center', vertical='center')
        
        ws1.merge_cells('A1:I1')
        ws1['A1'] = f"Stock Batch Summary: {item.name} ({item.unit})"
        ws1['A1'].font = title_font
        ws1['A1'].alignment = center_alignment

        headers1 = ['Batch Date', 'PR Reference', 'Vendor', 'Initial Qty', 'Unit Price', 'Batch Value', 'Total Issued', 'Total Returned', 'Remaining']
        for col, h in enumerate(headers1, 1):
            cell = ws1.cell(row=3, column=col, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_alignment

        row = 4
        total_p = Decimal('0')
        total_i = Decimal('0')
        total_r = Decimal('0')
        total_v = Decimal('0')
        total_b = Decimal('0')

        for b in batches:
            pr_ref = b.procurement.purchase_request.pr_reference if b.procurement and b.procurement.purchase_request else "N/A"
            b_returned = sum(r.quantity for r in returns_by_pr.get(pr_ref, [])) if pr_ref != "N/A" else Decimal('0')
            b_issued = b.initial_quantity - b.remaining_quantity - b_returned
            b_val = b.initial_quantity * b.unit_price

            ws1.cell(row=row, column=1, value=b.batch_date)
            ws1.cell(row=row, column=2, value=pr_ref)
            ws1.cell(row=row, column=3, value=b.procurement.vendor.name if b.procurement and b.procurement.vendor else "N/A")
            ws1.cell(row=row, column=4, value=float(b.initial_quantity))
            ws1.cell(row=row, column=5, value=float(b.unit_price))
            ws1.cell(row=row, column=6, value=float(b.initial_quantity * b.unit_price))
            ws1.cell(row=row, column=7, value=float(b_issued))
            ws1.cell(row=row, column=8, value=float(b_returned))
            ws1.cell(row=row, column=9, value=float(b.remaining_quantity))
            
            total_p += b.initial_quantity
            total_i += b_issued
            total_r += b_returned
            total_v += b_val
            total_b += b.remaining_quantity
            row += 1

        # Summary totals
        row += 1
        ws1.cell(row=row, column=3, value="TOTALS").font = Font(bold=True)
        ws1.cell(row=row, column=4, value=float(total_p)).font = Font(bold=True)
        ws1.cell(row=row, column=6, value=float(total_v)).font = Font(bold=True)
        ws1.cell(row=row, column=7, value=float(total_i)).font = Font(bold=True)
        ws1.cell(row=row, column=8, value=float(total_r)).font = Font(bold=True)
        ws1.cell(row=row, column=9, value=float(total_b)).font = Font(bold=True)

        for col in ws1.columns:
            max_l = max(len(str(c.value or "")) for c in col)
            col_letter = get_column_letter(col[0].column)
            ws1.column_dimensions[col_letter].width = max_l + 2

        # --- SHEET 2: DETAILED LEDGER ---
        ws2 = wb.create_sheet("Detailed Ledger")
        ws2.merge_cells('A1:G1')
        ws2['A1'] = f"Chronological Stock Ledger: {item.name}"
        ws2['A1'].font = title_font
        ws2['A1'].alignment = center_alignment

        headers2 = ['Date', 'Type', 'Reference', 'Description', 'In (+)', 'Out (-)', 'Balance']
        for col, h in enumerate(headers2, 1):
            cell = ws2.cell(row=3, column=col, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_alignment

        # Collect all movements
        movements = []
        for b in batches:
            movements.append({
                'date': b.batch_date,
                'type': 'PURCHASE',
                'ref': b.procurement.purchase_request.pr_reference if b.procurement and b.procurement.purchase_request else "N/A",
                'desc': f"Purchase from {b.procurement.vendor.name}" if b.procurement and b.procurement.vendor else "Direct Entry",
                'in': b.initial_quantity,
                'out': 0,
                'sort': b.created_at
            })
            for a in b.allocations.all():
                mr = a.mr_item.material_request
                movements.append({
                    'date': mr.action_at.date() if mr.action_at else b.batch_date,
                    'type': 'ISSUANCE',
                    'ref': mr.mr_number,
                    'desc': f"Issued to Kitchen (Batch Date: {b.batch_date})",
                    'in': 0,
                    'out': a.quantity_allocated,
                    'sort': mr.action_at if mr.action_at else b.created_at
                })
        
        for vi in vendor_returns:
            vr = vi.vendor_return
            movements.append({
                'date': vr.return_date,
                'type': 'RETURN',
                'ref': vr.purchase_request.pr_reference if vr.purchase_request else "N/A",
                'desc': f"Returned to Vendor: {vi.reason}",
                'in': 0,
                'out': vi.quantity,
                'sort': vr.created_at
            })

        movements.sort(key=lambda x: (x['date'], x['sort']))

        row = 4
        running_bal = Decimal('0')
        for m in movements:
            running_bal += Decimal(str(m['in']))
            running_bal -= Decimal(str(m['out']))
            
            ws2.cell(row=row, column=1, value=m['date'])
            ws2.cell(row=row, column=2, value=m['type'])
            ws2.cell(row=row, column=3, value=m['ref'])
            ws2.cell(row=row, column=4, value=m['desc'])
            ws2.cell(row=row, column=5, value=float(m['in']) if m['in'] > 0 else "")
            ws2.cell(row=row, column=6, value=float(m['out']) if m['out'] > 0 else "")
            ws2.cell(row=row, column=7, value=float(running_bal))
            
            # Color coding for movements
            if m['type'] == 'PURCHASE':
                ws2.cell(row=row, column=2).font = Font(color='27ae60', bold=True)
            elif m['type'] == 'ISSUANCE':
                ws2.cell(row=row, column=2).font = Font(color='2980b9')
            elif m['type'] == 'RETURN':
                ws2.cell(row=row, column=2).font = Font(color='e67e22')
            
            row += 1

        for col in ws2.columns:
            max_l = max(len(str(c.value or "")) for c in col)
            col_letter = get_column_letter(col[0].column)
            ws2.column_dimensions[col_letter].width = min(max_l + 2, 50)

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=Stock_Ledger_{item.name.replace(" ", "_")}_{datetime.now().strftime("%Y%m%d")}.xlsx'
        wb.save(response)
        return response



class VendorReturnViewSet(viewsets.ModelViewSet):
    queryset = VendorReturn.objects.all()
    serializer_class = VendorReturnSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.action in ['create', 'destroy']:
            return [CanViewPREntries()]
        return super().get_permissions()

    def get_queryset(self):
        queryset = VendorReturn.objects.all().order_by('-return_date')
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        vendor_id = self.request.query_params.get('vendor')
        
        if start_date:
            queryset = queryset.filter(return_date__gte=start_date)
        if end_date:
            queryset = queryset.filter(return_date__lte=end_date)
        if vendor_id:
            queryset = queryset.filter(vendor_id=vendor_id)
            
        return queryset

    @action(detail=False, methods=['get'])
    def item_report(self, request):
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        vendor_id = request.query_params.get('vendor')
        
        items = VendorReturnItem.objects.all().select_related(
            'vendor_return', 'vendor_return__vendor', 'vendor_return__purchase_request', 'item'
        ).order_by('-vendor_return__return_date')
        
        if start_date:
            items = items.filter(vendor_return__return_date__gte=start_date)
        if end_date:
            items = items.filter(vendor_return__return_date__lte=end_date)
        if vendor_id:
            items = items.filter(vendor_return__vendor_id=vendor_id)
            
        data = []
        for i in items:
            data.append({
                'id': i.id,
                'return_date': i.vendor_return.return_date,
                'vendor_name': i.vendor_return.vendor.name,
                'pr_reference': i.vendor_return.purchase_request.pr_reference,
                'item_name': i.item.name,
                'quantity': float(i.quantity),
                'unit': i.item.unit,
                'unit_price': float(i.unit_price),
                'total_amount': float(i.quantity * i.unit_price),
                'reason': i.reason,
                'remarks': i.vendor_return.remarks
            })
        return Response(data)

    @action(detail=False, methods=['get'])
    def export_item_report(self, request):
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        vendor_id = request.query_params.get('vendor')
        
        items = VendorReturnItem.objects.all().select_related(
            'vendor_return', 'vendor_return__vendor', 'vendor_return__purchase_request', 'item'
        ).order_by('-vendor_return__return_date')
        
        if start_date:
            items = items.filter(vendor_return__return_date__gte=start_date)
        if end_date:
            items = items.filter(vendor_return__return_date__lte=end_date)
        if vendor_id:
            items = items.filter(vendor_return__vendor_id=vendor_id)
            
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Vendor Returns Report"
        
        headers = ["Date", "Vendor", "PR Reference", "Item", "Quantity", "Unit", "Unit Price", "Total Amount", "Reason", "Remarks"]
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
            cell.font = Font(color='FFFFFF', bold=True)
            
        for row_num, i in enumerate(items, 2):
            ws.cell(row=row_num, column=1, value=str(i.vendor_return.return_date))
            ws.cell(row=row_num, column=2, value=i.vendor_return.vendor.name)
            ws.cell(row=row_num, column=3, value=i.vendor_return.purchase_request.pr_reference)
            ws.cell(row=row_num, column=4, value=i.item.name)
            ws.cell(row=row_num, column=5, value=float(i.quantity))
            ws.cell(row=row_num, column=6, value=i.item.unit)
            ws.cell(row=row_num, column=7, value=float(i.unit_price))
            ws.cell(row=row_num, column=8, value=float(i.quantity * i.unit_price))
            ws.cell(row=row_num, column=9, value=i.reason)
            ws.cell(row=row_num, column=10, value=i.vendor_return.remarks)
            
        # Auto-adjust column widths
        for col_idx, col in enumerate(ws.columns, 1):
            max_length = 0
            column_letter = openpyxl.utils.get_column_letter(col_idx)
            for cell in col:
                try:
                    if cell.value and len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws.column_dimensions[column_letter].width = max_length + 2

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=Vendor_Return_Report_{timezone.now().strftime("%Y%m%d")}.xlsx'
        wb.save(response)
        return response

    @action(detail=False, methods=['get'], url_path='export_excel')
    def export_excel(self, request):
        returns = self.get_queryset().select_related('purchase_request', 'vendor', 'created_by').prefetch_related('items__item')
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Vendor Returns"
        
        headers = ["Date", "Return ID", "PR Reference", "Vendor", "Total Amount", "Items (Qty)", "Created By", "Remarks"]
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
            cell.font = Font(color='FFFFFF', bold=True)
            
        for row_num, ret in enumerate(returns, 2):
            ws.cell(row=row_num, column=1, value=str(ret.return_date))
            ws.cell(row=row_num, column=2, value=ret.id)
            ws.cell(row=row_num, column=3, value=ret.purchase_request.pr_reference if ret.purchase_request else "N/A")
            ws.cell(row=row_num, column=4, value=ret.vendor.name if ret.vendor else "N/A")
            ws.cell(row=row_num, column=5, value=float(ret.total_amount))
            
            items_str = ", ".join([f"{i.item.name} ({i.quantity})" for i in ret.items.all()])
            ws.cell(row=row_num, column=6, value=items_str)
            ws.cell(row=row_num, column=7, value=ret.created_by.username if ret.created_by else "")
            ws.cell(row=row_num, column=8, value=ret.remarks)
            
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=Vendor_Returns_{timezone.now().strftime("%Y%m%d")}.xlsx'
        wb.save(response)
        return response

    def perform_create(self, serializer):
        try:
            items_data = self.request.data.get('items', [])
            with transaction.atomic():
                total_amount = Decimal('0')
                for item_data in items_data:
                    item_id = item_data.get('item')
                    qty = Decimal(str(item_data.get('quantity', 0)))
                    if qty <= 0:
                        continue
                        
                    # Get original PR pricing for this item
                    from .models import PurchaseRequestItem
                    pr_item = PurchaseRequestItem.objects.get(
                        purchase_request_id=self.request.data.get('purchase_request'),
                        item_id=item_id
                    )
                    
                    # Get actual tax rate from the latest receipt for this item
                    from .models import Procurement
                    latest_proc = Procurement.objects.filter(
                        purchase_request_id=self.request.data.get('purchase_request'),
                        item_id=item_id
                    ).last()
                    actual_tax_rate = latest_proc.tax_rate if latest_proc else pr_item.tax_rate

                    # Calculate line total with actual tax and original discount
                    base_price = pr_item.unit_price * qty
                    if pr_item.discount_type == 'PERCENTAGE':
                        discount_amount = base_price * (pr_item.discount_value / Decimal('100'))
                    else:
                        discount_amount = (pr_item.discount_value / pr_item.quantity_ordered) * qty if pr_item.quantity_ordered > 0 else Decimal('0')
                    
                    discounted_price = base_price - discount_amount
                    line_tax_factor = Decimal('1') + (actual_tax_rate / Decimal('100'))
                    line_total = (discounted_price * line_tax_factor).quantize(Decimal('0.00'))
                    
                    total_amount += line_total

                vendor_return = serializer.save(created_by=self.request.user, total_amount=total_amount)

                # Process each return item
                for return_item in vendor_return.items.all():
                    item = return_item.item
                    quantity_to_return = return_item.quantity

                    if quantity_to_return <= 0:
                        continue

                    # 1. Fetch exact PR Item and update received quantity
                    pr_item = PurchaseRequestItem.objects.get(
                        purchase_request=vendor_return.purchase_request,
                        item=item
                    )
                    
                    # Update PurchaseRequestItem received quantity
                    pr_item.quantity_received -= quantity_to_return
                    pr_item.save()
                    
                    # Note: Inventory, StockBatch deductions, and StockMovement creation 
                    # are already explicitly handled in serializers.py for exact Batch IDs.
                    
        except Exception as e:
            # Re-raise as a validation error so the frontend gets the message
            raise serializers.ValidationError({"detail": str(e)})

    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

class PasswordResetRequestViewSet(viewsets.ModelViewSet):
    queryset = PasswordResetRequest.objects.all()
    serializer_class = PasswordResetRequestSerializer
    
    def get_permissions(self):
        if self.action == 'create':
            permission_classes = [permissions.AllowAny]
        else:
            permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]
        return [permission() for permission in permission_classes]

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        reset_req = self.get_object()
        reset_req.status = 'COMPLETED'
        reset_req.completed_at = timezone.now()
        reset_req.completed_by = request.user
        reset_req.remarks = request.data.get('remarks', '')
        reset_req.save()
        return Response({"status": "Password reset marked as completed"})

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        reset_req = self.get_object()
        reset_req.status = 'CANCELLED'
        reset_req.completed_at = timezone.now()
        reset_req.completed_by = request.user
        reset_req.remarks = request.data.get('remarks', '')
        reset_req.save()
        return Response({"status": "Password reset marked as cancelled"})




from .models import OrganizationUnit
from .serializers import OrganizationUnitSerializer
from rest_framework.decorators import action
from rest_framework.response import Response

class OrganizationUnitViewSet(viewsets.ModelViewSet):
    queryset = OrganizationUnit.objects.filter(is_active=True).prefetch_related(
        'deployments', 'deployments__status', 'children'
    )
    serializer_class = OrganizationUnitSerializer

    @action(detail=False, methods=['get'])
    def tree(self, request):
        # Get only root nodes (nodes without parents)
        roots = self.get_queryset().filter(parent__isnull=True).order_by('display_order')
        serializer = self.get_serializer(roots, many=True)
        return Response(serializer.data)



from .models import Designation, DeploymentPlan, DeploymentStatus
from .serializers import (
    DesignationSerializer, 
    DeploymentPlanCRUDSerializer, 
    DeploymentStatusSerializer,
    OrganizationUnitCRUDSerializer
)

class DesignationViewSet(viewsets.ModelViewSet):
    queryset = Designation.objects.all().order_by('display_order', 'id')
    serializer_class = DesignationSerializer

class DeploymentPlanViewSet(viewsets.ModelViewSet):
    queryset = DeploymentPlan.objects.all().order_by('organization_unit', 'designation')
    serializer_class = DeploymentPlanCRUDSerializer

class DeploymentStatusViewSet(viewsets.ModelViewSet):
    queryset = DeploymentStatus.objects.all()
    serializer_class = DeploymentStatusSerializer

class OrganizationUnitCRUDViewSet(viewsets.ModelViewSet):
    queryset = OrganizationUnit.objects.all().order_by('display_order', 'id')
    serializer_class = OrganizationUnitCRUDSerializer
