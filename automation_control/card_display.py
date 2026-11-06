"""English display text without changing a card's catalogue identity or language."""
import json
import re
from functools import lru_cache
from pathlib import Path

JP_SETS = {
    'sv2a': 'Pokemon Card 151', 'sv9': 'Battle Partners', 'sv10': 'The Glory of Team Rocket',
    'sv4k': 'Ancient Roar', 'sv4m': 'Future Flash', 'sv5m': 'Cyber Judge',
    'sv5a': 'Crimson Haze', 'sv8': 'Super Electric Breaker', 'sv8a': 'Terastal Festival ex',
    's8b': 'VMAX Climax', 'm1s': 'Mega Symphonia', 's12a': 'VSTAR Universe',
    'sv7': 'Stellar Miracle', 'sv3a': 'Raging Surf', 'sv11b': 'Black Bolt',
    'sv1v': 'Violet ex', 'sv4a': 'Shiny Treasure ex', 'sv6a': 'Night Wanderer',
    'sv6': 'Mask of Change',
}
SPECIAL_NAMES = {'ロケット団のアテナ': "Team Rocket's Ariana", '力の砂時計': 'Powerglass',
                 'テツノイバラ': 'Iron Thorns', 'テツノコウベ': 'Iron Jugulis',
                 'チヲハウハネ': 'Slither Wing', 'かがやくリザードン': 'Radiant Charizard'}

@lru_cache(maxsize=1)
def species_names():
    return json.loads((Path(__file__).parent / 'data/pokemon_species_names.json').read_text())


def language_family(value):
    value = (value or '').lower()
    if 'japan' in value or value in ('ja', 'jp', '日本語'): return 'ja'
    if 'chinese' in value or value.startswith('zh'): return 'zh'
    if 'english' in value or value == 'en': return 'en'
    return value


def english_name(value):
    value = (value or '').strip()
    # Prefer a readable translation supplied with a bilingual recognition.
    for part in re.findall(r'\(([^()]*)\)', value):
        if part.isascii() and re.search('[A-Za-z]', part): return part.strip()
    if value in SPECIAL_NAMES: return SPECIAL_NAMES[value]
    result = value
    for prefix, translated in [('ロケット団の', "Team Rocket's "), ('Nの', "N's "),
                               ('アローラ', 'Alolan '), ('ガラル', 'Galarian '),
                               ('ヒスイ', 'Hisuian '), ('メガ', 'Mega ')]:
        if result.startswith(prefix): result = translated + result[len(prefix):]
    # Longest species first prevents replacing part of an evolved form.
    for source in sorted(species_names(), key=len, reverse=True):
        if source in result: result = result.replace(source, species_names()[source])
    result = re.sub(r'(?<=[a-z])(?=ex$|GX$|VMAX$|VSTAR$|V$)', ' ', result)
    # Preserve untranslatable text instead of manufacturing a partial name.
    return result if not re.search(r'[\u3040-\u30ff\u3400-\u9fff]', result) else value


def display_name(item, card):
    return english_name(item.name) if language_family(card.language) in ('ja', 'zh') else item.name


def printed_set_code(value):
    match = re.search(r'(?<![A-Za-z0-9])((?:CBB|CB|CSV)\d+[A-Za-z]*)(?![A-Za-z0-9])', value or '', re.I)
    return match.group(1).upper() if match else ''


def display_set(card, card_set):
    value = card_set.name if card_set else ''
    if language_family(card.language) == 'ja':
        code = (card.set_code or (card.set_id or '').removeprefix('jp-')).lower()
        if code in JP_SETS: return JP_SETS[code]
        if re.search(r'[\u3040-\u30ff\u3400-\u9fff]', value): return card.set_code or (card.set_id or '').removeprefix('jp-')
    return value
