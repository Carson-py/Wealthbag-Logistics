from django.test import TestCase
from django.utils import timezone
from decimal import Decimal
from datetime import date, timedelta
from accounts.models import User
from products.models import Product
from organization.models import Branch, Warehouse
from stock.models import BranchStock
from stock import services as stock_services
from layby.models import Layby, LaybyItem, LaybyPayment
from layby import services as layby_services
from sales.models import Sale

class LaybyTestCase(TestCase):
    def setUp(self):
        # Create a user/cashier
        self.user = User.objects.create_user(email='cashier@example.com', password='password123')
        
        # Create a branch
        self.branch = Branch.objects.create(name='Main Branch')
        
        # Create a product
        self.product = Product.objects.create(name='Test Product', sku='TEST001')
        
        # Add stock to branch
        self.stock_entry = BranchStock.objects.create(
            product=self.product,
            branch=self.branch,
            quantity=Decimal('100.00'),
            purchase_price=Decimal('50.00'),
            selling_price=Decimal('100.00'),
            batch_number='BATCH001',
            received_date=timezone.now()
        )

    def test_layby_lifecycle(self):
        # 1. Create a layby
        items_data = [{'product_id': self.product.id, 'quantity': 2}]
        due_date = (date.today() + timedelta(days=30)).isoformat()
        
        layby = layby_services.create_layby(
            customer_name='John Doe',
            customer_phone='1234567890',
            branch_id=self.branch.id,
            due_date=due_date,
            items_data=items_data,
            deposit=Decimal('50.00'),
            payment_method='cash',
            cashier=self.user
        )
        
        # Verify layby creation
        self.assertEqual(layby.customer_name, 'John Doe')
        self.assertEqual(layby.total_amount, Decimal('200.00')) # 2 * 100.00
        self.assertEqual(layby.balance, Decimal('150.00'))
        self.assertEqual(layby.status, 'pending')
        
        # Verify stock reservation
        stock = BranchStock.objects.get(id=self.stock_entry.id)
        self.assertEqual(stock.reserved_quantity, Decimal('2.00'))
        
        # 2. Add a payment
        layby_services.add_layby_payment(
            layby=layby,
            amount=Decimal('50.00'),
            payment_method='card',
            cashier=self.user,
            notes='Partial payment'
        )
        
        layby.refresh_from_db()
        self.assertEqual(layby.balance, Decimal('100.00'))
        self.assertEqual(layby.total_paid, Decimal('100.00'))
        
        # 3. Add final payment
        layby_services.add_layby_payment(
            layby=layby,
            amount=Decimal('100.00'),
            payment_method='bank_transfer',
            cashier=self.user,
            notes='Final payment'
        )
        
        layby.refresh_from_db()
        self.assertEqual(layby.balance, Decimal('0.00'))
        self.assertEqual(layby.status, 'completed')
        
        # Verify a sale was created (it's created automatically now)
        sale = Sale.objects.get(notes__icontains=layby.layby_number)
        
        # Verify Sale details
        self.assertEqual(sale.status, 'completed')
        self.assertEqual(sale.total_amount, Decimal('200.00'))
        
        # Verify stock reduction (physical and reserved)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal('98.00'))
        self.assertEqual(stock.reserved_quantity, Decimal('0.00'))

    def test_auto_finalization_on_creation(self):
        # Test full payment at creation
        items_data = [{'product_id': self.product.id, 'quantity': 1}]
        due_date = (date.today() + timedelta(days=30)).isoformat()
        
        layby = layby_services.create_layby(
            customer_name='Instant Payer',
            customer_phone='111222333',
            branch_id=self.branch.id,
            due_date=due_date,
            items_data=items_data,
            deposit=Decimal('100.00'), # Full price
            payment_method='cash',
            cashier=self.user
        )
        
        layby.refresh_from_db()
        self.assertEqual(layby.status, 'completed')
        self.assertEqual(layby.balance, Decimal('0.00'))
        
        # Verify a sale was created
        self.assertTrue(Sale.objects.filter(notes__icontains=layby.layby_number).exists())

    def test_auto_finalization_on_payment(self):
        # Test full payment via add_payment
        items_data = [{'product_id': self.product.id, 'quantity': 1}]
        due_date = (date.today() + timedelta(days=30)).isoformat()
        
        layby = layby_services.create_layby(
            customer_name='Partial Payer',
            customer_phone='444555666',
            branch_id=self.branch.id,
            due_date=due_date,
            items_data=items_data,
            deposit=Decimal('40.00'),
            payment_method='cash',
            cashier=self.user
        )
        
        self.assertEqual(layby.status, 'pending')
        
        layby_services.add_layby_payment(
            layby=layby,
            amount=Decimal('60.00'), # Pay remaining
            payment_method='cash',
            cashier=self.user
        )
        
        layby.refresh_from_db()
        self.assertEqual(layby.status, 'completed')
        
        # Verify stock reduction
        stock = BranchStock.objects.get(id=self.stock_entry.id)
        # 100 - (2 from previous test runs if database isn't reset, but it's a new DB for each test)
        # So it should be 100 - 1 = 99
        self.assertEqual(stock.quantity, Decimal('99.00'))
        self.assertEqual(stock.reserved_quantity, Decimal('0.00'))

    def test_layby_cancellation(self):
        # 1. Create a layby
        items_data = [{'product_id': self.product.id, 'quantity': 5}]
        due_date = (date.today() + timedelta(days=30)).isoformat()
        
        layby = layby_services.create_layby(
            customer_name='Jane Doe',
            customer_phone='0987654321',
            branch_id=self.branch.id,
            due_date=due_date,
            items_data=items_data,
            deposit=Decimal('10.00'),
            payment_method='cash',
            cashier=self.user
        )
        
        # Verify stock reservation
        stock = BranchStock.objects.get(id=self.stock_entry.id)
        self.assertEqual(stock.reserved_quantity, Decimal('5.00'))
        
        # 2. Cancel layby
        layby_services.cancel_layby(layby, self.user)
        
        # Verify Layby status
        layby.refresh_from_db()
        self.assertEqual(layby.status, 'cancelled')
        
        # Verify stock release
        stock.refresh_from_db()
        self.assertEqual(stock.reserved_quantity, Decimal('0.00'))
        self.assertEqual(stock.quantity, Decimal('100.00'))

    def test_insufficient_stock_reservation(self):
        # 1. Try to reserve more than available
        items_data = [{'product_id': self.product.id, 'quantity': 101}]
        due_date = (date.today() + timedelta(days=30)).isoformat()
        
        with self.assertRaises(ValueError):
            layby_services.create_layby(
                customer_name='Fail Bot',
                customer_phone='0000000',
                branch_id=self.branch.id,
                due_date=due_date,
                items_data=items_data,
                deposit=Decimal('0.00'),
                payment_method='cash',
                cashier=self.user
            )
