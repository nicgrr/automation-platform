from types import SimpleNamespace
from automation_control.representative_backs import select_back, attach_to_pair, with_notice, NOTICE, LEGACY_NOTICES


def test_language_and_game_select_the_correct_back():
    assert select_back('pokemon', 'Japanese').endswith('pokemon-japanese.jpg')
    for language in ('English', 'Chinese', 'Simplified Chinese', 'Traditional Chinese'):
        assert select_back('pokemon', language).endswith('pokemon-english-chinese.jpg')
    assert select_back('one_piece', 'English', 'SR').endswith('one-piece-standard.jpg')
    assert select_back('one_piece', 'Japanese', 'L').endswith('one-piece-leader.jpg')
    assert select_back('dragon_ball_super', 'Japanese') is None
    assert select_back('pokemon', '') is None


def test_actual_back_is_preserved_and_shared_back_can_be_reselected():
    pair = SimpleNamespace(back_image_path='/actual-back.jpg', extracted={})
    attach_to_pair(pair, 'pokemon', 'Japanese')
    assert pair.back_image_path == '/actual-back.jpg'
    pair.back_image_path = None
    attach_to_pair(pair, 'pokemon', 'Japanese')
    assert pair.extracted['representative_back']
    attach_to_pair(pair, 'pokemon', 'English')
    assert pair.back_image_path.endswith('pokemon-english-chinese.jpg')


def test_shared_back_is_disclosed_once():
    path = select_back('pokemon', 'Japanese')
    description = with_notice('Front photo.', [path])
    assert NOTICE in description
    assert with_notice(description, [path]) == description
    assert with_notice('Real photos.', ['/actual-back.jpg']) == 'Real photos.'


def test_notice_says_stock_photo_and_offers_a_real_back():
    assert 'stock photo' in NOTICE and 'message us' in NOTICE


def test_old_wording_is_replaced_not_duplicated():
    path = select_back('pokemon', 'English')
    old = 'Near Mint.\n\n' + LEGACY_NOTICES[0] + '\n\nShipped in a toploader.'
    updated = with_notice(old, [path])
    assert updated == 'Near Mint.\n\n' + NOTICE + '\n\nShipped in a toploader.'
    assert not any(legacy in updated for legacy in LEGACY_NOTICES)
    assert with_notice(updated, [path]) == updated
