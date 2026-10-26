from rest_framework import viewsets, permissions, pagination
from rest_framework.response import Response
from decimal import Decimal
from django.db.models import Sum
from .models import Procurement, MaterialRequest, MaterialRequestItem, StockMovement, Item, StockBatch
from .permissions import CanViewReports

class StandardResultsSetPagination(pagination.PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

class PurchaseAuditViewSet(viewsets.ViewSet):
    permission_classes = [CanViewReports]
    
    def list(self, request):
        item_id = request.query_params.get('item')
        start_date = request.query_params.get('start_date') or '2026-04-30'
        end_date = request.query_params.get('end_date')
        
        queryset = Procurement.objects.filter(purchase_date__gte=start_date).order_by('-purchase_date', '-created_at')
        if item_id:
            queryset = queryset.filter(item_id=item_id)
        if end_date:
            queryset = queryset.filter(purchase_date__lte=end_date)
            
        queryset = queryset.select_related(
            'item', 'vendor', 'purchase_request', 'receipt',
            'purchase_request__closed_by', 'purchase_request__approved_by_l1', 'purchase_request__approved_by_l2'
        ).prefetch_related('stock_batches', 'stock_batches__allocations')
        
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        
        results = []
        
        from .models import VendorReturnItem
        for p in page:
            bought_qty = p.quantity or Decimal('0.000')
            unit_price = p.unit_price or Decimal('0.00')
            total_price = p.total_price or Decimal('0.00')
            
            base_cost = bought_qty * unit_price
            tax_paid = max(Decimal('0.00'), total_price - base_cost)
            
            remaining_in_stock = sum([batch.remaining_quantity for batch in p.stock_batches.all()]) or Decimal('0.000')
            pushed_from_this_pr = sum([alloc.quantity_allocated for batch in p.stock_batches.all() for alloc in batch.allocations.all()]) or Decimal('0.000')
            
            vendor_returned_qty = Decimal('0.000')
            if p.purchase_request:
                vendor_returned_qty = VendorReturnItem.objects.filter(
                    vendor_return__purchase_request=p.purchase_request,
                    item=p.item
                ).aggregate(s=Sum('quantity'))['s'] or Decimal('0.000')
                
            adjusted_wasted = bought_qty - remaining_in_stock - pushed_from_this_pr - vendor_returned_qty
            
            pr_number = p.purchase_request.pr_reference if p.purchase_request else 'Direct Purchase'
            avg_unit_incl_tax = total_price / bought_qty if bought_qty > 0 else Decimal('0.00')
            
            creator_name = 'N/A'
            if p.purchase_request:
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
            
            od = p.purchase_request.order_date if p.purchase_request else None
            pd = p.receipt.purchase_date if p.receipt else getattr(p, 'purchase_date', None)
            
            results.append({
                'id': p.id,
                'item_name': p.item.name,
                'pr_number': pr_number,
                'order_date': od.date().strftime('%Y-%m-%d') if hasattr(od, 'date') else od,
                'purchase_date': pd.date().strftime('%Y-%m-%d') if hasattr(pd, 'date') else pd,
                'vendor_name': p.vendor.name if p.vendor else "N/A",
                'purchase_type': p.purchase_type or "N/A",
                'purchased_qty': float(bought_qty),
                'unit_price_base': float(unit_price),
                'tax_rate': f"{p.tax_rate}%" if p.tax_rate else "0%",
                'discount_value': f"{float(p.discount_value)} ({p.discount_type})" if getattr(p, 'discount_value', None) else "None",
                'unit_price_incl_tax': float(avg_unit_incl_tax),
                'total_pr_value': float(total_price),
                'tax_paid': float(tax_paid),
                'pushed_qty': float(pushed_from_this_pr),
                'pushed_value': float(pushed_from_this_pr * avg_unit_incl_tax),
                'vendor_returned_qty': float(vendor_returned_qty),
                'vendor_returned_value': float(vendor_returned_qty * avg_unit_incl_tax),
                'adjusted_qty': float(adjusted_wasted),
                'adjusted_value': float(adjusted_wasted * avg_unit_incl_tax),
                'remaining_qty': float(remaining_in_stock),
                'remaining_value': float(remaining_in_stock * avg_unit_incl_tax),
                'action_by': creator_name or 'N/A'
            })
            
        return paginator.get_paginated_response(results)

class MRAuditViewSet(viewsets.ViewSet):
    permission_classes = [CanViewReports]
    
    def list(self, request):
        item_id = request.query_params.get('item')
        start_date = request.query_params.get('start_date') or '2026-04-30'
        end_date = request.query_params.get('end_date')
        
        queryset = MaterialRequestItem.objects.filter(material_request__order_date__gte=start_date).order_by('-material_request__target_date')
        if item_id:
            queryset = queryset.filter(item_id=item_id)
        if end_date:
            queryset = queryset.filter(material_request__order_date__lte=end_date)
            
        queryset = queryset.select_related('item', 'material_request').prefetch_related('batch_allocations__stock_batch__procurement__purchase_request')
        
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        
        results = []
        for mr_item in page:
            mr = mr_item.material_request
            req_qty = mr_item.quantity or Decimal('0.00')
            iss_qty = mr_item.quantity_issued or Decimal('0.00')
            ret_qty = mr_item.quantity_returned or Decimal('0.00')
            cons_qty = iss_qty - ret_qty
            cost = mr_item.consumption_total_cost or Decimal('0.00')
            
            person_count = mr.actual_user_count if mr.actual_user_count else mr.user_count
            if cons_qty <= 0:
                cost = Decimal('0.00')
                person_count = 0
            
            pr_sources = []
            for alloc in mr_item.batch_allocations.all():
                sb = alloc.stock_batch
                if sb.procurement and sb.procurement.purchase_request:
                    pr_sources.append(sb.procurement.purchase_request.pr_reference)
                elif sb.procurement:
                    pr_sources.append("Direct Purchase")
                else:
                    pr_sources.append("Opening Stock")
                    
            per_head = float(cost) / person_count if person_count and person_count > 0 else 0.0
            
            results.append({
                'id': mr_item.id,
                'item_name': mr_item.item.name,
                'mr_number': mr.mr_number,
                'target_date': mr.target_date.strftime('%Y-%m-%d') if mr.target_date else None,
                'status': mr.status,
                'person_count': person_count,
                'requested_qty': float(req_qty),
                'issued_qty': float(iss_qty),
                'returned_qty': float(ret_qty),
                'consumed_qty': float(cons_qty),
                'consumption_cost': float(cost),
                'per_head_cost': float(per_head),
                'pushed_from_pr': ", ".join(list(set(pr_sources)))
            })
            
        return paginator.get_paginated_response(results)

class AdjustmentAuditViewSet(viewsets.ViewSet):
    permission_classes = [CanViewReports]
    
    def list(self, request):
        item_id = request.query_params.get('item')
        start_date = request.query_params.get('start_date') or '2026-04-30'
        end_date = request.query_params.get('end_date')
        
        queryset = StockMovement.objects.filter(type__in=['WASTAGE', 'ADJUSTMENT']).order_by('-movement_date')
        if start_date:
            queryset = queryset.filter(movement_date__date__gte=start_date)
        if item_id:
            queryset = queryset.filter(item_id=item_id)
        if end_date:
            queryset = queryset.filter(movement_date__date__lte=end_date)
            
        queryset = queryset.select_related('item', 'user')
        
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        
        import re
        all_batches = StockBatch.objects.select_related('procurement__purchase_request').all()
        batch_dict = {b.id: b for b in all_batches}
        latest_batch_dict = {}
        for b in sorted(all_batches, key=lambda x: (x.created_at if x.created_at else x.batch_date)):
            latest_batch_dict[b.item_id] = b.unit_price
            
        movement_types = dict(StockMovement.MOVEMENT_TYPES)
            
        results = []
        for adj in page:
            batch_details = adj.reference_id or "FIFO/Legacy"
            adj_unit_cost = Decimal('0.00')
            
            if "Batch" in batch_details:
                try:
                    batch_ids = [int(x) for x in re.findall(r'Batch (\d+)', batch_details)]
                    if batch_ids:
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
            
            results.append({
                'id': adj.id,
                'date': adj.movement_date.strftime('%Y-%m-%d %H:%M:%S'),
                'item_name': adj.item.name,
                'quantity': float(adj.quantity),
                'uom': adj.item.unit,
                'adjustment_value': float(adj_value),
                'adjustment_type': movement_types.get(adj.type, adj.type),
                'batch_details': batch_details,
                'remarks': adj.remarks or "",
                'user': adj.user.username if adj.user else "System"
            })
            
        return paginator.get_paginated_response(results)

class ItemSummaryViewSet(viewsets.ViewSet):
    permission_classes = [CanViewReports]

    def list(self, request):
        item_id = request.query_params.get('item')
        start_date = request.query_params.get('start_date') or '2026-04-30'
        end_date = request.query_params.get('end_date')

        items = Item.objects.all().order_by('name')
        if item_id:
            items = items.filter(id=item_id)
            
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(items, request)
        
        page_item_ids = [item.id for item in page]
        
        from .models import Procurement, VendorReturnItem
        from django.db.models import Sum
        from decimal import Decimal
        
        procs_qs = Procurement.objects.filter(item_id__in=page_item_ids, purchase_date__gte=start_date).prefetch_related(
            'stock_batches', 'stock_batches__allocations'
        )
        if end_date:
            procs_qs = procs_qs.filter(purchase_date__lte=end_date)
            
        item_summaries = {}
        for item in page:
            item_summaries[item.id] = {
                'purchased_qty': Decimal('0.00'), 'purchased_val': Decimal('0.00'),
                'pushed_qty': Decimal('0.00'), 'pushed_val': Decimal('0.00'),
                'adjusted_qty': Decimal('0.00'), 'adjusted_val': Decimal('0.00'),
                'returned_qty': Decimal('0.00'), 'returned_val': Decimal('0.00'),
                'remaining_qty': Decimal('0.00'), 'remaining_val': Decimal('0.00'),
            }
            
        # Optimization: Pre-fetch vendor returns for these procurements
        pr_ids = list(set([p.purchase_request_id for p in procs_qs if p.purchase_request_id]))
        vr_qs = VendorReturnItem.objects.filter(vendor_return__purchase_request_id__in=pr_ids).values('vendor_return__purchase_request_id', 'item_id').annotate(qty=Sum('quantity'))
        vr_map = {(x['vendor_return__purchase_request_id'], x['item_id']): x['qty'] for x in vr_qs}
            
        for p in procs_qs:
            bought_qty = p.quantity or Decimal('0.000')
            unit_price = p.unit_price or Decimal('0.00')
            total_price = p.total_price or Decimal('0.00')
            
            remaining_in_stock = sum([batch.remaining_quantity for batch in p.stock_batches.all()]) or Decimal('0.000')
            pushed_from_this_pr = sum([alloc.quantity_allocated for batch in p.stock_batches.all() for alloc in batch.allocations.all()]) or Decimal('0.000')
            
            vendor_returned_qty = Decimal('0.000')
            if p.purchase_request_id:
                vendor_returned_qty = vr_map.get((p.purchase_request_id, p.item_id), Decimal('0.000'))
                
            adjusted_wasted = bought_qty - remaining_in_stock - pushed_from_this_pr - vendor_returned_qty
            
            avg_unit_incl_tax = total_price / bought_qty if bought_qty > 0 else Decimal('0.00')
            
            remaining_value = remaining_in_stock * avg_unit_incl_tax
            adjusted_value = adjusted_wasted * avg_unit_incl_tax
            pushed_value = pushed_from_this_pr * avg_unit_incl_tax
            vendor_returned_value = vendor_returned_qty * avg_unit_incl_tax
            
            item_summaries[p.item_id]['purchased_qty'] += bought_qty
            item_summaries[p.item_id]['purchased_val'] += total_price
            item_summaries[p.item_id]['pushed_qty'] += pushed_from_this_pr
            item_summaries[p.item_id]['pushed_val'] += pushed_value
            item_summaries[p.item_id]['adjusted_qty'] += adjusted_wasted
            item_summaries[p.item_id]['adjusted_val'] += adjusted_value
            item_summaries[p.item_id]['returned_qty'] += vendor_returned_qty
            item_summaries[p.item_id]['returned_val'] += vendor_returned_value
            item_summaries[p.item_id]['remaining_qty'] += remaining_in_stock
            item_summaries[p.item_id]['remaining_val'] += remaining_value
            
        results = []
        for item in page:
            s = item_summaries[item.id]
            results.append({
                'id': item.id,
                'item_name': item.name,
                'purchased_qty': float(s['purchased_qty']),
                'purchased_value': float(s['purchased_val']),
                'pushed_qty': float(s['pushed_qty']),
                'pushed_value': float(s['pushed_val']),
                'vendor_returned_qty': float(s['returned_qty']),
                'vendor_returned_value': float(s['returned_val']),
                'adjusted_qty': float(s['adjusted_qty']),
                'adjusted_value': float(s['adjusted_val']),
                'remaining_qty': float(s['remaining_qty']),
                'remaining_value': float(s['remaining_val']),
            })
            
        return paginator.get_paginated_response(results)

class VendorReturnsAuditViewSet(viewsets.ViewSet):
    permission_classes = [CanViewReports]

    def list(self, request):
        item_id = request.query_params.get('item')
        start_date = request.query_params.get('start_date') or '2026-04-30'
        end_date = request.query_params.get('end_date')

        from .models import VendorReturnItem, Procurement
        queryset = VendorReturnItem.objects.filter(vendor_return__return_date__gte=start_date).order_by('-vendor_return__return_date')
        if item_id:
            queryset = queryset.filter(item_id=item_id)
        if end_date:
            queryset = queryset.filter(vendor_return__return_date__lte=end_date)
            
        queryset = queryset.select_related('item', 'vendor_return__vendor', 'vendor_return__purchase_request', 'vendor_return__created_by')
        
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        
        results = []
        for vri in page:
            vr = vri.vendor_return
            unit_price = Decimal('0.00')
            if vr.purchase_request:
                proc = Procurement.objects.filter(purchase_request=vr.purchase_request, item=vri.item).first()
                if proc and proc.quantity and proc.quantity > 0:
                    unit_price = proc.total_price / proc.quantity
            
            results.append({
                'id': vri.id,
                'vendor_name': vr.vendor.name if vr.vendor else "N/A",
                'pr_number': vr.purchase_request.pr_reference if vr.purchase_request else "N/A",
                'return_date': vr.return_date.strftime('%Y-%m-%d') if vr.return_date else None,
                'item_name': vri.item.name,
                'return_qty': float(vri.quantity),
                'unit_price': float(unit_price),
                'return_value': float(Decimal(str(vri.quantity)) * unit_price),
                'reason': vri.reason or vr.remarks or "",
                'action_by': vr.created_by.username if getattr(vr, 'created_by', None) else "N/A"
            })
            
        return paginator.get_paginated_response(results)
