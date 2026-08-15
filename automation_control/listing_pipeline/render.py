from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from .config import PipelineConfig
from .models import Listing

TEMPLATE_DIR = Path(__file__).parent / "templates"
MAX_TITLE_LENGTH = 80
CONDITION_PLACEHOLDER = "<<EDIT PER LISTING>>"


def get_environment() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)


def _resolve_term(listing: Listing, key: str, config: PipelineConfig) -> str | None:
    if key == "game":
        return config.ebay.game
    if not listing.is_bundle:
        return getattr(listing.cards[0], key, None)
    return None


def build_title(listing: Listing, config: PipelineConfig) -> str:
    if listing.is_bundle:
        if not listing.title_override:
            raise ValueError(
                f"listing '{listing.custom_label}': bundle listings need an entry in "
                f"config's [bundle_titles] since a title can't be derived from a single card"
            )
        title = listing.title_override
        return title if len(title) <= MAX_TITLE_LENGTH else title[:MAX_TITLE_LENGTH]

    term_order = config.title.term_order
    terms = [value for key in term_order if (value := _resolve_term(listing, key, config))]
    if not terms:
        raise ValueError(f"listing '{listing.custom_label}': no title terms could be resolved from config.title.term_order")
    title = " ".join(terms)
    while len(title) > MAX_TITLE_LENGTH and len(terms) > 1:
        terms = terms[:-1]
        title = " ".join(terms)
    if len(title) > MAX_TITLE_LENGTH:
        title = title[:MAX_TITLE_LENGTH]
    return title


def _condition_summary(listing: Listing) -> str:
    if any(card.condition is None for card in listing.cards):
        return Markup(CONDITION_PLACEHOLDER)  # deliberate literal marker, not escaped -- see UPLOAD_INSTRUCTIONS.md
    values = {card.condition for card in listing.cards}
    return values.pop() if len(values) == 1 else Markup(CONDITION_PLACEHOLDER)


def build_description(listing: Listing, env: Environment | None = None) -> str:
    env = env or get_environment()
    template = env.get_template("description.html.j2")
    return template.render(
        listing=listing,
        cards=listing.cards,
        is_bundle=listing.is_bundle,
        condition_summary=_condition_summary(listing),
    )


@dataclass(frozen=True)
class RenderedListing:
    listing: Listing
    title: str
    description_html: str


def render_listing(listing: Listing, config: PipelineConfig, env: Environment | None = None) -> RenderedListing:
    env = env or get_environment()
    return RenderedListing(
        listing=listing,
        title=build_title(listing, config),
        description_html=build_description(listing, env),
    )


def render_all(listings: list[Listing], config: PipelineConfig) -> list[RenderedListing]:
    env = get_environment()
    return [render_listing(listing, config, env) for listing in listings]
