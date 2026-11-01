"""Deterministic buyer-facing copy from the owner's collectible details.

This module does not identify figures, infer condition, or change stock/prices.
Callers remain responsible for escaping the returned text when rendering HTML.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


_UNKNOWN = frozenset({
    '', '?', '-', 'unknown', 'unidentified', 'unidentified figure', 'unknown figure',
    'not identified', 'needs identification', 'needs review', 'needs_review',
    'not visible / not printed', 'not visible / not_printed', 'not visible',
    'not provided', 'not confirmed', 'unconfirmed', 'tbc', 'tbd', 'n/a', 'none',
})
_MAX_TITLE_LENGTH = 256


def _field(value: object) -> str:
    """Keep user text, with consistent whitespace and no placeholder labels."""
    text = ' '.join(str(value).split()) if value is not None else ''
    return '' if text.casefold() in _UNKNOWN else text


def _contains_component(text: str, component: str) -> bool:
    if not component:
        return False
    return re.search(r'(?<!\w)' + re.escape(component.casefold()) + r'(?!\w)',
                     text.casefold()) is not None


def _title(brand: str, series: str, name: str) -> str:
    parts = []
    if brand and not _contains_component(name, brand):
        parts.append(brand)
    if name:
        parts.append(name)
    if series and not any(_contains_component(part, series) for part in parts):
        parts.append(series)
    if not parts:
        parts.append('Collectible')
    if not any(_contains_component(part, 'figure') for part in parts):
        parts.append('Figure')
    result = ' '.join(parts)
    if len(result) > _MAX_TITLE_LENGTH:
        shortened = result[:_MAX_TITLE_LENGTH - 1].rstrip()
        # Preserve complete words when there is a useful nearby boundary.
        boundary = shortened.rfind(' ')
        if boundary > _MAX_TITLE_LENGTH // 2:
            shortened = shortened[:boundary]
        result = shortened + '…'
    return result


def generate_copy(
    brand: str | None,
    series: str | None,
    name: str | None,
    *,
    condition: str = 'Opened / used',
    included: str = 'Figure only; no box or collector card included.',
    notes: str = '',
) -> dict[str, str]:
    """Generate fresh copy, so a changed identity cannot retain an old name.

    Condition and included contents use conservative defaults for this owner's
    opened figures. Explicit blank or unknown values omit those lines instead.
    """
    brand, series, name = (_field(value) for value in (brand, series, name))
    details = (
        ('Brand', brand), ('Series / Set', series), ('Figure', name),
        ('Condition', _field(condition)), ('Includes', _field(included)),
        ('Condition notes', _field(notes)),
    )
    lines = [f'{label}: {value}' for label, value in details if value]
    lines.append("Please review the photos for the item's condition.")
    return {'title': _title(brand, series, name), 'description': '\n'.join(lines)}


def all_details(draft: Mapping[str, object]) -> str:
    """Clipboard text with confirmed price only; never turn stock into a lot."""
    sections = [str(draft.get(field) or '').strip()
                for field in ('title', 'description')]
    sections = [section for section in sections if section]
    raw_price = draft.get('confirmed_price')
    try:
        amount = Decimal(str(raw_price))
    except (InvalidOperation, ValueError):
        amount = None
    if amount is not None and amount.is_finite() and amount > 0:
        currency = str(draft.get('currency') or 'AUD').upper().strip()
        if not re.fullmatch(r'[A-Z]{3}', currency):
            currency = 'AUD'
        sections.append(f'Price: {currency} {amount:.2f} (postage extra)')
    return '\n\n'.join(sections)
