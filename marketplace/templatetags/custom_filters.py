# your_app/templatetags/custom_filters.py

from django import template

register = template.Library()

@register.filter
def multiply(value, arg):
    try:
        return value * arg
    except (TypeError, ValueError):
        return None

@register.filter
def is_image(file_url):
    return file_url.lower().endswith(('.jpg', '.jpeg', '.png'))

@register.filter
def is_pdf(file_url):
    return file_url.lower().endswith('.pdf')
