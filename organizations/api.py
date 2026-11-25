"""Public /api/v1 endpoints for the pages before sign-in (and, later, the
mobile app's sign-up screens)."""
from typing import List, Optional

from ninja import Router, Schema

from plans import erp

from .catalog import DEFAULT_ROLES, PERMISSIONS

router = Router(tags=["public"])


class PlanOut(Schema):
    key: str
    label: str
    monthly_price_inr: int
    users: Optional[int]
    light_users: Optional[int]
    teams: Optional[int]
    extra_light_user_monthly_inr: int
    warehouses: Optional[int]
    support: str
    features: List[str]


class RoleOut(Schema):
    key: str
    name: str
    description: str
    user_type: str
    permissions: List[str]


@router.get("/plans", response=List[PlanOut], auth=None)
def plans(request):
    """The ERP plans (plans/erp.py). Prices are per month, excluding GST;
    users or warehouses null means no limit."""
    return [
        {'key': c['key'], 'label': c['label'], 'monthly_price_inr': c['monthly'], 'users': c['users'],
         'light_users': c['light_users'], 'teams': c['teams'], 'extra_light_user_monthly_inr': c['extra_light_user_monthly'],
         'warehouses': c['warehouses'], 'support': c['support'], 'features': c['features']}
        for c in erp.cards()
    ]


@router.get("/roles/defaults", response=List[RoleOut], auth=None)
def default_roles(request):
    """The roles every new organization starts with, and what each allows."""
    return [
        {'key': key, 'name': name, 'description': description, 'user_type': user_type, 'permissions': codes}
        for key, (name, description, codes, user_type) in DEFAULT_ROLES.items()
    ]


@router.get("/permissions", response=List[dict], auth=None)
def permissions(request):
    return [{'code': code, 'label': label, 'module': module} for code, (label, module, _f, _s) in PERMISSIONS.items()]
