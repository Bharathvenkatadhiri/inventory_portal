# your_app/templatetags/custom_filters.py

from django import template

register = template.Library()

@register.filter
def multiply(value, arg):
    try:
        return value * arg
    except (TypeError, ValueError):
        return None

CURRENCY_SYMBOLS = {'INR': '₹', 'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥', 'CNY': '¥'}


@register.filter
def money_totals(totals):
    """Renders services.totals_by_currency() output, e.g. "₹3,10,000 + $1,200"."""
    if not totals:
        return f"{CURRENCY_SYMBOLS['INR']}0"
    parts = []
    for item in totals:
        symbol = CURRENCY_SYMBOLS.get(item['currency'])
        amount = f"{item['amount']:,.0f}"
        parts.append(f"{symbol}{amount}" if symbol else f"{item['currency']} {amount}")
    return " + ".join(parts)


@register.filter
def is_image(file_url):
    return file_url.lower().endswith(('.jpg', '.jpeg', '.png'))

@register.filter
def is_pdf(file_url):
    return file_url.lower().endswith('.pdf')
