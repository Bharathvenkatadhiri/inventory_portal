from django import template

from accounts import team

register = template.Library()


@register.filter
def teammate(user, other):
    """{% if request.user|teammate:demand.user_id %}: `other` (a user or id)
    is on the same company account as `user`, or is `user`."""
    return team.is_teammate(user, other)
