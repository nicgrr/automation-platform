import pytest

from automation_control.vinted_copy import all_details, generate_copy


@pytest.mark.parametrize(('brand', 'series', 'name', 'expected'), [
    ('Sonny Angel', 'Animal Series 1', 'Duck', 'Sonny Angel Duck Animal Series 1 Figure'),
    ('Sonny Angel', 'Animal Series 1', 'Sonny Angel Duck',
     'Sonny Angel Duck Animal Series 1 Figure'),
    ('SONNY ANGEL', 'Animal Series 1', 'Sonny Angel Duck Animal Series 1',
     'Sonny Angel Duck Animal Series 1 Figure'),
    ('POP MART', 'Twinkle Twinkle Create Your Taste', 'Milk Biscuit',
     'POP MART Milk Biscuit Twinkle Twinkle Create Your Taste Figure'),
    ('POP MART', 'THE MONSTERS Almost Hidden', 'Labubu Mailbox',
     'POP MART Labubu Mailbox THE MONSTERS Almost Hidden Figure'),
])
def test_identity_generates_buyer_copy(brand, series, name, expected):
    copy = generate_copy(brand, series, name)
    assert copy['title'] == expected
    assert f'Figure: {name}' in copy['description']
    assert f'Brand: {brand}' in copy['description']
    assert f'Series / Set: {series}' in copy['description']
    assert 'Condition: Opened / used' in copy['description']
    assert 'no box or collector card included' in copy['description']


def test_new_selection_replaces_old_identity_without_inventing_claims():
    before = generate_copy('Sonny Angel', 'Animal Series 1', 'Duck')
    after = generate_copy('POP MART', 'Twinkle Twinkle Create Your Taste', 'Milk Biscuit')
    assert 'Duck' in before['title']
    text = after['title'] + after['description']
    assert 'Duck' not in text and 'Sonny Angel' not in text and 'Animal Series 1' not in text
    assert 'Milk Biscuit' in after['title'] and 'Milk Biscuit' in after['description']
    assert not any(claim in text.casefold() for claim in ('authentic', 'secret', 'rare', 'mint', 'excellent'))


def test_deduplication_matches_complete_components_only():
    assert generate_copy('Duck', 'Duckling', 'Duckling')['title'] == 'Duck Duckling Figure'
    assert generate_copy('POP MART', 'Series A', 'Milk Biscuit Figure')['title'] == 'POP MART Milk Biscuit Figure Series A'


def test_unknown_fields_do_not_leak_review_flags_into_buyer_copy():
    copy = generate_copy('Unknown', 'NEEDS_REVIEW', 'Unidentified figure',
                         condition='', included='TBC', notes='Unconfirmed')
    assert copy['title'] == 'Collectible Figure'
    assert copy['description'] == "Please review the photos for the item's condition."


def test_explicit_condition_contents_notes_and_unicode_are_preserved():
    copy = generate_copy('Sonny Angel', '夏祭り — Summer Festival', '金魚 / Goldfish 🐟',
                         condition='Opened; small scratch on base',
                         included='Figure, opened box and matching collector card.',
                         notes='Light mark beside the left eye; see photo 3.')
    assert '金魚 / Goldfish 🐟' in copy['title']
    assert '夏祭り — Summer Festival' in copy['description']
    assert 'Condition: Opened; small scratch on base' in copy['description']
    assert 'Includes: Figure, opened box and matching collector card.' in copy['description']
    assert 'Condition notes: Light mark beside the left eye; see photo 3.' in copy['description']
    assert 'no box' not in copy['description']
    assert copy['description'].encode('utf-8').decode('utf-8') == copy['description']


def test_long_title_is_bounded_without_shortening_description_identity():
    name = 'Goldfish 日本語 ' * 40
    copy = generate_copy('Sonny Angel', 'Summer Festival', name)
    assert len(copy['title']) <= 256
    assert copy['title'].endswith('…')
    assert name.strip() in copy['description']


def test_copy_all_uses_confirmed_price_and_never_inventory_quantity():
    draft = {**generate_copy('Sonny Angel', 'Animal Series 1', 'Duck'),
             'confirmed_price': '19.99', 'suggested_price': '35.99', 'quantity': 12,
             'currency': 'AUD', 'hold_reason': 'Internal review reminder'}
    text = all_details(draft)
    assert text.startswith(draft['title'] + '\n\n' + draft['description'])
    assert text.endswith('Price: AUD 19.99 (postage extra)')
    assert '35.99' not in text and '12' not in text and 'Internal review' not in text


@pytest.mark.parametrize('price', [None, '', 'NaN', 'Infinity', '-1', '0', 'not confirmed'])
def test_copy_all_omits_unconfirmed_or_invalid_price(price):
    assert all_details({'title': 'Duck', 'description': 'Figure only.',
                        'confirmed_price': price, 'suggested_price': '19.99'}) == 'Duck\n\nFigure only.'
