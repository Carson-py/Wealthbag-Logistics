from django.db.models import Sum, F, Q
from django.db import transaction
from django.utils import timezone
from decimal import Decimal
from datetime import datetime
from typing import List, Dict, Tuple, Any, Optional
import uuid
from .models import StockEntry, StockAdjustment, BranchStock, StockTransfer, StockTransferItem, StockEntryGroup
from products.models import Product
from organization.models import Warehouse, Branch


def _make_serializable(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a dictionary to a JSON-serializable format.
    Converts model instances to their IDs.
    """
    serializable_data = {}
    for key, value in data.items():
        if hasattr(value, 'pk'):  # It's a model instance
            serializable_data[key] = value.pk
        elif hasattr(value, 'id') and not isinstance(value, (str, int, float, bool, type(None))):  # Model instance with id
            serializable_data[key] = value.id
        elif isinstance(value, Decimal):
            serializable_data[key] = str(value)
        else:
            serializable_data[key] = value
    return serializable_data


def _resolve_reorder_level(explicit_level: Optional[int], original_stock_entry=None, original_branch_stock=None) -> int:
    """
    Determine the reorder level to apply to a new stock record.
    Preference order:
        1. Explicit level provided in the request/transfer item.
        2. Original stock entry/branch stock used for the transfer.
        3. Default of 0.
    """
    if explicit_level is not None:
        return explicit_level
    if original_stock_entry is not None:
        return original_stock_entry.reorder_level
    if original_branch_stock is not None:
        return original_branch_stock.reorder_level
    return 0


def _calculate_weighted_purchase_price(entries_used: List[Tuple[Any, Decimal]]) -> Decimal:
    """
    Calculate the weighted average purchase price based on the entries used during transfer.
    """
    total_quantity = Decimal('0')
    total_cost = Decimal('0')
    
    for entry, quantity_used in entries_used:
        if quantity_used is None or quantity_used == 0:
            continue
        entry_price = getattr(entry, 'purchase_price', None)
        if entry_price is None:
            continue
        total_quantity += quantity_used
        total_cost += entry_price * quantity_used
    
    if total_quantity == 0:
        return Decimal('0')
    return total_cost / total_quantity


def _preview_purchase_price(
    product,
    quantity: Decimal,
    source_warehouse: Optional[Warehouse] = None,
    source_branch: Optional[Branch] = None,
) -> Decimal:
    """
    Estimate the purchase price for a pending transfer by looking at the source stock layers (FIFO).
    """
    if source_warehouse:
        entries = StockEntry.objects.filter(
            product=product,
            warehouse=source_warehouse,
            quantity__gt=0
        ).order_by('received_date', 'created_at')
    elif source_branch:
        entries = BranchStock.objects.filter(
            product=product,
            branch=source_branch,
            quantity__gt=0
        ).order_by('received_date', 'created_at')
    else:
        return Decimal('0')
    
    remaining = quantity
    total_quantity = Decimal('0')
    total_cost = Decimal('0')
    
    for entry in entries:
        if remaining <= 0:
            break
        take = entry.quantity if entry.quantity <= remaining else remaining
        total_quantity += take
        total_cost += take * entry.purchase_price
        remaining -= take
    
    if total_quantity == 0:
        return Decimal('0')
    return total_cost / total_quantity


def generate_unique_batch_number() -> str:
    """
    Generate a unique batch number for stock entries.
    Format: BATCH-YYYYMMDD-UUID (first 8 chars of UUID for readability)
    
    Ensures uniqueness across both StockEntry and BranchStock models.
    """
    date_prefix = datetime.now().strftime('%Y%m%d')
    uuid_part = str(uuid.uuid4())[:8].upper()
    batch_number = f'BATCH-{date_prefix}-{uuid_part}'
    
    # Ensure uniqueness - check both StockEntry and BranchStock
    max_attempts = 10
    attempts = 0
    
    while attempts < max_attempts:
        exists_in_stock = StockEntry.objects.filter(batch_number=batch_number).exists()
        exists_in_branch = BranchStock.objects.filter(batch_number=batch_number).exists()
        
        if not exists_in_stock and not exists_in_branch:
            return batch_number
        
        # If exists, generate new one
        uuid_part = str(uuid.uuid4())[:8].upper()
        batch_number = f'BATCH-{date_prefix}-{uuid_part}'
        attempts += 1
    
    # Fallback: add timestamp to ensure uniqueness
    timestamp = int(timezone.now().timestamp() * 1000) % 1000000
    return f'BATCH-{date_prefix}-{uuid_part}-{timestamp}'


def add_stock_to_warehouse(
    product_id: int,
    warehouse_id: int,
    quantity: Decimal,
    purchase_price: Decimal,
    reorder_level: Optional[int] = None,
    supplier_id: int = None,
    batch_number: str = None,
    notes: str = '',
    created_by=None
) -> StockEntry:
    """
    Add new stock to warehouse with purchase price.
    Creates a new StockEntry and updates ProductStock.
    
    If batch_number is not provided, it will be auto-generated to ensure uniqueness.
    """
    product = Product.objects.get(pk=product_id)
    warehouse = Warehouse.objects.get(pk=warehouse_id)
    reorder_level = reorder_level if reorder_level is not None else 0
    
    # Auto-generate batch number if not provided
    if not batch_number:
        batch_number = generate_unique_batch_number()
    
    with transaction.atomic():
        # Create stock entry
        stock_entry = StockEntry.objects.create(
            product=product,
            warehouse=warehouse,
            quantity=quantity,
            reorder_level=reorder_level,
            purchase_price=purchase_price,
            supplier_id=supplier_id,
            batch_number=batch_number,
            notes=notes,
            created_by=created_by,
            received_date=timezone.now()
        )
        
        # Create adjustment record
        StockAdjustment.objects.create(
            product=product,
            warehouse=warehouse,
            adjustment_type='addition',
            quantity=quantity,
            purchase_price=purchase_price,
            reason=f'Stock added: {notes}' if notes else 'Stock added',
            created_by=created_by
        )
    
    return stock_entry


def add_multi_product_stock_to_warehouse(
    warehouse_id: int,
    items_data: List[Dict],
    reference_number: str = None,
    group_notes: str = '',
    created_by=None
) -> StockEntryGroup:
    """
    Add multiple products to warehouse in a single operation.
    
    Args:
        warehouse_id: Warehouse to add stock to
        items_data: List of dictionaries, each containing:
            - product_id: int
            - quantity: Decimal
            - purchase_price: Decimal
            - supplier_id: int (optional)
            - batch_number: str (optional)
            - notes: str (optional)
        reference_number: Optional reference number (auto-generated if not provided)
        group_notes: Notes for the entire entry group
        created_by: User creating the entries
    
    Returns:
        StockEntryGroup instance with all entries
    """
    warehouse = Warehouse.objects.get(pk=warehouse_id)
    
    with transaction.atomic():
        # Create entry group
        entry_group = StockEntryGroup(
            warehouse=warehouse,
            reference_number=reference_number or '',
            notes=group_notes,
            created_by=created_by
        )
        entry_group.save()  # This will auto-generate reference_number if empty
        
        # Create stock entries for each item
        created_entries = []
        for item_data in items_data:
            product = Product.objects.get(pk=item_data['product_id'])
            quantity = item_data['quantity']
            item_reorder_level = item_data.get('reorder_level')
            reorder_level = item_data.get('reorder_level')
            if reorder_level is None:
                reorder_level = 0
            purchase_price = item_data['purchase_price']
            supplier_id = item_data.get('supplier_id')
            batch_number = item_data.get('batch_number')
            item_notes = item_data.get('notes', '')
            
            # Auto-generate batch number if not provided
            if not batch_number:
                batch_number = generate_unique_batch_number()
            
            # Create stock entry
            stock_entry = StockEntry.objects.create(
                product=product,
                warehouse=warehouse,
                quantity=quantity,
                reorder_level=reorder_level,
                purchase_price=purchase_price,
                supplier_id=supplier_id,
                batch_number=batch_number,
                notes=item_notes,
                created_by=created_by,
                received_date=timezone.now(),
                entry_group=entry_group,
                is_initial_stock=True
            )
            
            # Create adjustment record
            StockAdjustment.objects.create(
                product=product,
                warehouse=warehouse,
                adjustment_type='addition',
                quantity=quantity,
                purchase_price=purchase_price,
                reason=f'Stock added (Group: {entry_group.reference_number}): {item_notes}' if item_notes else f'Stock added (Group: {entry_group.reference_number})',
                created_by=created_by
            )
            
            created_entries.append(stock_entry)
    
    return entry_group


def remove_stock_from_warehouse(
    product_id: int,
    warehouse_id: int,
    quantity: Decimal,
    reason: str = '',
    adjustment_type: str = 'removal',
    created_by=None
) -> StockAdjustment:
    """
    Remove stock from warehouse.
    Uses FIFO (First In First Out) method - removes oldest stock first.
    """
    product = Product.objects.get(pk=product_id)
    warehouse = Warehouse.objects.get(pk=warehouse_id)
    
    with transaction.atomic():
        # Remove stock using FIFO (oldest first)
        remaining_to_remove = quantity
        stock_entries = StockEntry.objects.filter(
            product=product,
            warehouse=warehouse
        ).order_by('received_date', 'created_at')
        
        for entry in stock_entries:
            if remaining_to_remove <= 0:
                break
            
            if entry.quantity > 0:
                if entry.quantity >= remaining_to_remove:
                    entry.quantity -= remaining_to_remove
                    remaining_to_remove = Decimal('0')
                else:
                    remaining_to_remove -= entry.quantity
                    entry.quantity = Decimal('0')
                
                entry.save()
        
        # Create adjustment record
        adjustment = StockAdjustment.objects.create(
            product=product,
            warehouse=warehouse,
            adjustment_type=adjustment_type,
            quantity=-quantity,  # Negative for removal
            reason=reason or f'Stock removed: {adjustment_type}',
            created_by=created_by
        )
    
    return adjustment


def bulk_add_stock_to_warehouse(stock_entries_data: List[Dict], created_by=None) -> Tuple[List[StockEntry], List[Dict]]:
    """
    Bulk add stock entries to warehouse.
    
    Args:
        stock_entries_data: List of dictionaries containing stock entry information
            Each dict should have: product_id, warehouse_id, quantity, purchase_price,
            supplier_id (optional), batch_number (optional), notes (optional)
        created_by: User creating the stock entries
    
    Returns:
        Tuple of (created_entries, errors)
        - created_entries: List of successfully created StockEntry objects
        - errors: List of error dictionaries with 'index' and 'error' keys
    """
    created_entries = []
    errors = []
    
    with transaction.atomic():
        for index, entry_data in enumerate(stock_entries_data):
            try:
                stock_entry = add_stock_to_warehouse(
                    product_id=entry_data.get('product_id'),
                    warehouse_id=entry_data.get('warehouse_id'),
                    quantity=entry_data.get('quantity'),
                    reorder_level=entry_data.get('reorder_level'),
                    purchase_price=entry_data.get('purchase_price'),
                    supplier_id=entry_data.get('supplier_id'),
                    batch_number=entry_data.get('batch_number'),
                    notes=entry_data.get('notes', ''),
                    created_by=created_by
                )
                created_entries.append(stock_entry)
                
            except Exception as e:
                # Convert entry_data to serializable format
                serializable_data = _make_serializable(entry_data)
                
                errors.append({
                    'index': index,
                    'data': serializable_data,
                    'error': str(e)
                })
                # Continue with next entry even if one fails
    
    return created_entries, errors


def get_warehouse_stock_summary(warehouse_id: int, product_id: int = None):
    """
    Get stock summary for a warehouse, showing all batches with different purchase prices.
    """
    entries = StockEntry.objects.filter(warehouse_id=warehouse_id, quantity__gt=0)
    
    if product_id:
        entries = entries.filter(product_id=product_id)
    
    # Group by product and purchase price
    summary = {}
    for entry in entries:
        key = (entry.product_id, entry.purchase_price)
        if key not in summary:
            summary[key] = {
                'product_id': entry.product_id,
                'product_name': entry.product.name,
                'product_sku': entry.product.sku,
                'purchase_price': entry.purchase_price,
                'total_quantity': Decimal('0'),
                'total_cost': Decimal('0'),
                'batches': []
            }
        
        summary[key]['total_quantity'] += entry.quantity
        summary[key]['total_cost'] += entry.total_cost
        summary[key]['batches'].append({
            'id': entry.id,
            'quantity': entry.quantity,
            'batch_number': entry.batch_number,
            'received_date': entry.received_date,
        })
    
    return list(summary.values())


def get_average_purchase_price(warehouse_id: int, product_id: int) -> Decimal:
    """
    Calculate weighted average purchase price for a product in a warehouse.
    """
    entries = StockEntry.objects.filter(
        warehouse_id=warehouse_id,
        product_id=product_id,
        quantity__gt=0
    )
    
    if not entries.exists():
        # Fallback to product's default purchase price
        try:
            product = Product.objects.get(pk=product_id)
            return product.purchase_price
        except Product.DoesNotExist:
            return Decimal('0')
    
    total_cost = sum(entry.total_cost for entry in entries)
    total_quantity = sum(entry.quantity for entry in entries)
    
    if total_quantity > 0:
        return total_cost / total_quantity
    return Decimal('0')


def get_stock_value(warehouse_id: int, product_id: int = None) -> dict:
    """
    Calculate total stock value in a warehouse.
    """
    entries = StockEntry.objects.filter(warehouse_id=warehouse_id, quantity__gt=0)
    
    if product_id:
        entries = entries.filter(product_id=product_id)
    
    total_value = sum(entry.total_cost for entry in entries)
    total_quantity = sum(entry.quantity for entry in entries)
    
    return {
        'warehouse_id': warehouse_id,
        'total_quantity': total_quantity,
        'total_value': total_value,
        'average_purchase_price': total_value / total_quantity if total_quantity > 0 else Decimal('0'),
    }


# ========== Branch Stock Functions ==========

def add_stock_to_branch(
    product_id: int,
    branch_id: int,
    quantity: Decimal,
    purchase_price: Decimal,
    selling_price: Decimal,
    supplier_id: int = None,
    batch_number: str = None,
    notes: str = '',
    created_by=None
) -> BranchStock:
    """
    Add new stock to branch with purchase and selling prices.
    Creates a new BranchStock entry.
    
    If batch_number is not provided, it will be auto-generated to ensure uniqueness.
    """
    product = Product.objects.get(pk=product_id)
    branch = Branch.objects.get(pk=branch_id)
    
    # Auto-generate batch number if not provided
    if not batch_number:
        batch_number = generate_unique_batch_number()
    
    with transaction.atomic():
        branch_stock = BranchStock.objects.create(
            product=product,
            branch=branch,
            quantity=quantity,
            purchase_price=purchase_price,
            selling_price=selling_price,
            supplier_id=supplier_id,
            batch_number=batch_number,
            notes=notes,
            created_by=created_by,
            received_date=timezone.now()
        )
    
    return branch_stock


def remove_stock_from_branch(
    product_id: int,
    branch_id: int,
    quantity: Decimal,
    reason: str = '',
    created_by=None
) -> None:
    """
    Remove stock from branch.
    Uses FIFO (First In First Out) method - removes oldest stock first.
    """
    product = Product.objects.get(pk=product_id)
    branch = Branch.objects.get(pk=branch_id)
    
    with transaction.atomic():
        # Check available stock
        total_available = BranchStock.objects.filter(
            product=product,
            branch=branch
        ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
        
        if total_available < quantity:
            raise ValueError(f'Insufficient stock. Available: {total_available}, Requested: {quantity}')
        
        # Remove stock using FIFO (oldest first)
        remaining_to_remove = quantity
        stock_entries = BranchStock.objects.filter(
            product=product,
            branch=branch,
            quantity__gt=0
        ).order_by('received_date', 'created_at')
        
        for entry in stock_entries:
            if remaining_to_remove <= 0:
                break
            
            if entry.quantity > 0:
                if entry.quantity >= remaining_to_remove:
                    entry.quantity -= remaining_to_remove
                    remaining_to_remove = Decimal('0')
                else:
                    remaining_to_remove -= entry.quantity
                    entry.quantity = Decimal('0')
                
                entry.save()


def get_branch_stock_summary(branch_id: int, product_id: int = None):
    """
    Get stock summary for a branch, showing all batches with different purchase prices.
    """
    entries = BranchStock.objects.filter(branch_id=branch_id, quantity__gt=0)
    
    if product_id:
        entries = entries.filter(product_id=product_id)
    
    # Group by product and purchase price
    summary = {}
    for entry in entries:
        key = (entry.product_id, entry.purchase_price)
        if key not in summary:
            summary[key] = {
                'product_id': entry.product_id,
                'product_name': entry.product.name,
                'product_sku': entry.product.sku,
                'purchase_price': entry.purchase_price,
                'selling_price': entry.selling_price,
                'total_quantity': Decimal('0'),
                'total_cost': Decimal('0'),
                'batches': []
            }
        
        summary[key]['total_quantity'] += entry.quantity
        summary[key]['total_cost'] += entry.total_cost
        summary[key]['batches'].append({
            'id': entry.id,
            'quantity': entry.quantity,
            'batch_number': entry.batch_number,
            'received_date': entry.received_date,
        })
    
    return list(summary.values())


def get_branch_stock_value(branch_id: int, product_id: int = None) -> dict:
    """
    Calculate total stock value in a branch.
    """
    entries = BranchStock.objects.filter(branch_id=branch_id, quantity__gt=0)
    
    if product_id:
        entries = entries.filter(product_id=product_id)
    
    total_value = sum(entry.total_cost for entry in entries)
    total_quantity = sum(entry.quantity for entry in entries)
    
    return {
        'branch_id': branch_id,
        'total_quantity': total_quantity,
        'total_value': total_value,
        'average_purchase_price': total_value / total_quantity if total_quantity > 0 else Decimal('0'),
    }


# ========== Stock Transfer Functions ==========

def create_stock_transfer(
    transfer_type: str,
    product_id: int,
    quantity: Decimal,
    reorder_level: Optional[int] = None,
    source_warehouse_id: int = None,
    source_branch_id: int = None,
    destination_warehouse_id: int = None,
    destination_branch_id: int = None,
    supplier_id: int = None,
    batch_number: str = None,
    reference_number: str = None,
    notes: str = '',
    selling_price: Optional[Decimal] = None,
    created_by=None
) -> StockTransfer:
    """
    Create a new stock transfer request.
    Purchase price is determined automatically based on the source stock when the transfer is completed.
    """
    product = Product.objects.get(pk=product_id)
    
    # Validate and get source/destination based on transfer type
    source_warehouse = None
    source_branch = None
    destination_warehouse = None
    destination_branch = None
    
    if transfer_type == 'warehouse_to_warehouse':
        source_warehouse = Warehouse.objects.get(pk=source_warehouse_id)
        destination_warehouse = Warehouse.objects.get(pk=destination_warehouse_id)
        # Check available stock
        available = StockEntry.objects.filter(
            product=product,
            warehouse=source_warehouse,
            quantity__gt=0
        ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
        if available < quantity:
            raise ValueError(f'Insufficient stock in warehouse. Available: {available}, Requested: {quantity}')
    
    elif transfer_type == 'warehouse_to_branch':
        source_warehouse = Warehouse.objects.get(pk=source_warehouse_id)
        destination_branch = Branch.objects.get(pk=destination_branch_id)
        # Check available stock
        available = StockEntry.objects.filter(
            product=product,
            warehouse=source_warehouse,
            quantity__gt=0
        ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
        if available < quantity:
            raise ValueError(f'Insufficient stock in warehouse. Available: {available}, Requested: {quantity}')
    
    elif transfer_type == 'branch_to_branch':
        source_branch = Branch.objects.get(pk=source_branch_id)
        destination_branch = Branch.objects.get(pk=destination_branch_id)
        # Check available stock
        available = BranchStock.objects.filter(
            product=product,
            branch=source_branch,
            quantity__gt=0
        ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
        if available < quantity:
            raise ValueError(f'Insufficient stock in branch. Available: {available}, Requested: {quantity}')
    
    elif transfer_type == 'branch_to_warehouse':
        source_branch = Branch.objects.get(pk=source_branch_id)
        destination_warehouse = Warehouse.objects.get(pk=destination_warehouse_id)
        # Check available stock
        available = BranchStock.objects.filter(
            product=product,
            branch=source_branch,
            quantity__gt=0
        ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
        if available < quantity:
            raise ValueError(f'Insufficient stock in branch. Available: {available}, Requested: {quantity}')
    
    if destination_branch and selling_price is None:
        raise ValueError('Selling price is required when transferring stock to a branch.')

    preview_purchase_price = _preview_purchase_price(
        product=product,
        quantity=quantity,
        source_warehouse=source_warehouse,
        source_branch=source_branch
    )
    if preview_purchase_price == 0:
        raise ValueError('Unable to determine purchase price for the selected stock. Please ensure source stock has purchase prices.')

    with transaction.atomic():
        # reference_number will be auto-generated in save() if not provided
        transfer = StockTransfer(
            transfer_type=transfer_type,
            product=product,
            quantity=quantity,
            purchase_price=preview_purchase_price,
            reorder_level=reorder_level,
            selling_price=selling_price,
            supplier_id=supplier_id,
            batch_number=batch_number or '',
            source_warehouse=source_warehouse,
            source_branch=source_branch,
            destination_warehouse=destination_warehouse,
            destination_branch=destination_branch,
            reference_number=reference_number or '',  # Will be auto-generated in save() if empty
            notes=notes,
            status='pending',
            created_by=created_by
        )
        transfer.full_clean()  # Validate the transfer
        transfer.save()  # This will auto-generate reference_number if empty
    
    return transfer


def create_multi_product_stock_transfer(
    transfer_type: str,
    items_data: List[Dict],
    source_warehouse_id: int = None,
    source_branch_id: int = None,
    destination_warehouse_id: int = None,
    destination_branch_id: int = None,
    reference_number: str = None,
    notes: str = '',
    created_by=None
) -> StockTransfer:
    """
    Create a new stock transfer request with multiple products.
    
    Args:
        transfer_type: Type of transfer
        items_data: List of dictionaries, each containing:
            - product_id: int
            - quantity: Decimal
            - selling_price: Decimal (required when transferring to a branch)
            - supplier_id: int (optional)
            - batch_number: str (optional)
            - notes: str (optional)
        source_warehouse_id, source_branch_id: Source location (one required)
        destination_warehouse_id, destination_branch_id: Destination location (one required)
        reference_number: Optional reference number (auto-generated if not provided)
        notes: Transfer notes
        created_by: User creating the transfer
    
    Returns:
        StockTransfer instance with items
    """
    # Validate and get source/destination based on transfer type
    source_warehouse = None
    source_branch = None
    destination_warehouse = None
    destination_branch = None
    
    if transfer_type == 'warehouse_to_warehouse':
        source_warehouse = Warehouse.objects.get(pk=source_warehouse_id)
        destination_warehouse = Warehouse.objects.get(pk=destination_warehouse_id)
    elif transfer_type == 'warehouse_to_branch':
        source_warehouse = Warehouse.objects.get(pk=source_warehouse_id)
        destination_branch = Branch.objects.get(pk=destination_branch_id)
    elif transfer_type == 'branch_to_branch':
        source_branch = Branch.objects.get(pk=source_branch_id)
        destination_branch = Branch.objects.get(pk=destination_branch_id)
    elif transfer_type == 'branch_to_warehouse':
        source_branch = Branch.objects.get(pk=source_branch_id)
        destination_warehouse = Warehouse.objects.get(pk=destination_warehouse_id)
    
    with transaction.atomic():
        # Create transfer record (without product fields for multi-product)
        transfer = StockTransfer(
            transfer_type=transfer_type,
            source_warehouse=source_warehouse,
            source_branch=source_branch,
            destination_warehouse=destination_warehouse,
            destination_branch=destination_branch,
            reference_number=reference_number or '',
            notes=notes,
            status='pending',
            created_by=created_by
        )
        transfer.full_clean()
        transfer.save()  # This will auto-generate reference_number if empty
        
        # Check stock availability and create items
        for item_data in items_data:
            product = Product.objects.get(pk=item_data['product_id'])
            quantity = item_data['quantity']
            item_reorder_level = item_data.get('reorder_level')
            item_selling_price = item_data.get('selling_price')
            
            if destination_branch and item_selling_price is None:
                raise ValueError('Selling price is required for each item when transferring stock to a branch.')
            
            # Check available stock at source
            if source_warehouse:
                available = StockEntry.objects.filter(
                    product=product,
                    warehouse=source_warehouse,
                    quantity__gt=0
                ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
                if available < quantity:
                    raise ValueError(f'Insufficient stock for {product.name} in warehouse. Available: {available}, Requested: {quantity}')
            elif source_branch:
                available = BranchStock.objects.filter(
                    product=product,
                    branch=source_branch,
                    quantity__gt=0
                ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
                if available < quantity:
                    raise ValueError(f'Insufficient stock for {product.name} in branch. Available: {available}, Requested: {quantity}')
            
            # Determine purchase price from source stock layers
            preview_purchase_price = _preview_purchase_price(
                product=product,
                quantity=quantity,
                source_warehouse=source_warehouse,
                source_branch=source_branch
            )
            if preview_purchase_price == 0:
                raise ValueError(f'Unable to determine purchase price for {product.name}. Ensure source stock has purchase prices.')
            
            # Create transfer item
            StockTransferItem.objects.create(
                transfer=transfer,
                product=product,
                quantity=quantity,
                purchase_price=preview_purchase_price,
                reorder_level=item_reorder_level,
                selling_price=item_selling_price,
                supplier_id=item_data.get('supplier_id'),
                batch_number=item_data.get('batch_number', ''),
                notes=item_data.get('notes', '')
            )
    
    return transfer


def complete_stock_transfer(transfer_id: int, completed_by=None) -> StockTransfer:
    """
    Complete a stock transfer by moving stock from source to destination.
    Supports both single-product (legacy) and multi-product transfers.
    """
    transfer = StockTransfer.objects.get(pk=transfer_id)
    
    if transfer.status == 'completed':
        raise ValueError('Transfer is already completed.')
    if transfer.status == 'cancelled':
        raise ValueError('Cannot complete a cancelled transfer.')
    
    with transaction.atomic():
        # Check if this is a multi-product transfer
        items = transfer.items.all() if hasattr(transfer, 'items') else []
        
        if items.exists():
            # Multi-product transfer - process each item
            _complete_multi_product_transfer(transfer, items, completed_by)
        else:
            # Single product transfer (legacy) - use existing logic
            _complete_single_product_transfer(transfer, completed_by)
        
        # Update transfer status
        transfer.status = 'completed'
        transfer.completed_by = completed_by
        transfer.completed_at = timezone.now()
        transfer.save()
    
    return transfer


def _complete_single_product_transfer(transfer: StockTransfer, completed_by=None):
    """Complete a single-product transfer (legacy support)"""
    with transaction.atomic():
        # Track original entries used in transfer (to preserve initial stock records)
        original_entries_used = []  # List of (entry, quantity_used) tuples
        original_batch_numbers = []  # Track original batch numbers
        
        # Remove stock from source using FIFO and track original entries
        if transfer.source_warehouse:
            # Remove from warehouse
            remaining_to_remove = transfer.quantity
            stock_entries = StockEntry.objects.filter(
                product=transfer.product,
                warehouse=transfer.source_warehouse,
                quantity__gt=0
            ).order_by('received_date', 'created_at')
            
            for entry in stock_entries:
                if remaining_to_remove <= 0:
                    break
                
                quantity_used = Decimal('0')
                if entry.quantity >= remaining_to_remove:
                    quantity_used = remaining_to_remove
                    entry.quantity -= remaining_to_remove
                    remaining_to_remove = Decimal('0')
                else:
                    quantity_used = entry.quantity
                    remaining_to_remove -= entry.quantity
                    entry.quantity = Decimal('0')
                
                # Track original entry and batch number
                original_entries_used.append((entry, quantity_used))
                if entry.batch_number:
                    original_batch_numbers.append(entry.batch_number)
                
                entry.save()
        
        elif transfer.source_branch:
            # Remove from branch
            remaining_to_remove = transfer.quantity
            stock_entries = BranchStock.objects.filter(
                product=transfer.product,
                branch=transfer.source_branch,
                quantity__gt=0
            ).order_by('received_date', 'created_at')
            
            for entry in stock_entries:
                if remaining_to_remove <= 0:
                    break
                
                quantity_used = Decimal('0')
                if entry.quantity >= remaining_to_remove:
                    quantity_used = remaining_to_remove
                    entry.quantity -= remaining_to_remove
                    remaining_to_remove = Decimal('0')
                else:
                    quantity_used = entry.quantity
                    remaining_to_remove -= entry.quantity
                    entry.quantity = Decimal('0')
                
                # Track original entry and batch number
                original_entries_used.append((entry, quantity_used))
                if entry.batch_number:
                    original_batch_numbers.append(entry.batch_number)
                
                entry.save()
        
        # Determine supplier - use transfer supplier or get from source stock
        supplier_id = transfer.supplier_id if transfer.supplier else None
        original_stock_entry = None
        original_branch_stock = None
        
        # Get the first original entry for linking (FIFO - oldest first)
        if original_entries_used:
            first_entry, _ = original_entries_used[0]
            if isinstance(first_entry, StockEntry):
                original_stock_entry = first_entry
                if not supplier_id and first_entry.supplier:
                    supplier_id = first_entry.supplier_id
            elif isinstance(first_entry, BranchStock):
                original_branch_stock = first_entry
                if not supplier_id and first_entry.supplier:
                    supplier_id = first_entry.supplier_id
        
        # Fallback: Try to get supplier from source stock if not found
        if not supplier_id:
            if transfer.source_warehouse:
                source_entry = StockEntry.objects.filter(
                    product=transfer.product,
                    warehouse=transfer.source_warehouse,
                    quantity__gt=0
                ).order_by('received_date', 'created_at').first()
                if source_entry and source_entry.supplier:
                    supplier_id = source_entry.supplier_id
            elif transfer.source_branch:
                source_entry = BranchStock.objects.filter(
                    product=transfer.product,
                    branch=transfer.source_branch,
                    quantity__gt=0
                ).order_by('received_date', 'created_at').first()
                if source_entry and source_entry.supplier:
                    supplier_id = source_entry.supplier_id
        
        # Build original batch number string
        original_batch_str = ', '.join(original_batch_numbers) if original_batch_numbers else (transfer.batch_number or '')
        destination_reorder_level = _resolve_reorder_level(
            transfer.reorder_level,
            original_stock_entry,
            original_branch_stock
        )
        destination_purchase_price = _calculate_weighted_purchase_price(original_entries_used)
        if destination_purchase_price == 0 and transfer.purchase_price:
            destination_purchase_price = transfer.purchase_price
        transfer.purchase_price = destination_purchase_price
        
        # Add stock to destination - always generate new unique batch number for transferred stock
        if transfer.destination_warehouse:
            # Add to warehouse - generate new unique batch number
            new_batch_number = generate_unique_batch_number()
            transfer_notes = f'Transferred from {transfer.source_warehouse.name if transfer.source_warehouse else transfer.source_branch.name}'
            if original_batch_str:
                transfer_notes += f' (Original batch: {original_batch_str})'
            if transfer.notes:
                transfer_notes += f': {transfer.notes}'
            
            StockEntry.objects.create(
                product=transfer.product,
                warehouse=transfer.destination_warehouse,
                quantity=transfer.quantity,
                purchase_price=destination_purchase_price,
                reorder_level=destination_reorder_level,
                supplier_id=supplier_id,
                batch_number=new_batch_number,
                original_batch_number=original_batch_str,
                notes=transfer_notes,
                created_by=completed_by,
                received_date=timezone.now(),
                is_initial_stock=False,  # This is transferred stock, not initial
                source_transfer=transfer,
                original_stock_entry=original_stock_entry  # Link to original warehouse entry
            )
        
        elif transfer.destination_branch:
            # Add to branch - need to get selling price from source or use a default
            selling_price = transfer.selling_price
            
            # Try to get selling price from source branch stock if available
            if selling_price is None and transfer.source_branch:
                source_stock = BranchStock.objects.filter(
                    product=transfer.product,
                    branch=transfer.source_branch,
                    quantity__gt=0
                ).first()
                if source_stock:
                    selling_price = source_stock.selling_price
            if selling_price is None:
                selling_price = destination_purchase_price
            
            # Generate new unique batch number for transferred stock
            new_batch_number = generate_unique_batch_number()
            transfer_notes = f'Transferred from {transfer.source_warehouse.name if transfer.source_warehouse else transfer.source_branch.name}'
            if original_batch_str:
                transfer_notes += f' (Original batch: {original_batch_str})'
            if transfer.notes:
                transfer_notes += f': {transfer.notes}'
            
            BranchStock.objects.create(
                product=transfer.product,
                branch=transfer.destination_branch,
                quantity=transfer.quantity,
                purchase_price=destination_purchase_price,
                selling_price=selling_price,
                reorder_level=destination_reorder_level,
                supplier_id=supplier_id,
                batch_number=new_batch_number,
                original_batch_number=original_batch_str,
                notes=transfer_notes,
                created_by=completed_by,
                received_date=timezone.now(),
                is_initial_stock=False,  # Branches only receive stock via transfers
                source_transfer=transfer,
                original_stock_entry=original_stock_entry,  # Link to original warehouse entry (if from warehouse)
                original_branch_stock=original_branch_stock  # Link to original branch stock (if from branch)
            )


def _complete_multi_product_transfer(transfer: StockTransfer, items, completed_by=None):
    """Complete a multi-product transfer by processing each item"""
    for item in items:
        original_entries_used = []
        original_batch_numbers = []
        
        # Remove stock from source using FIFO
        if transfer.source_warehouse:
            remaining_to_remove = item.quantity
            stock_entries = StockEntry.objects.filter(
                product=item.product,
                warehouse=transfer.source_warehouse,
                quantity__gt=0
            ).order_by('received_date', 'created_at')
            
            for entry in stock_entries:
                if remaining_to_remove <= 0:
                    break
                
                quantity_used = Decimal('0')
                if entry.quantity >= remaining_to_remove:
                    quantity_used = remaining_to_remove
                    entry.quantity -= remaining_to_remove
                    remaining_to_remove = Decimal('0')
                else:
                    quantity_used = entry.quantity
                    remaining_to_remove -= entry.quantity
                    entry.quantity = Decimal('0')
                
                original_entries_used.append((entry, quantity_used))
                if entry.batch_number:
                    original_batch_numbers.append(entry.batch_number)
                entry.save()
        
        elif transfer.source_branch:
            remaining_to_remove = item.quantity
            stock_entries = BranchStock.objects.filter(
                product=item.product,
                branch=transfer.source_branch,
                quantity__gt=0
            ).order_by('received_date', 'created_at')
            
            for entry in stock_entries:
                if remaining_to_remove <= 0:
                    break
                
                quantity_used = Decimal('0')
                if entry.quantity >= remaining_to_remove:
                    quantity_used = remaining_to_remove
                    entry.quantity -= remaining_to_remove
                    remaining_to_remove = Decimal('0')
                else:
                    quantity_used = entry.quantity
                    remaining_to_remove -= entry.quantity
                    entry.quantity = Decimal('0')
                
                original_entries_used.append((entry, quantity_used))
                if entry.batch_number:
                    original_batch_numbers.append(entry.batch_number)
                entry.save()
        
        # Determine supplier
        supplier_id = item.supplier_id if item.supplier else None
        original_stock_entry = None
        original_branch_stock = None
        
        if original_entries_used:
            first_entry, _ = original_entries_used[0]
            if isinstance(first_entry, StockEntry):
                original_stock_entry = first_entry
                if not supplier_id and first_entry.supplier:
                    supplier_id = first_entry.supplier_id
            elif isinstance(first_entry, BranchStock):
                original_branch_stock = first_entry
                if not supplier_id and first_entry.supplier:
                    supplier_id = first_entry.supplier_id
        
        if not supplier_id:
            if transfer.source_warehouse:
                source_entry = StockEntry.objects.filter(
                    product=item.product,
                    warehouse=transfer.source_warehouse,
                    quantity__gt=0
                ).order_by('received_date', 'created_at').first()
                if source_entry and source_entry.supplier:
                    supplier_id = source_entry.supplier_id
            elif transfer.source_branch:
                source_entry = BranchStock.objects.filter(
                    product=item.product,
                    branch=transfer.source_branch,
                    quantity__gt=0
                ).order_by('received_date', 'created_at').first()
                if source_entry and source_entry.supplier:
                    supplier_id = source_entry.supplier_id
        
        original_batch_str = ', '.join(original_batch_numbers) if original_batch_numbers else (item.batch_number or '')
        destination_reorder_level = _resolve_reorder_level(
            item.reorder_level,
            original_stock_entry,
            original_branch_stock
        )
        destination_purchase_price = _calculate_weighted_purchase_price(original_entries_used)
        if destination_purchase_price == 0 and item.purchase_price:
            destination_purchase_price = item.purchase_price
        item.purchase_price = destination_purchase_price
        item.save(update_fields=['purchase_price'])
        
        # Add stock to destination
        if transfer.destination_warehouse:
            new_batch_number = generate_unique_batch_number()
            transfer_notes = f'Transferred from {transfer.source_warehouse.name if transfer.source_warehouse else transfer.source_branch.name}'
            if original_batch_str:
                transfer_notes += f' (Original batch: {original_batch_str})'
            if item.notes:
                transfer_notes += f': {item.notes}'
            if transfer.notes:
                transfer_notes += f' | Transfer: {transfer.notes}'
            
            StockEntry.objects.create(
                product=item.product,
                warehouse=transfer.destination_warehouse,
                quantity=item.quantity,
                purchase_price=destination_purchase_price,
                reorder_level=destination_reorder_level,
                supplier_id=supplier_id,
                batch_number=new_batch_number,
                original_batch_number=original_batch_str,
                notes=transfer_notes,
                created_by=completed_by,
                received_date=timezone.now(),
                is_initial_stock=False,
                source_transfer=transfer,
                original_stock_entry=original_stock_entry
            )
        
        elif transfer.destination_branch:
            selling_price = item.selling_price
            if selling_price is None and transfer.source_branch:
                source_stock = BranchStock.objects.filter(
                    product=item.product,
                    branch=transfer.source_branch,
                    quantity__gt=0
                ).first()
                if source_stock:
                    selling_price = source_stock.selling_price
            if selling_price is None:
                selling_price = destination_purchase_price
            
            new_batch_number = generate_unique_batch_number()
            transfer_notes = f'Transferred from {transfer.source_warehouse.name if transfer.source_warehouse else transfer.source_branch.name}'
            if original_batch_str:
                transfer_notes += f' (Original batch: {original_batch_str})'
            if item.notes:
                transfer_notes += f': {item.notes}'
            if transfer.notes:
                transfer_notes += f' | Transfer: {transfer.notes}'
            
            BranchStock.objects.create(
                product=item.product,
                branch=transfer.destination_branch,
                quantity=item.quantity,
                purchase_price=destination_purchase_price,
                selling_price=selling_price,
                reorder_level=destination_reorder_level,
                supplier_id=supplier_id,
                batch_number=new_batch_number,
                original_batch_number=original_batch_str,
                notes=transfer_notes,
                created_by=completed_by,
                received_date=timezone.now(),
                is_initial_stock=False,
                source_transfer=transfer,
                original_stock_entry=original_stock_entry,
                original_branch_stock=original_branch_stock
            )
        transfer.status = 'completed'
        transfer.completed_by = completed_by
        transfer.completed_at = timezone.now()
        transfer.save()
    
    return transfer


def cancel_stock_transfer(transfer_id: int, cancelled_by=None) -> StockTransfer:
    """
    Cancel a pending stock transfer.
    """
    transfer = StockTransfer.objects.get(pk=transfer_id)
    
    if transfer.status == 'completed':
        raise ValueError('Cannot cancel a completed transfer.')
    if transfer.status == 'cancelled':
        raise ValueError('Transfer is already cancelled.')
    
    transfer.status = 'cancelled'
    transfer.save()
    
    return transfer


def bulk_create_stock_transfers(transfers_data: List[Dict], created_by=None) -> Tuple[List[StockTransfer], List[Dict]]:
    """
    Bulk create stock transfers.
    
    Args:
        transfers_data: List of dictionaries containing transfer information
            Each dict should have: transfer_type, product_id, quantity, purchase_price,
            source_warehouse_id or source_branch_id (based on transfer_type),
            destination_warehouse_id or destination_branch_id (based on transfer_type),
            supplier_id (optional), batch_number (optional), reference_number (optional),
            notes (optional)
        created_by: User creating the transfers
    
    Returns:
        Tuple of (created_transfers, errors)
        - created_transfers: List of successfully created StockTransfer objects
        - errors: List of error dictionaries with 'index' and 'error' keys
    """
    created_transfers = []
    errors = []
    
    with transaction.atomic():
        for index, transfer_data in enumerate(transfers_data):
            try:
                transfer = create_stock_transfer(
                    transfer_type=transfer_data.get('transfer_type'),
                    product_id=transfer_data.get('product_id'),
                    quantity=transfer_data.get('quantity'),
                    reorder_level=transfer_data.get('reorder_level'),
                    source_warehouse_id=transfer_data.get('source_warehouse_id'),
                    source_branch_id=transfer_data.get('source_branch_id'),
                    destination_warehouse_id=transfer_data.get('destination_warehouse_id'),
                    destination_branch_id=transfer_data.get('destination_branch_id'),
                    supplier_id=transfer_data.get('supplier_id'),
                    batch_number=transfer_data.get('batch_number'),
                    reference_number=transfer_data.get('reference_number'),
                    notes=transfer_data.get('notes', ''),
                    selling_price=transfer_data.get('selling_price'),
                    created_by=created_by
                )
                created_transfers.append(transfer)
                
            except Exception as e:
                # Convert transfer_data to serializable format
                serializable_data = _make_serializable(transfer_data)
                
                errors.append({
                    'index': index,
                    'data': serializable_data,
                    'error': str(e)
                })
                # Continue with next transfer even if one fails
    
    return created_transfers, errors

