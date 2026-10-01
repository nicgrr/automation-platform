from types import SimpleNamespace

from scripts.cache_bandai_fb09_japanese import english_display_names, parse_card_list


def test_parse_official_fb09_base_and_parallel_images():
    html = '''
    <li><img data-src="../../images/cards/card/jp/FB09-096.webp" alt="FB09-096 悪鬼を討つ戦士"></li>
    <li><img data-src="../../images/cards/card/jp/FB09-096_p1.webp" alt="FB09-096 悪鬼を討つ戦士"></li>
    <li><img data-src="../../images/cards/card/jp/FB09-121_p2.webp" alt="FB09-121 ゴジータ：BR"></li>
    '''
    cards = parse_card_list(html)
    assert [card.number for card in cards] == ["FB09-096", "FB09-096", "FB09-121"]
    assert [card.variant for card in cards] == [None, "Parallel", "Super Parallel"]
    assert cards[1].card_id == "dbfw-jp-fb09-096-p1"
    assert cards[0].image_url == "https://www.dbs-cardgame.com/fw/images/cards/card/jp/FB09-096.webp"


def test_english_names_prefer_clean_base_title():
    class Query:
        def filter(self, *_args):
            return self

        def all(self):
            return [
                SimpleNamespace(number="FB09-096", name="Warrior Who Defeats the Evil Demon (Alternate Art)"),
                SimpleNamespace(number="FB09-096", name="Warrior Who Defeats the Evil Demon"),
            ]

    session = SimpleNamespace(query=lambda *_args: Query())
    assert english_display_names(session)["FB09-096"] == "Warrior Who Defeats the Evil Demon"
