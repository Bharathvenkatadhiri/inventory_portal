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


@register.filter
def search_highlight(snippet):
    """A search snippet from marketplace.search: escapes it, then turns the
    match markers into <mark> tags. Empty sections (" · · ") are dropped."""
    from django.utils.html import escape
    from django.utils.safestring import mark_safe
    from marketplace.search import HIGHLIGHT_START, HIGHLIGHT_STOP
    parts = [part.strip() for part in (snippet or '').split('·')]
    text = ' · '.join(part for part in parts if part and part != 'Parts:')
    html = escape(text).replace(HIGHLIGHT_START, '<mark class="rounded bg-accent-100 px-0.5 text-navy-900">').replace(HIGHLIGHT_STOP, '</mark>')
    return mark_safe(html)


def _indian_grouping(number):
    """1234567 -> "12,34,567" (lakh/crore grouping)."""
    digits = str(abs(int(number)))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ','.join(groups) + ',' + tail
    return ('-' if number < 0 else '') + digits


@register.filter
def inr(value):
    """A whole-rupee amount with Indian grouping, e.g. "₹12,34,567"."""
    if value is None or value == '':
        return '—'
    return f"₹{_indian_grouping(round(value))}"


@register.filter
def inr_compact(value):
    """Short INR for chart labels: ₹950, ₹12.3K, ₹4.5L, ₹1.2Cr."""
    if value is None:
        return '—'
    value = float(value)
    for size, suffix in ((1e7, 'Cr'), (1e5, 'L'), (1e3, 'K')):
        if abs(value) >= size:
            return f"₹{value / size:.1f}".rstrip('0').rstrip('.') + suffix
    return f"₹{value:.0f}"
