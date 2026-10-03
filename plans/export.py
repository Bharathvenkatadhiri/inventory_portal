"""Excel/CSV export (plans that include 'export'). CSV opens in Excel; a
UTF-8 byte-order mark makes Excel read ₹ and non-ASCII names correctly."""
import csv

from django.http import StreamingHttpResponse
from django.utils import timezone


class _Echo:
    def write(self, value):
        return value


def _safe(value):
    """Neutralises spreadsheet formulas in user-entered text (CSV injection)."""
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


def csv_response(name, header, rows):
    writer = csv.writer(_Echo())

    def lines():
        yield '﻿'
        yield writer.writerow([_safe(h) for h in header])
        for row in rows:
            yield writer.writerow([_safe(cell) for cell in row])

    filename = f"makesetu-{name}-{timezone.localdate().isoformat()}.csv"
    response = StreamingHttpResponse(lines(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
