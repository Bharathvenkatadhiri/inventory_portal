from django import template

from homepage.feedback import public_summary

register = template.Library()


@register.inclusion_tag('_portal_feedback_section.html')
def portal_feedback_section(heading="What our users say", embedded=False):
    """The public rating and best reviews block for pages seen before login.
    Renders nothing until at least one user has rated the portal. Pass
    embedded=True inside base.html's content block, which already has the
    page container and side padding."""
    return {'summary': public_summary(), 'heading': heading, 'embedded': embedded}
