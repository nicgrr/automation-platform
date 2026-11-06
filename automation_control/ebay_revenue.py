"""Read-only gross revenue from imported eBay orders, once per order."""
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

SYDNEY = ZoneInfo('Australia/Sydney')
PAID_STATUSES = {'PAID', 'PARTIALLY_REFUNDED', 'FULLY_REFUNDED'}


def order_revenue(rows, now=None):
    now = (now or datetime.now(UTC)).astimezone(SYDNEY)
    latest = {}
    for row in rows:
        timestamp = row.observed_at.replace(tzinfo=UTC) if row.observed_at.tzinfo is None else row.observed_at
        if row.ebay_order_id not in latest or timestamp > latest[row.ebay_order_id][0]:
            latest[row.ebay_order_id] = (timestamp, row.raw_snapshot or {})
    result = dict(today=Decimal('0'), month=Decimal('0'), total=Decimal('0'),
                  month_orders=0, orders=0, skipped=0, first_date=None, has_imports=bool(rows))
    for _, data in latest.values():
        if data.get('orderPaymentStatus') not in PAID_STATUSES:
            continue
        if (data.get('cancelStatus') or {}).get('cancelState') in {'CANCELED', 'CANCELLED'}:
            continue
        total = (data.get('pricingSummary') or {}).get('total') or {}
        try:
            if total.get('currency') != 'AUD':
                raise ValueError('not AUD')
            amount = Decimal(total['value'])
            if not amount.is_finite() or amount < 0:
                raise ValueError('invalid total')
            sold = datetime.fromisoformat(data['creationDate'].replace('Z', '+00:00'))
            if sold.tzinfo is None:
                raise ValueError('missing timezone')
            sold = sold.astimezone(SYDNEY)
        except (KeyError, ValueError, TypeError, InvalidOperation):
            result['skipped'] += 1
            continue
        if sold > now:
            result['skipped'] += 1
            continue
        result['total'] += amount
        result['orders'] += 1
        if result['first_date'] is None or sold.date() < result['first_date']:
            result['first_date'] = sold.date()
        if sold.date() == now.date():
            result['today'] += amount
        if (sold.year, sold.month) == (now.year, now.month):
            result['month'] += amount
            result['month_orders'] += 1
    return result
