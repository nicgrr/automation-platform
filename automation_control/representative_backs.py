"""Seller-authorized shared backs for front-only card photography."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'scan_ingest_data/media/listing_photos/representative-backs'
NOTICE = ('The front photograph shows the card offered. The back photograph is a shared '
          'representative image, not a photograph of this individual card, and does not show its back condition.')


def is_representative(path):
    return bool(path) and Path(path).resolve().parent == ROOT.resolve()


def select_back(game, language, rarity=''):
    game = (game or '').lower().strip()
    language = (language or '').lower().strip()
    filename = None
    if game in ('pokemon', 'pokemon_japan'):
        if language in ('japanese', 'ja', 'jp', '日本語'):
            filename = 'pokemon-japanese.jpg'
        elif language in ('english', 'en', 'chinese', 'zh', 'zh-cn', 'zh-tw', 'simplified chinese', 'traditional chinese', 'chinese (simplified)', 'chinese (traditional)'):
            filename = 'pokemon-english-chinese.jpg'
    elif game == 'one_piece':
        filename = 'one-piece-leader.jpg' if (rarity or '').strip().lower() in ('l', 'leader') else 'one-piece-standard.jpg'
    path = ROOT / filename if filename else None
    return str(path) if path and path.is_file() else None


def with_notice(description, paths):
    if any(is_representative(p) for p in paths or []):
        return description if NOTICE in description else description.rstrip() + '\n\n' + NOTICE
    return description


def attach_to_pair(pair, game, language, rarity=''):
    if pair.back_image_path and not is_representative(pair.back_image_path):
        return
    path = select_back(game, language, rarity)
    pair.back_image_path = path
    pair.extracted = {**(pair.extracted or {}), 'representative_back': bool(path)}
