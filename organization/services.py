from django.core.exceptions import ValidationError
from typing import Optional
from .models import Branch, Warehouse


def create_branch(name: str, address: str = '', warehouse_id: int = None) -> Branch:
    """
    Create a new branch.
    Returns the created branch instance.
    """
    warehouse = None
    if warehouse_id is not None:
        warehouse = Warehouse.objects.get(pk=warehouse_id)
    
    branch = Branch.objects.create(name=name, address=address, warehouse=warehouse)
    return branch


def edit_branch(pk: int, name: str = None, address: str = None, warehouse_id: Optional[int] = None) -> Branch:
    """
    Update an existing branch.
    Returns the updated branch instance.
    Note: To remove warehouse, pass warehouse_id as a special sentinel value or handle separately.
    """
    branch = Branch.objects.get(pk=pk)
    if warehouse_id is not None:
        warehouse = Warehouse.objects.get(pk=warehouse_id)
        branch.warehouse = warehouse
    # Note: If warehouse_id is None, we don't change the warehouse field
    # To explicitly remove warehouse, you would need to pass a special value or handle it in the view
    if name is not None:
        branch.name = name
    if address is not None:
        branch.address = address
    branch.save()
    return branch


def delete_branch(pk: int) -> None:
    """
    Delete a branch by primary key.
    """
    branch = Branch.objects.get(pk=pk)
    branch.delete()


def create_warehouse(name: str, location: str = '', is_main: bool = False) -> Warehouse:
    """
    Create a new warehouse.
    If is_main=True, raises an error if another warehouse is already marked as main.
    Returns the created warehouse instance.
    """
    # If setting as main, check if another warehouse is already marked as main
    if is_main:
        existing_main = Warehouse.objects.filter(is_main=True).first()
        if existing_main:
            raise ValidationError(
                f'Warehouse "{existing_main.name}" is already marked as main. '
                'Only one warehouse can be marked as main. Please unmark the other warehouse first.'
            )
    
    warehouse = Warehouse.objects.create(name=name, location=location, is_main=is_main)
    return warehouse


def edit_warehouse(pk: int, name: str = None, location: str = None, is_main: bool = None) -> Warehouse:
    """
    Update an existing warehouse.
    If is_main=True, ensures no other warehouse is marked as main.
    Returns the updated warehouse instance.
    """
    warehouse = Warehouse.objects.get(pk=pk)
    
    # If setting as main, unmark other main warehouses
    if is_main is True:
        Warehouse.objects.filter(is_main=True).exclude(pk=pk).update(is_main=False)
        warehouse.is_main = True
    elif is_main is False:
        warehouse.is_main = False
    
    if name is not None:
        warehouse.name = name
    if location is not None:
        warehouse.location = location
    
    warehouse.save()
    return warehouse


def delete_warehouse(pk: int) -> None:
    """
    Delete a warehouse by primary key.
    """
    warehouse = Warehouse.objects.get(pk=pk)
    warehouse.delete()
