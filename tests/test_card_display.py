from types import SimpleNamespace as Obj
from automation_control.card_display import english_name, display_set
from automation_control.ebay_titles import build_single_card_ebay_title


def test_translated_species_retains_forms_and_bilingual_spaces():
    assert english_name('ヒトカゲ') == 'Charmander'
    assert english_name('Nのゾロアークex') == "N's Zoroark ex"
    assert english_name('メガサーナイトex') == 'Mega Gardevoir ex'
    assert english_name('ロケット団のクロバットex') == "Team Rocket's Crobat ex"
    assert english_name('月亮伊布 (Umbreon)') == 'Umbreon'
    assert english_name('リザードンVSTAR') == 'Charizard VSTAR'


def test_japanese_title_keeps_language_and_printing():
    item = Obj(name='ヒトカゲ')
    card = Obj(game='pokemon_japan', set_id='jp-sv2a', set_code='sv2a', number='168',
               language='Japanese', rarity='AR', parallel=None, variant=None)
    card_set = Obj(name='ポケモンカード151', printed_total=165, total=210)
    title = build_single_card_ebay_title(item, card, card_set)
    assert 'Charmander' in title and '168/165' in title and 'Japanese' in title
    assert 'Pokemon Card 151' in title and len(title) <= 80
    assert item.name == 'ヒトカゲ'


def test_chinese_number_not_expanded_from_foreign_set_total():
    card = Obj(game='pokemon', set_id='local-cn', set_code='CBB2C', number='0113',
               language='Simplified Chinese', rarity='RR', parallel=None, variant=None)
    card_set = Obj(name='CBB2C', printed_total=165, total=210)
    title = build_single_card_ebay_title(Obj(name='伊布V'), card, card_set)
    assert 'Eevee V' in title and 'CBB2C 0113' in title
    assert '/165' not in title and 'Chinese' in title and len(title) <= 80
