from django.db import transaction
from django.utils import timezone
from decimal import Decimal
from typing import List, Dict
from django.core.exceptions import ValidationError

from .models import Layby, LaybyItem, LaybyPayment
from products.models import Product
from organization.models import Branch
from stock import services as stock_services
from sales import services as sales_services
from sales.models import Sale

def create_layby(
    customer_name: str,
    customer_phone: str,
    branch_id: int,
    due_date: str,
    items_data: List[Dict],
    deposit: Decimal,
    payment_method: str,
    cashier,
    notes: str = ""
) -> Layby:
    """
    Create a new layby agreement, reserve stock, and take initial deposit.
    """
    branch = Branch.objects.get(pk=branch_id)
    
    with transaction.atomic():
        # 1. Create Layby record
        layby = Layby.objects.create(
            customer_name=customer_name,
            customer_phone=customer_phone,
            branch=branch,
            cashier=cashier,
            due_date=due_date,
            notes=notes,
            status='pending'
        )
        
        total_amount = Decimal('0')
        
        # 2. Add items and reserve stock
        for item in items_data:
            product_id = item['product_id']
            quantity = Decimal(str(item['quantity']))
            product = Product.objects.get(pk=product_id)
            
            # Use the product's current selling price at the branch if possible
            # or a provided price? For now let's assume we fetch current price.
            # In a real POS, this would come from the frontend.
            # Let's check stock for price.
            stock_entry = branch.stock_entries.filter(product=product).first()
            if not stock_entry:
                 raise ValidationError(f"Product {product.name} has no stock at this branch.")
            
            unit_price = stock_entry.selling_price
            
            LaybyItem.objects.create(
                layby=layby,
                product=product,
                quantity=quantity,
                unit_price=unit_price
            )
            
            total_amount += unit_price * quantity
            
            # Reserve stock
            stock_services.reserve_stock_at_branch(product_id, branch_id, quantity)
            
        layby.total_amount = total_amount
        layby.deposit = deposit # This is the initial deposit
        layby.balance = total_amount - deposit
        layby.save()
        
        # 3. Record initial payment
        if deposit > 0:
            LaybyPayment.objects.create(
                layby=layby,
                amount=deposit,
                payment_method=payment_method,
                cashier=cashier,
                notes="Initial deposit"
            )
        
        # 4. Auto-finalize if fully paid
        if layby.balance <= 0:
            finalize_layby(layby, cashier)
            
        return layby

def add_layby_payment(
    layby: Layby,
    amount: Decimal,
    payment_method: str,
    cashier,
    notes: str = ""
) -> LaybyPayment:
    """
    Add a payment to an existing layby and update balance.
    """
    if layby.status != 'pending':
        raise ValidationError(f"Cannot add payment to a layby with status: {layby.status}")
        
    if amount > layby.balance:
        raise ValidationError(f"Payment amount ({amount}) exceeds remaining balance ({layby.balance})")
        
    with transaction.atomic():
        payment = LaybyPayment.objects.create(
            layby=layby,
            amount=amount,
            payment_method=payment_method,
            cashier=cashier,
            notes=notes
        )
        
        layby.balance -= amount
        layby.save()

        # Auto-finalize if fully paid
        if layby.balance <= 0:
            finalize_layby(layby, cashier)
        
        return payment

def finalize_layby(layby: Layby, cashier) -> Sale:
    """
    Finalize a fully-paid layby by converting it to a sale and reducing stock.
    """
    if layby.status != 'pending':
        raise ValidationError(f"Cannot finalize a layby with status: {layby.status}")
        
    if layby.balance > 0:
        raise ValidationError(f"Cannot finalize layby. Remaining balance: {layby.balance}")
        
    with transaction.atomic():
        # 1. Create Sale
        # We need to construct the sale items data for sales_services.create_sale
        sale_items = []
        for item in layby.items.all():
            sale_items.append({
                'product_id': item.product.id,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'discount': Decimal('0')
            })
            
        # create_sale expects branch_id and other fields
        sale = sales_services.create_sale(
            branch_id=layby.branch.id,
            cashier=layby.cashier, # Use the original cashier or current? Usually current.
            items_data=sale_items,
            type_of_payment='multiple', # Since it was layby, might be multiple payments
            notes=f"Converted from Layby {layby.layby_number}. Original Customer: {layby.customer_name}"
        )
        
        # 2. Complete the sale (this normally deducts stock)
        # However, we need to tell complete_sale (or the stock service it calls) 
        # that this is a layby sale so it reduces reserved_quantity too.
        # But wait, create_sale/complete_sale calls stock_services.remove_stock_from_branch.
        # I updated remove_stock_from_branch to take an is_layby flag.
        # I need to ensure complete_sale passes this flag OR I manually handle stock deduction here.
        
        # Let's manually deduct stock here using the updated service and mark sale as completed.
        for item in layby.items.all():
            stock_services.remove_stock_from_branch(
                product_id=item.product.id,
                branch_id=layby.branch.id,
                quantity=item.quantity,
                reason=f"Layby Sale {layby.layby_number}",
                is_layby=True
            )
            
        sale.status = 'completed'
        sale.save()
        
        # 3. Update Layby status
        layby.status = 'completed'
        layby.save()
        
        return sale

def cancel_layby(layby: Layby, cashier) -> Layby:
    """
    Cancel a layby and release reserved stock.
    """
    if layby.status != 'pending':
        raise ValidationError(f"Cannot cancel a layby with status: {layby.status}")
        
    with transaction.atomic():
        # Release reserved stock for all items
        for item in layby.items.all():
            stock_services.release_stock_at_branch(
                product_id=item.product.id,
                branch_id=layby.branch.id,
                quantity=item.quantity
            )
            
        layby.status = 'cancelled'
        layby.save()
        
        return layby
