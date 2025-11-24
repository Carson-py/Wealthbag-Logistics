from django.db.models import Sum, Count, Avg, F, Q, DecimalField
from django.db.models.functions import TruncHour, TruncDay, TruncMonth
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal
from django.db import transaction
from typing import List, Dict, Tuple, Any, Optional
import os
from .models import Product, Category, Barcode
from organization.models import Warehouse, Branch
from stock.services import add_stock_to_warehouse


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


def create_product(sku: str, name: str, category: int = None, unit: int = None) -> Product:
    """
    Create a new product and automatically generate a barcode linked to the product SKU.
    """
    product = Product.objects.create(
        sku=sku,
        name=name,
        category=category,
        unit=unit
    )
    
    # Auto-generate barcode for the product using SKU as barcode value
    generate_product_barcode(product)
    
    return product


def generate_product_barcode(product: Product) -> 'Barcode':
    """
    Generate and create a barcode for a product using the product SKU.
    Uses CODE128 format (auto-generated).
    The barcode value will be the product SKU, making it easy to lookup products.
    
    Args:
        product: Product instance
        
    Returns:
        Created Barcode instance
    """
    from .models import Barcode
    from .utils import generate_barcode_image
    
    # Use product SKU as barcode value
    barcode_value = product.sku
    
    # Check if barcode already exists for this product
    existing_barcode = Barcode.objects.filter(product=product, barcode=barcode_value).first()
    if existing_barcode:
        return existing_barcode
    
    # Check if barcode value already exists for another product
    if Barcode.objects.filter(barcode=barcode_value).exclude(product=product).exists():
        # If SKU is already used, use product ID instead
        barcode_value = str(product.id)
    
    # Generate barcode image (always CODE128)
    barcode_image = generate_barcode_image(barcode_value)
    
    if not barcode_image:
        # If image generation fails, create barcode without image (frontend can add later)
        barcode = Barcode.objects.create(
            product=product,
            barcode=barcode_value,
            is_primary=True,
            notes=f'Auto-generated barcode for product {product.name}'
        )
        return barcode
    
    # Create barcode with generated image
    barcode = Barcode.objects.create(
        product=product,
        barcode_image=barcode_image,
        barcode=barcode_value,
        is_primary=True,
        notes=f'Auto-generated barcode for product {product.name}'
    )
    
    return barcode


def add_product_to_main_warehouse(product: Product, warehouse_id: int, quantity: Decimal, 
                                  purchase_price: Decimal = None, supplier_id: int = None,
                                  batch_number: str = None, created_by=None):
    """
    Add product to main warehouse (initial stock).
    Now uses StockEntry system to track purchase prices.
    """
    # Create stock entry using the stock management system
    stock_entry = add_stock_to_warehouse(
        product_id=product.id,
        warehouse_id=warehouse_id,
        quantity=quantity,
        purchase_price=purchase_price,
        supplier_id=supplier_id,
        batch_number=batch_number,
        notes=f'Initial stock for product {product.name}',
        created_by=created_by
    )
    
    # Return the StockEntry (aggregated view)
    return stock_entry


def get_main_warehouse() -> Warehouse:
    """
    Get the main warehouse (is_main=True).
    Raises Warehouse.DoesNotExist if no main warehouse is set.
    """
    try:
        return Warehouse.objects.get(is_main=True)
    except Warehouse.DoesNotExist:
        raise Warehouse.DoesNotExist(
            'No main warehouse found. Please mark a warehouse as main (is_main=True) before creating products.'
        )
    except Warehouse.MultipleObjectsReturned:
        # If multiple main warehouses exist, get the first one
        return Warehouse.objects.filter(is_main=True).first()


def bulk_create_products(products_data: List[Dict], created_by=None) -> Tuple[List[Product], List[Dict]]:
    """
    Bulk create products with their initial stock entries.
    
    Args:
        products_data: List of dictionaries containing product and stock information
            Each dict should have: sku, name, category, unit, initial_warehouse_id (optional),
            initial_quantity, initial_purchase_price (optional), batch_number (optional),
            supplier_id (optional). If initial_warehouse_id is not provided, uses main warehouse.
        created_by: User creating the products
    
    Returns:
        Tuple of (created_products, errors)
        - created_products: List of successfully created Product objects
        - errors: List of error dictionaries with 'index' and 'error' keys
    """
    created_products = []
    errors = []
    
    with transaction.atomic():
        for index, product_data in enumerate(products_data):
            try:
                # Extract product fields
                sku = product_data.get('sku')
                name = product_data.get('name')
                category = product_data.get('category')
                unit = product_data.get('unit')
                
                # Extract stock fields
                initial_warehouse_id = product_data.get('initial_warehouse_id')
                initial_quantity = product_data.get('initial_quantity')
                initial_purchase_price = product_data.get('initial_purchase_price')
                supplier_id = product_data.get('supplier_id')
                batch_number = product_data.get('batch_number')
                
                # If no warehouse specified, use main warehouse
                if not initial_warehouse_id:
                    main_warehouse = get_main_warehouse()
                    initial_warehouse_id = main_warehouse.id
                
                # Create product
                product = create_product(
                    sku=sku,
                    name=name,
                    category=category,
                    unit=unit
                )
                
                # Add initial stock
                add_product_to_main_warehouse(
                    product=product,
                    warehouse_id=initial_warehouse_id,
                    quantity=initial_quantity,
                    purchase_price=initial_purchase_price,
                    supplier_id=supplier_id,
                    batch_number=batch_number,
                    created_by=created_by
                )
                
                created_products.append(product)
                
            except Exception as e:
                # Convert product_data to serializable format (convert objects to IDs)
                serializable_data = _make_serializable(product_data)
                
                errors.append({
                    'index': index,
                    'data': serializable_data,
                    'error': str(e)
                })
                # Continue with next product even if one fails
    
    return created_products, errors


def get_product_by_barcode(barcode_value: str) -> Optional[Product]:
    """
    Find a product by barcode value.
    Returns the product if found, None otherwise.
    
    Args:
        barcode_value: The barcode string to search for
        
    Returns:
        Product instance or None if not found
    """
    try:
        barcode = Barcode.objects.select_related('product').get(barcode=barcode_value)
        return barcode.product if barcode.product.is_active else None
    except Barcode.DoesNotExist:
        return None


def get_product_with_stock_by_barcode(barcode_value: str, branch_id: int = None, warehouse_id: int = None) -> Optional[Dict]:
    """
    Find a product by barcode and include available stock information.
    Links Product, Barcode, and Stock for frontend consumption.
    
    Args:
        barcode_value: The barcode string to search for
        branch_id: Optional branch ID to check stock availability at that branch
        warehouse_id: Optional warehouse ID to check stock availability at that warehouse
        
    Returns:
        Dictionary with product, barcode info, and stock info, or None if not found
        {
            'product': Product instance,
            'barcode': Barcode instance,
            'available_stock': Decimal (if branch_id or warehouse_id provided),
            'selling_price': Decimal (if branch_id provided and stock exists),
            'purchase_price': Decimal (if branch_id or warehouse_id provided and stock exists)
        }
    """
    try:
        barcode = Barcode.objects.select_related('product').get(barcode=barcode_value)
        product = barcode.product
        
        if not product.is_active:
            return None
        
        result = {
            'product': product,
            'barcode': barcode,
        }
        
        # If branch_id provided, get branch stock information
        if branch_id:
            from stock.models import BranchStock
            from django.db.models import Sum
            
            branch_stock = BranchStock.objects.filter(
                product=product,
                branch_id=branch_id,
                quantity__gt=0
            ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
            
            result['available_stock'] = branch_stock
            
            # Get selling price from most recent stock entry
            latest_stock = BranchStock.objects.filter(
                product=product,
                branch_id=branch_id,
                quantity__gt=0
            ).order_by('-received_date', '-created_at').first()
            
            if latest_stock:
                result['selling_price'] = latest_stock.selling_price
                result['purchase_price'] = latest_stock.purchase_price
            else:
                result['selling_price'] = None
                result['purchase_price'] = None
        
        # If warehouse_id provided, get warehouse stock information
        elif warehouse_id:
            from stock.models import StockEntry
            from django.db.models import Sum
            
            warehouse_stock = StockEntry.objects.filter(
                product=product,
                warehouse_id=warehouse_id,
                quantity__gt=0
            ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
            
            result['available_stock'] = warehouse_stock
            
            # Get purchase price from most recent stock entry
            latest_stock = StockEntry.objects.filter(
                product=product,
                warehouse_id=warehouse_id,
                quantity__gt=0
            ).order_by('-received_date', '-created_at').first()
            
            if latest_stock:
                result['purchase_price'] = latest_stock.purchase_price
            else:
                result['purchase_price'] = None
        
        return result
        
    except Barcode.DoesNotExist:
        return None


def create_barcode(product_id: int, barcode_value: str, barcode_image, 
                   is_primary: bool = False, notes: str = '') -> Barcode:
    """
    Create a Barcode instance with image and scanned value (provided by frontend).
    Uses CODE128 format (same as auto-generated barcodes).
    
    Args:
        product_id: ID of the product to link the barcode to
        barcode_value: The scanned barcode value (provided by frontend scanner)
        barcode_image: Django UploadedFile or file-like object containing barcode image
        is_primary: Whether this is the primary barcode for the product
        notes: Additional notes about the barcode
        
    Returns:
        Created Barcode instance
        
    Raises:
        ValueError: If barcode value already exists
    """
    product = Product.objects.get(pk=product_id)
    
    # Check if barcode already exists
    if Barcode.objects.filter(barcode=barcode_value).exists():
        raise ValueError(f"Barcode {barcode_value} already exists for another product.")
    
    # Create barcode instance (all barcodes use CODE128 format)
    barcode = Barcode.objects.create(
        product=product,
        barcode_image=barcode_image,
        barcode=barcode_value,
        is_primary=is_primary,
        notes=notes
    )
    
    return barcode