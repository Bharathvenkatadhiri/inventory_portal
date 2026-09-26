"""Buyer spend and supplier win-rate reports.

Money is reported in INR: each order's total (incl. GST, as on the order
page) is converted with the current ExchangeRate for its RFQ currency.
Spend and revenue count completed orders only, dated by when they were
completed. An order in a currency with no rate is left out of the INR
figures and listed in `unconverted` so the page can say so.
"""
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from statistics import median

from django.db.models import Exists, Max, OuterRef, Prefetch, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import ExchangeRate, Order, OrderEvent, Quote, Requirement, RequirementPart, RFQDecline

PERIODS = [('3m', 'Last 3 months', 3), ('6m', 'Last 6 months', 6), ('12m', 'Last 12 months', 12), ('all', 'All time', None)]
DEFAULT_PERIOD = '12m'


def _month_start(day):
    return date(day.year, day.month, 1)


def _add_months(month, count):
    index = month.year * 12 + month.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def period_window(key):
    """(key, label, first month or None for all time) for a period key."""
    for period_key, label, months in PERIODS:
        if period_key == key:
            break
    else:
        period_key, label, months = next(p for p in PERIODS if p[0] == DEFAULT_PERIOD)
    if months is None:
        return period_key, label, None
    return period_key, label, _add_months(_month_start(timezone.localdate()), -(months - 1))


def _since_datetime(first_month):
    return timezone.make_aware(datetime(first_month.year, first_month.month, 1)) if first_month else None


def _month_series(values_by_month, first_month):
    """Every month from first_month (or the earliest with data) to this
    month, zero-filled, with bar heights as a percent of the busiest month."""
    this_month = _month_start(timezone.localdate())
    start = first_month or (min(values_by_month) if values_by_month else this_month)
    months = []
    month = start
    while month <= this_month:
        months.append(month)
        month = _add_months(month, 1)
    peak = max((values_by_month.get(m, 0) for m in months), default=0)
    return [
        {'month': m, 'value': values_by_month.get(m, 0),
         'percent': float(values_by_month.get(m, 0)) * 100 / float(peak) if peak else 0}
        for m in months
    ]


class InrConverter:
    def __init__(self):
        self.rates = {row.currency: row.inr_per_unit for row in ExchangeRate.objects.all()}
        self.rates['INR'] = Decimal('1')
        self.missing = set()

    def __call__(self, amount, currency):
        currency = currency or 'INR'
        rate = self.rates.get(currency)
        if rate is None:
            self.missing.add(currency)
            return None
        return (Decimal(amount) * rate).quantize(Decimal('1'))


def completed_orders():
    """Completed orders with `completed_at`: when the status log recorded
    the completion (falling back to the last update for older orders)."""
    completed_event = OrderEvent.objects.filter(
        order=OuterRef('pk'), kind=OrderEvent.KIND_STATUS, to_value='completed',
    ).order_by().values('order').annotate(at=Max('changed_at')).values('at')
    return (
        Order.objects.filter(status='completed')
        .annotate(completed_at=Coalesce(Subquery(completed_event), 'updated_at'))
        .select_related('quote', 'requirement', 'supplier')
        .prefetch_related(Prefetch('requirement__requirement_parts', queryset=RequirementPart.objects.order_by('pk')))
    )


def _valued(orders, since, convert):
    """(order, inr value) for completed orders on or after `since`, oldest first."""
    if since:
        orders = orders.filter(completed_at__gte=since)
    rows = []
    for order in orders.order_by('completed_at'):
        total = order.quote.get_breakdown()['total']
        rows.append((order, convert(total, order.requirement.quote_currency), total))
    return rows


def _share_rows(values, total):
    return [
        {**row, 'percent': float(row['value']) * 100 / float(total) if total else 0}
        for row in sorted(values, key=lambda row: row['value'], reverse=True)
    ]


# --- Buyer spend ------------------------------------------------------------

def buyer_spend(user, period=DEFAULT_PERIOD):
    period_key, period_label, first_month = period_window(period)
    since = _since_datetime(first_month)
    convert = InrConverter()
    rows = _valued(completed_orders().filter(customer__user=user), since, convert)
    converted = [(order, inr, total) for order, inr, total in rows if inr is not None]

    total_inr = sum((inr for _, inr, _ in converted), Decimal('0'))
    by_month = defaultdict(Decimal)
    by_supplier = {}
    by_process = defaultdict(Decimal)
    for order, inr, _ in converted:
        by_month[_month_start(timezone.localtime(order.completed_at).date())] += inr
        entry = by_supplier.setdefault(order.supplier_id, {
            'name': order.supplier.companyname or str(order.supplier), 'value': Decimal('0'), 'orders': 0,
        })
        entry['value'] += inr
        entry['orders'] += 1
        # An RFQ can mix processes; split the order's value by part quantity.
        parts = list(order.requirement.requirement_parts.all())
        quantity = sum(part.quantity for part in parts)
        for part in parts:
            if quantity:
                by_process[part.get_technology_display()] += inr * part.quantity / quantity
        if not quantity:
            by_process['Unspecified'] += inr

    return {
        'period': period_key, 'period_label': period_label, 'periods': PERIODS,
        'total_inr': total_inr,
        'order_count': len(converted),
        'average_inr': total_inr / len(converted) if converted else None,
        'supplier_count': len(by_supplier),
        'unconverted': sorted(convert.missing),
        'unconverted_count': len(rows) - len(converted),
        'months': _month_series(by_month, first_month),
        'suppliers': _share_rows(by_supplier.values(), total_inr),
        'processes': _share_rows([{'name': name, 'value': value} for name, value in by_process.items()], total_inr),
        'funnel': rfq_funnel(user, since),
        'csv_rows': [
            [f"ORD-{order.billno}", f"RFQ-{order.requirement_id}", order.requirement.title,
             order.supplier.companyname or str(order.supplier), timezone.localtime(order.completed_at).date().isoformat(),
             order.requirement.quote_currency, f"{total:.2f}", '' if inr is None else f"{inr:.0f}"]
            for order, inr, total in rows
        ],
        'csv_header': ['Order', 'RFQ', 'Title', 'Manufacturer', 'Completed on', 'Currency', 'Order total', 'Total (INR)'],
    }


def rfq_funnel(user, since):
    """How the buyer's RFQs posted in the period progressed."""
    rfqs = Requirement.objects.filter(user=user, is_deleted=False)
    if since:
        rfqs = rfqs.filter(created_at__gte=since)
    live_quotes = Quote.objects.filter(requirement=OuterRef('pk'), is_deleted=False, is_draft=False)
    rfqs = rfqs.annotate(has_quote=Exists(live_quotes), has_award=Exists(live_quotes.filter(is_selected=True)))
    posted = rfqs.count()
    steps = [
        ('Posted', posted),
        ('Received quotes', rfqs.filter(has_quote=True).count()),
        ('Awarded', rfqs.filter(has_award=True).count()),
        ('Completed', rfqs.filter(status='Completed').count()),
    ]
    return {
        'steps': [{'label': label, 'count': count, 'percent': count * 100 / posted if posted else 0} for label, count in steps],
        'expired_without_quotes': rfqs.filter(
            has_quote=False, status__isnull=True, end_date__lt=timezone.now(),
        ).count(),
    }


# --- Supplier win rate --------------------------------------------------------

PRICE_BUCKETS = [
    ('below', 'Priced below the winner'),
    ('within_5', 'Up to 5% above'),
    ('within_15', '5–15% above'),
    ('over_15', 'More than 15% above'),
]


def _price_bucket(gap):
    if gap < 0:
        return 'below'
    if gap <= 5:
        return 'within_5'
    if gap <= 15:
        return 'within_15'
    return 'over_15'


def _rate(won, lost):
    decided = won + lost
    return won * 100 / decided if decided else None


def supplier_win_rate(supplier, period=DEFAULT_PERIOD):
    period_key, period_label, first_month = period_window(period)
    since = _since_datetime(first_month)

    winner = Quote.objects.filter(requirement=OuterRef('requirement'), is_selected=True, is_deleted=False)
    quotes = (
        Quote.objects.filter(supplier=supplier, is_deleted=False, is_draft=False)
        .select_related('requirement')
        .prefetch_related('requirement__requirement_parts')
        .annotate(winning_price=Subquery(winner.values('quote_price')[:1]), winning_supplier=Subquery(winner.values('supplier')[:1]))
        .order_by('created_at')
    )
    if since:
        quotes = quotes.filter(created_at__gte=since)

    won = lost = pending = 0
    by_month = defaultdict(lambda: {'submitted': 0, 'won': 0, 'lost': 0})
    by_process = defaultdict(lambda: {'won': 0, 'lost': 0, 'pending': 0})
    gaps = []
    csv_rows = []
    for quote in quotes:
        if quote.is_selected:
            outcome = 'won'
        elif quote.status == 'Rejected' or (quote.winning_supplier is not None and quote.winning_supplier != supplier.pk):
            outcome = 'lost'
        else:
            outcome = 'pending'
        won += outcome == 'won'
        lost += outcome == 'lost'
        pending += outcome == 'pending'
        month = by_month[_month_start(timezone.localtime(quote.created_at).date())]
        month['submitted'] += 1
        if outcome != 'pending':
            month[outcome] += 1
        for process in {part.get_technology_display() for part in quote.requirement.requirement_parts.all()} or {'Unspecified'}:
            by_process[process][outcome] += 1
        gap = None
        if outcome == 'lost' and quote.winning_price:
            # Only the gap is shown — never the competitor or their price.
            gap = float((quote.quote_price - quote.winning_price) * 100 / quote.winning_price)
            gaps.append(gap)
        csv_rows.append([
            f"RFQ-{quote.requirement_id}", quote.requirement.title, timezone.localtime(quote.created_at).date().isoformat(),
            quote.requirement.quote_currency, f"{quote.quote_price:.2f}", outcome.title(),
            '' if gap is None else f"{gap:+.1f}%",
        ])

    declines = RFQDecline.objects.filter(supplier=supplier)
    if since:
        declines = declines.filter(created_at__gte=since)

    months = _month_series({m: v['submitted'] for m, v in by_month.items()}, first_month)
    for row in months:
        stats = by_month.get(row['month'], {'won': 0, 'lost': 0})
        row['won'] = stats['won']
        row['win_rate'] = _rate(stats['won'], stats['lost'])

    bucket_counts = defaultdict(int)
    for gap in gaps:
        bucket_counts[_price_bucket(gap)] += 1

    convert = InrConverter()
    revenue_rows = _valued(completed_orders().filter(supplier=supplier), since, convert)
    revenue_by_month = defaultdict(Decimal)
    for order, inr, _ in revenue_rows:
        if inr is not None:
            revenue_by_month[_month_start(timezone.localtime(order.completed_at).date())] += inr

    return {
        'period': period_key, 'period_label': period_label, 'periods': PERIODS,
        'submitted': won + lost + pending, 'won': won, 'lost': lost, 'pending': pending,
        'win_rate': _rate(won, lost),
        'declined': declines.count(),
        'months': months,
        'processes': sorted(
            ({'name': name, **counts, 'win_rate': _rate(counts['won'], counts['lost'])} for name, counts in by_process.items()),
            key=lambda row: (-(row['won'] + row['lost'] + row['pending']), row['name']),
        ),
        'price_gaps': {
            'count': len(gaps),
            'median': median(gaps) if gaps else None,
            'buckets': [
                {'key': key, 'label': label, 'count': bucket_counts[key],
                 'percent': bucket_counts[key] * 100 / len(gaps) if gaps else 0}
                for key, label in PRICE_BUCKETS
            ],
        },
        'revenue_inr': sum((inr for _, inr, _ in revenue_rows if inr is not None), Decimal('0')),
        'revenue_orders': sum(1 for _, inr, _ in revenue_rows if inr is not None),
        'revenue_months': _month_series(revenue_by_month, first_month),
        'unconverted': sorted(convert.missing),
        'unconverted_count': sum(1 for _, inr, _ in revenue_rows if inr is None),
        'csv_rows': csv_rows,
        'csv_header': ['RFQ', 'Title', 'Quoted on', 'Currency', 'Unit price', 'Outcome', 'Gap to winning price'],
    }
