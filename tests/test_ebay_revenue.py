from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from automation_control.ebay_revenue import order_revenue

NOW = datetime(2026, 11, 1, 1, tzinfo=UTC)

def row(id, date='2026-10-31T14:00:00Z', value='14.99', status='PAID', currency='AUD', cancel='NONE_REQUESTED', observed=NOW):
    return SimpleNamespace(ebay_order_id=id, observed_at=observed, raw_snapshot={
        'creationDate':date,'orderPaymentStatus':status,'cancelStatus':{'cancelState':cancel},
        'pricingSummary':{'total':{'value':value,'currency':currency}}})

def test_multi_line_order_counts_once_and_uses_sydney_month_boundary():
    summary=order_revenue([row('a'),row('a'),row('b',date='2026-10-31T12:00:00Z')],NOW)
    assert summary['today']==summary['month']==Decimal('14.99')
    assert summary['total']==Decimal('29.98')
    assert summary['month_orders']==1

def test_newest_cancellation_excludes_previously_paid_order():
    old=row('a',observed=datetime(2026,10,31,tzinfo=UTC))
    latest=row('a',cancel='CANCELED')
    assert order_revenue([latest,old],NOW)['orders']==0

def test_unpaid_foreign_invalid_totals_excluded_but_refunded_gross_is_explicit():
    summary=order_revenue([row('a',status='PENDING'),row('b',currency='USD'),
                          row('c',value='NaN'),row('d',status='FULLY_REFUNDED')],NOW)
    assert summary['total']==Decimal('14.99')
    assert summary['orders']==1 and summary['skipped']==2
