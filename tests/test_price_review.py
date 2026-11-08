from decimal import Decimal
import pytest
from tests.test_stock_intake import db,apply
from automation_control.listing_price_approval import approve,current_review,fingerprint,blockers

def test_review_rounding_invalidation_and_bulk_gate(db):
    listing=apply(db,qty=2);db.commit()
    assert blockers(db,listing)
    approve(db,listing,'5.00','single','owner',fingerprint(db,listing),True);db.commit()
    assert listing.price==Decimal('5.99') and not blockers(db,listing)
    assert current_review(db,listing)
    listing.finish='holo';db.commit()
    assert current_review(db,listing) is None and blockers(db,listing)
    approve(db,listing,'4.99','bulk','owner',fingerprint(db,listing));db.commit()
    assert 'bulk' in blockers(db,listing)[0]

@pytest.mark.parametrize('price',['NaN','Infinity','0','-1','4.999','1000000'])
def test_invalid_price_and_stale_form_preserve_price(db,price):
    listing=apply(db);db.commit()
    with pytest.raises(ValueError):approve(db,listing,price,'single','owner',fingerprint(db,listing))
    assert listing.price==0
    with pytest.raises(ValueError):approve(db,listing,'8.99','single','owner','stale')
