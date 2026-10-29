"""Per-listing finish overrides, independent of catalogue rarity."""
from html import escape
from types import SimpleNamespace
FINISHES = {'normal':'Normal / Non-holo','holo':'Holo','reverse_holo':'Reverse Holo',
            'pokeball_pattern':'Poké Ball Reverse Holo','masterball_pattern':'Master Ball Reverse Holo',
            'cosmos_holo':'Cosmos Holo'}


def finish_field(listing):
    current = getattr(listing, 'finish', None) or ''
    options = "<option value=''>Use detected finish</option>" + ''.join(
        f"<option value='{key}'{' selected' if current == key else ''}>{escape(label)}</option>"
        for key,label in FINISHES.items())
    return ("<label class='field studio-field'>Finish / foil treatment<select name='finish'>" + options +
            "</select></label><small>Separate from rarity (IR, ex, etc.). Changing finish refreshes the generated eBay title and description. Save to review before publishing.</small>")


def finish_label(listing):
    return FINISHES.get(getattr(listing, 'finish', None), '')


def title_inventory(listing, inventory):
    label=finish_label(listing)
    return SimpleNamespace(variant=SimpleNamespace(value='Normal' if listing.finish=='normal' else label)) if label else inventory


def save_finish(listing, value):
    if value is None: return
    if value and value not in FINISHES: raise ValueError('Choose a valid card finish.')
    value=value or None
    if getattr(listing,'finish',None)!=value:
        listing.finish=value
        listing.ebay_title=None
        listing.ebay_description=None
