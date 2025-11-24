from .models import Branch, Warehouse


def create_branch(warehouse_id: int, name: str, address: str = '') -> Branch:
    """
    Create a new branch.
    Returns the created branch instance.
    """
    warehouse = Warehouse.objects.get(pk=warehouse_id)

    branch = Branch.objects.create(warehouse=warehouse, name=name, address=address)
    return branch


def edit_branch(pk: int, name: str = None, address: str = None, warehouse_id: int = None) -> Branch:
    """
    Update an existing branch.
    Returns the updated branch instance.
    """
    branch = Branch.objects.get(pk=pk)
    if warehouse_id is not None:
        warehouse = Warehouse.objects.get(pk=warehouse_id)
        branch.warehouse = warehouse
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
    If is_main=True, ensures no other warehouse is marked as main.
    Returns the created warehouse instance.
    """
    # If setting as main, unmark other main warehouses
    if is_main:
        Warehouse.objects.filter(is_main=True).update(is_main=False)
    
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
