from scripts.cache_one_piece_japanese import _clean_english_name, parse_cards, series_from_index


def test_parse_official_one_piece_series_and_variants():
    index = '<option value="550101">ブースターパック ROMANCE DAWN【OP-01】</option>'
    assert series_from_index(index) == [("550101", "ブースターパック ROMANCE DAWN【OP-01】")]
    page = '''
    <a class="modalOpen" data-src="#OP01-001"><img data-src="../images/cardlist/card/OP01-001_p2.png?1" alt="ロロノア・ゾロ"></a>
    <dl class="modalCol" id="OP01-001"><dt><div class="infoCol"><span>OP01-001</span> | <span>L</span> | <span>LEADER</span></div><div class="cardName">ロロノア・ゾロ</div></dt>
    '''
    cards = parse_cards(page)
    assert cards[0].id == "opjp-op01-001-p2"
    assert cards[0].number == "OP01-001"
    assert cards[0].variant == "Parallel 2"


def test_clean_english_name_removes_catalogue_suffixes():
    assert _clean_english_name("Roronoa Zoro - OP01-001", "OP01-001") == "Roronoa Zoro"
    assert _clean_english_name("Nami (Alternate Art)", "OP01-016") == "Nami"
