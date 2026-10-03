from django import template

from accounts import team
from plans import access

register = template.Library()


@register.filter
def teammate(user, other):
    """{% if request.user|teammate:demand.user_id %}: `other` (a user or id)
    is on the same company account as `user`, or is `user`."""
    return team.is_teammate(user, other)


@register.filter
def can(user, action):
    """{% if request.user|can:'quotes.manage' %}: the user's role allows
    `action` (accounts.team.ROLE_PERMISSIONS)."""
    return team.allows(user, action)


@register.filter
def plan_allows(user, action):
    """{% if request.user|plan_allows:'export' %}: role, plan and limits all
    allow `action` (plans.access)."""
    return access.can(user, action)
