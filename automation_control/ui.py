"""Shared page shell and styling for the EzBay dashboard. Plain strings, no
template engine or external assets -- keeps this private admin tool
self-contained (no CDN fonts/scripts) and consistent with the rest of this
codebase's no-framework-magic style.

Light theme, unified across every page (2026-09-07): this used to be a dark
"trading desk" look with only /dashboard broken out into its own light
shell. Every color here is a CSS custom property specifically so a page's
own `_STYLE` block (collectibles.py, business_records.py, etc.) keeps
working unchanged by referencing `var(--panel)`/`var(--text)`/etc. rather
than hardcoding hex values -- that's what made this a token-swap instead of
a per-page rewrite. The one hardcoded literal several pages still shared
(`#0a0f1c` for recessed input/detail backgrounds) is now `var(--surface-sunken)`.
"""

from html import escape

STYLE = """<style>
:root {
  --bg: #f4f5f8;
  --panel: #ffffff;
  --panel-border: #e5e7eb;
  --surface-sunken: #f4f5f8;
  --accent: #0891b2;
  --accent-2: #7c3aed;
  --accent-rgb: 8, 145, 178;
  --text: #12141c;
  --text-dim: #6b7280;
  --text-faint: #9ca3af;
  --success: #059669;
  --success-bg: #ecfdf5;
  --danger: #dc2626;
  --danger-bg: #fef2f2;
  --warning: #b45309;
  --warning-bg: #fffbeb;
  --neutral-bg: #e0f2fe;
  --radius: 14px;
  --shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
  --shadow-lift: 0 8px 20px rgba(16, 24, 40, 0.14);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, ui-sans-serif, sans-serif;
  line-height: 1.5;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
main { max-width: 1180px; margin: 0 auto; padding: 32px 24px 80px; }

.back-link {
  display: inline-flex; align-items: center; gap: 5px; color: var(--text-dim); font-size: 13px;
  text-decoration: none; margin-bottom: 14px;
}
.back-link:hover { color: var(--accent); text-decoration: none; }
.page-title { font-size: 24px; margin: 0 0 4px; letter-spacing: -0.01em; font-weight: 800; color: var(--text); }
.subtitle { color: var(--text-dim); margin: 0 0 28px; font-size: 13.5px; }

.stat-group { margin-bottom: 28px; }
.stat-group:last-of-type { margin-bottom: 32px; }
.group-label { font-size: 11px; color: var(--text-faint); text-transform: uppercase; letter-spacing: 0.12em; margin: 0 0 12px; }

.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 32px; }
.stat-group .stat-grid { margin-bottom: 0; }
.stat-card { background: var(--panel); border: 1px solid var(--panel-border); border-radius: var(--radius); padding: 18px 20px; box-shadow: var(--shadow); }
.stat-card .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-faint); margin-bottom: 8px; }
.stat-card .value { font-size: 20px; font-weight: 700; }
.stat-card .value .value-sub { font-size: 13px; font-weight: 500; color: var(--text-dim); }

.pill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 11px; border-radius: 999px; font-size: 12px; font-weight: 650; }
.pill.ok { background: var(--success-bg); color: var(--success); }
.pill.bad { background: var(--danger-bg); color: var(--danger); }
.pill.warn { background: var(--warning-bg); color: var(--warning); }
.pill.neutral { background: var(--neutral-bg); color: var(--accent); }
.pill.info { background: rgba(124, 58, 237, 0.12); color: var(--accent-2); }

.panel { background: var(--panel); border: 1px solid var(--panel-border); border-radius: var(--radius); padding: 24px; margin-bottom: 24px; box-shadow: var(--shadow); }
.panel h2 { margin: 0 0 16px; font-size: 14px; letter-spacing: 0.02em; font-weight: 700; color: var(--text); display: flex; align-items: center; gap: 8px; }
.panel h2::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--accent); flex: none; }
.panel h3 { font-size: 12px; color: var(--text-faint); text-transform: uppercase; letter-spacing: 0.08em; margin: 22px 0 10px; }
.panel h3:first-of-type { margin-top: 0; }
.panel > p { color: var(--text-dim); font-size: 13px; }

.btn { display: inline-flex; align-items: center; gap: 8px; background: linear-gradient(120deg, var(--accent), var(--accent-2)); color: #fff; font-weight: 700; padding: 10px 20px; border-radius: 10px; text-decoration: none; font-size: 14px; box-shadow: var(--shadow); }
.btn:hover { filter: brightness(1.06); text-decoration: none; }
.ghost { background: transparent; border: 1px solid var(--panel-border); color: var(--text-dim); }
.ghost:hover { border-color: var(--danger); color: var(--danger); }

.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; white-space: nowrap; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--panel-border); }
th { color: var(--text-faint); font-weight: 650; text-transform: uppercase; font-size: 11px; letter-spacing: 0.06em; }
tr:hover td { background: rgba(var(--accent-rgb), 0.04); }

ul.events { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
ul.events li { font-size: 13px; color: var(--text-dim); border-left: 2px solid var(--panel-border); padding-left: 10px; }

details { background: var(--surface-sunken); border: 1px solid var(--panel-border); border-radius: 10px; padding: 10px 14px; }
details summary { cursor: pointer; font-size: 13px; list-style: revert; }
details summary::marker { color: var(--accent); }

form { display: flex; flex-direction: column; gap: 14px; max-width: 320px; }
label { font-size: 13px; color: var(--text-dim); display: flex; flex-direction: column; gap: 6px; }
input, select, textarea { background: var(--surface-sunken); border: 1px solid var(--panel-border); border-radius: 8px; padding: 10px 12px; color: var(--text); font-size: 14px; font-family: inherit; }
input:focus, select:focus, textarea:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.15); }
button { background: linear-gradient(120deg, var(--accent), var(--accent-2)); border: none; padding: 11px; border-radius: 8px; color: #fff; font-weight: 700; cursor: pointer; font-size: 14px; }
button:hover { filter: brightness(1.06); }
</style>"""


# Shared topbar: brand, global search (autocomplete), a small persistent
# nav -- present on every page via page() below. Was previously duplicated
# almost verbatim inside dashboard.py's own bespoke shell; consolidated
# here after that duplication let the two copies drift (the standalone one
# shipped with no mobile breakpoint at all -- on a narrow phone screen the
# nav links and the search box fought over the same row with nothing
# allowed to shrink, so the search box was squeezed to a few px wide. Fixed
# below with `min-width: 0` on the flex item plus a breakpoint that drops
# the nav links rather than the search box, since search is the thing this
# app is actually used for from a phone).
TOPBAR_STYLE = """<style>
.topbar { display: flex; align-items: center; gap: 20px; background: var(--panel); border-bottom: 1px solid var(--panel-border); padding: 12px 20px; position: sticky; top: 0; z-index: 10; }
.topbar-brand { font-size: 19px; font-weight: 800; letter-spacing: -0.02em; flex: none; color: var(--text); }
.topbar-brand:hover { text-decoration: none; }
.topbar-brand .mark { background: linear-gradient(120deg, var(--accent), var(--accent-2)); -webkit-background-clip: text; background-clip: text; color: transparent; }
.topbar-search { flex: 1 1 auto; min-width: 0; max-width: 420px; }
.autocomplete-wrap { position: relative; width: 100%; }
.autocomplete-wrap input { width: 100%; padding: 9px 14px; border-radius: 999px; border: 1px solid var(--panel-border); background: var(--surface-sunken); font-size: 13.5px; }
.autocomplete-wrap input:focus { background: var(--panel); }
.autocomplete-dropdown { position: absolute; top: calc(100% + 8px); left: 0; right: 0; z-index: 30; background: var(--panel); border: 1px solid var(--panel-border); border-radius: 12px; box-shadow: var(--shadow-lift); overflow: hidden; }
.ac-item { display: flex; align-items: center; gap: 10px; padding: 10px 14px; cursor: pointer; font-size: 13.5px; }
.ac-item.active, .ac-item:hover { background: var(--surface-sunken); }
.ac-name { font-weight: 650; flex: 0 1 auto; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--text); }
.ac-detail { color: var(--text-dim); font-size: 12px; flex: 1; }
.ac-owned { font-size: 11px; color: var(--success); background: var(--success-bg); padding: 2px 8px; border-radius: 999px; }
.topbar-nav { display: flex; align-items: center; gap: 18px; font-size: 13.5px; color: var(--text-dim); flex: none; margin-left: auto; }
.topbar-nav a { color: var(--text-dim); }
.topbar-nav a:hover { color: var(--accent); text-decoration: none; }
.navtoggle{display:inline-grid;place-items:center;width:34px;height:34px;flex:none;cursor:pointer;
  background:transparent;border:1px solid var(--panel-border);border-radius:8px;color:var(--text-dim);padding:0}
.navtoggle:hover{border-color:var(--accent);color:var(--accent)}
.navtoggle svg{width:16px;height:16px}
.topbar-refresh{display:inline-grid;place-items:center;width:34px;height:34px;flex:none;cursor:pointer;
  background:transparent;border:1px solid var(--panel-border);border-radius:8px;color:var(--text-dim);padding:0}
.topbar-refresh:hover{border-color:var(--accent);color:var(--accent);filter:none}
.topbar-refresh:disabled{opacity:.55;cursor:wait}
.topbar-refresh svg{width:17px;height:17px}
.topbar-refresh.is-refreshing svg{animation:refresh-spin .7s linear infinite}
@keyframes refresh-spin{to{transform:rotate(360deg)}}

.scrim{position:fixed;inset:0;background:rgba(18,20,28,.38);opacity:0;pointer-events:none;
  transition:opacity .16s ease;z-index:28}
body.nav-open .scrim{opacity:1;pointer-events:auto}

.sidenav{position:fixed;top:0;left:0;bottom:0;width:268px;z-index:30;background:var(--panel);
  border-right:1px solid var(--panel-border);padding:16px 0 26px;overflow-y:auto;
  transform:translateX(-100%);transition:transform .18s ease;box-shadow:var(--shadow-lift)}
body.nav-open .sidenav{transform:none}
.sidenav-head{display:flex;align-items:center;justify-content:space-between;padding:0 16px 14px;
  border-bottom:1px solid var(--panel-border);margin-bottom:6px}
.sidenav-brand{font-size:17px;font-weight:800;letter-spacing:-.02em}
.sidenav-brand .mark{background:linear-gradient(120deg,var(--accent),var(--accent-2));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.sidenav-close{background:none;border:none;color:var(--text-dim);font-size:21px;line-height:1;
  cursor:pointer;padding:0 4px}
.sidenav-close:hover{color:var(--accent)}
.navgroup{padding:10px 0 2px}
.navgroup h4{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--text-faint);
  margin:0 0 6px;padding:0 16px;font-weight:700}
.sidenav a{display:flex;align-items:center;gap:8px;padding:7px 16px;font-size:13.5px;
  color:var(--text);text-decoration:none;border-left:3px solid transparent}
.sidenav a:hover{background:var(--surface-sunken);color:var(--accent);text-decoration:none}
.sidenav a .navlabel{flex:1 1 auto;min-width:0}
/* current page: a filled rail rather than a background wash, so it still
   reads as the active item when a hovered sibling is also shaded */
.sidenav a.is-active{border-left-color:var(--accent);color:var(--accent);font-weight:700;
  background:rgba(var(--accent-rgb),.06)}

/* work-waiting badge -- only rendered when a queue is non-empty, so an
   absent badge means "nothing to do", never "not counted" */
.navcount{flex:none;min-width:20px;padding:1px 7px;border-radius:999px;font-size:11px;
  font-weight:700;text-align:center;background:var(--warning-bg);color:var(--warning);
  font-variant-numeric:tabular-nums}
.sidenav a.is-active .navcount{background:var(--panel);color:var(--warning)}

.navgroup-fold{background:none;border:none;border-radius:0;padding:10px 0 2px;margin:0}
.navgroup-fold summary{display:flex;align-items:center;gap:8px;padding:0 16px;margin-bottom:6px;
  font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--text-faint);
  font-weight:700;cursor:pointer;list-style:none}
.navgroup-fold summary::-webkit-details-marker{display:none}
.navgroup-fold summary::before{content:"\25B8";font-size:9px;letter-spacing:0;transition:transform .15s ease}
.navgroup-fold[open] summary::before{transform:rotate(90deg)}
.navgroup-fold summary:hover{color:var(--accent)}
.foldcount{margin-left:auto;letter-spacing:0;font-size:10px;color:var(--text-faint);
  background:var(--surface-sunken);border-radius:999px;padding:1px 7px}

.navsearch-open{display:flex;align-items:center;justify-content:space-between;gap:10px;
  width:calc(100% - 32px);margin:6px 16px 4px;padding:8px 12px;background:var(--surface-sunken);
  border:1px solid var(--panel-border);border-radius:8px;color:var(--text-dim);
  font-size:13px;font-weight:500;cursor:pointer}
.navsearch-open:hover{border-color:var(--accent);color:var(--accent)}
.navsearch-open kbd{font-family:inherit;font-size:10.5px;font-weight:700;letter-spacing:.04em;
  background:var(--panel);border:1px solid var(--panel-border);border-radius:5px;padding:2px 6px;
  color:var(--text-faint)}

/* jump-to palette */
.palette{position:fixed;inset:0;z-index:40;display:flex;align-items:flex-start;justify-content:center;
  padding:12vh 20px 20px}
/* an author `display` beats the UA's [hidden]{display:none}, so both of
   these need saying explicitly -- without them the palette is open on
   every page load and filtering never hides a row */
.palette[hidden]{display:none}
.palette-scrim{position:absolute;inset:0;background:rgba(18,20,28,.42)}
.palette-box{position:relative;width:100%;max-width:520px;background:var(--panel);
  border:1px solid var(--panel-border);border-radius:14px;box-shadow:var(--shadow-lift);
  overflow:hidden;display:flex;flex-direction:column;max-height:70vh}
.palette-input{border:none;border-bottom:1px solid var(--panel-border);border-radius:0;
  background:var(--panel);padding:15px 18px;font-size:15px;flex:none}
.palette-input:focus{outline:none;box-shadow:none;border-color:var(--panel-border)}
.palette-list{overflow-y:auto;padding:6px 0}
.pal-item{display:flex;align-items:baseline;gap:10px;padding:9px 18px;cursor:pointer;font-size:13.5px}
.pal-item[hidden]{display:none}
.pal-item.active,.pal-item:hover{background:var(--surface-sunken)}
.pal-item.active .pal-label{color:var(--accent)}
.pal-label{font-weight:650;color:var(--text)}
.pal-group{margin-left:auto;font-size:11px;color:var(--text-faint);text-transform:uppercase;
  letter-spacing:.08em;flex:none}
.palette-empty{padding:18px;font-size:13px;color:var(--text-dim);text-align:center}

@media (max-width: 680px) {
  .topbar-nav { display: none; }
  .topbar-search { max-width: none; }
}
</style>"""

# The drawer is intentionally a map of workflows, not a catalogue of every
# route EzBay happens to expose. The old version listed 39 destinations at
# once; seven review queues, seven capture variants and fourteen selling / buying
# tools all competed at the same visual level. ADMIN_NAV is now the short map
# of the owner's five daily workflows, with Dashboard and System around them.
# TOOL_NAV below remains the complete map used by Ctrl-K and the workflow hubs,
# so condensing the drawer never makes a tool undiscoverable or breaks a saved
# direct URL.
ADMIN_NAV = [
    ("Home", [
        ("/dashboard", "Dashboard"),
        ("/analytics", "Analytics"),
    ], False),
    ("Add stock", [
        ("/scan-add", "Scan & add"),
    ], False),
    ("Review", [
        ("/review-centre", "Review centre"),
    ], False),
    ("Inventory", [
        ("/inventory", "Inventory"),
        ("/personal-collection", "Personal collection"),
    ], False),
    ("Sell", [
        ("/ebay", "eBay"),
        ("/ebay-listing-cleanup", "eBay listing cleanup"),
        ("/ebay-sales", "Pack Assistant"),
        ("/nl-vault", "NL Vault shop"),
    ], False),
    ("Research", [
        ("/market-tools", "Market tools"),
    ], False),
    ("System", [
        ("/settings", "Settings & data"),
    ], False),
]


# Every individual tool remains in the command palette and on one of the
# workflow hub pages. Keeping this separate from ADMIN_NAV is the important
# part: the drawer stays calm while Ctrl-K still answers precise requests such
# as "foil", "bulk cards" or "eBay export" in one step.
TOOL_NAV = [
    ("Scanning", [
        ("/feed", "Scan feed"),
        ("/scan-ingest", "Scanner status & logs"),
    ], False),
    ("Review", [
        ("/review", "Scan crops"),
        ("/foil-review", "Foil review"),
        ("/inventory-audit", "Photo audit"),
        ("/collectibles/review", "Photographed collectibles"),
        ("/accessories/review", "Photographed supplies"),
        ("/cards/review", "Photographed cards"),
        ("/cards/pricing", "Card price review"),
    ], False),
    ("Inventory", [
        ("/search", "Search catalogue"),
        ("/sealed-products", "Sealed products"),
        ("/collectibles", "Collectibles"),
        ("/accessories", "Card supplies"),
    ], False),
    ("Add stock", [
        ("/cards/capture/bulk", "Bulk upload cards"),
        ("/collectibles/capture", "Photograph a collectible"),
        ("/accessories/capture", "Photograph a supply item"),
        ("/collectibles/capture/bulk", "Bulk upload collectibles"),
        ("/accessories/capture/bulk", "Bulk upload supplies"),
        ("/collectibles/identify", "Identify from a link"),
    ], False),
    ("NL Vault", [
        ("/shop-listings", "Shop listings"),
        ("/shop-pricing", "Shop pricing"),
        ("/shop-orders", "Shop orders"),
        ("/contact-messages", "Contact messages"),
    ], False),
    ("eBay", [
        ("/ebay-listings", "eBay live listings"),
        ("/ebay-sales", "eBay sales (pack assistant)"),
        ("/ebay-export", "eBay bulk upload file"),
    ], False),
    ("Sales records", [
        ("/sales", "Sales ledger & direct sale"),
        ("/customers", "Customers"),
    ], False),
    ("Market tools", [
        ("/collectibles/pricing", "Collectibles pricing"),
        ("/market-search", "Market search"),
        ("/market-watch", "Sonny Angel watch"),
        ("/deal-check", "Deal check"),
        ("/buying-calculator", "Buying calculator"),
        ("/release-calendar", "Release calendar"),
        ("/retailer-goals", "Retailer Goals"),
        ("/purchase-lots", "Purchase lots"),
        ("/suppliers", "Suppliers"),
    ], False),
    ("Data", [
        ("/export", "Export data"),
    ], False),
]


# Which condensed drawer item should stay highlighted while an individual
# tool is open. The client picks the longest matching prefix, so
# /collectibles/review correctly belongs to Review rather than the broader
# /collectibles inventory family.
NAV_ACTIVE_PREFIXES = {
    "/dashboard": ("/dashboard",),
    "/analytics": ("/analytics",),
    "/scan-add": (
        "/scan-add", "/feed", "/scan-ingest", "/cards/capture",
        "/collectibles/capture", "/accessories/capture", "/collectibles/identify",
    ),
    "/review-centre": (
        "/review-centre", "/review", "/foil-review", "/inventory-audit",
        "/collectibles/review", "/accessories/review", "/cards/review", "/cards/pricing",
    ),
    "/inventory": ("/inventory", "/search", "/sealed-products", "/collectibles", "/accessories"),
    "/personal-collection": ("/personal-collection",),
    "/ebay": ("/ebay", "/ebay-listings", "/ebay-sales", "/ebay-export", "/ebay-listing-cleanup"),
    "/ebay-sales": ("/ebay-sales",),
    "/nl-vault": ("/nl-vault", "/shop-listings", "/shop-pricing", "/shop-orders", "/contact-messages"),
    "/sales": ("/sales", "/customers"),
    "/market-tools": ("/market-tools", "/collectibles/pricing", "/market-search", "/market-watch", "/deal-check", "/buying-calculator", "/release-calendar", "/retailer-goals", "/purchase-lots", "/suppliers"),
    "/settings": ("/settings", "/export"),
}

# Built, routed, and deliberately not listed anywhere.
#
# Each of these can only ever be filled by typing into the page itself,
# and none of them ever has been -- every table behind them is empty, and
# nothing else in the app or the storefront writes to any of them. A
# drawer that shows nine destinations which have never held a row costs
# attention on every single page load and gives none of it back.
#
# Hidden, not deleted: the routes still answer, so a bookmarked or typed
# URL works exactly as before, and the pages themselves are untouched.
# Moving one back into ADMIN_NAV above is the whole of un-hiding it. The
# day one of these tables has rows is the day its entry goes back.
#
# Deliberately excluded from this list: /shop-orders and /contact-messages
# are equally empty but are fed by the live shop and contact form, so they
# fill on their own and stay visible. /release-calendar had rows already.
HIDDEN_NAV = [
    ("/potential-stock", "Potential stock"),
    ("/whatnot", "Whatnot shows"),
    ("/marketplaces", "Marketplaces"),
]

# Which links get a "work waiting" badge. Kept next to the nav rather than
# inside nav_counts.py so the two halves of the contract -- what the server
# counts, and what the drawer shows -- are visible together.
# Only /review-centre is actually in ADMIN_NAV (the drawer was condensed
# from ~39 links down to today's 10 -- see ADMIN_NAV's own comment); the
# other seven review-queue routes this set used to list are never passed
# to _nav_link at all, so a badge span for them could never exist in the
# DOM to begin with. Harmless as dead weight (queue_counts() below still
# computes their real numbers, just for dashboard.py's Operations widget
# and /review-centre's own inline breakdown, not a nav badge), but listed
# here it read as "these have badges too", which they never did.
NAV_COUNT_HREFS = {"/review-centre"}


def _nav_link(href: str, label: str) -> str:
    badge = (
        f"<span class='navcount' data-count-for='{href}' hidden></span>"
        if href in NAV_COUNT_HREFS else ""
    )
    prefixes = "|".join(NAV_ACTIVE_PREFIXES.get(href, (href,)))
    return (
        f"<a href='{href}' data-nav-href='{href}' data-nav-prefixes='{escape(prefixes, quote=True)}'>"
        f"<span class='navlabel'>{escape(label)}</span>{badge}</a>"
    )


def _nav_group(title: str, links: list, collapsed: bool) -> str:
    body = "".join(_nav_link(href, label) for href, label in links)
    if not collapsed:
        return f"<div class='navgroup'><h4>{escape(title)}</h4>{body}</div>"
    # <details> rather than a JS toggle: it remembers nothing between page
    # loads, which is the behaviour wanted here -- shut every time, because
    # the point is that these don't ask for attention.
    return (
        f"<details class='navgroup navgroup-fold'><summary>{escape(title)}"
        f"<span class='foldcount'>{len(links)}</span></summary>{body}</details>"
    )


def _sidenav_html() -> str:
    """The eleven workflow entrances shared by every EzBay page.

    Individual tools live on their workflow hub and in Ctrl-K rather than
    expanding this drawer back into the forty-link route catalogue it
    replaced. The dashboard renders its quicklinks from this same source.
    """
    groups = "".join(_nav_group(title, links, collapsed) for title, links, collapsed in ADMIN_NAV)
    return (
        "<div class='scrim' data-nav-close></div>"
        "<aside class='sidenav' aria-label='Main navigation'>"
        "<div class='sidenav-head'>"
        "<a class='sidenav-brand' href='/dashboard'><span class='mark'>Ez</span>Bay</a>"
        "<button class='sidenav-close' data-nav-close aria-label='Close navigation'>&times;</button>"
        "</div>"
        "<button class='navsearch-open' data-palette-open>"
        "<span>Jump to\u2026</span><kbd>Ctrl K</kbd></button>"
        f"{groups}"
        "<div class='navgroup'><h4>Storefront</h4>"
        "<a href='#' data-shop-link target='_blank' rel='noopener'>"
        "<span class='navlabel'>NL Vault shop &#8599;</span></a>"
        "</div>"
        "</aside>"
        + _palette_html()
    )


def _palette_html() -> str:
    """Ctrl-K over every nav destination. The drawer is a good map once you
    know roughly where a thing lives; this is for when you don't, which
    with ~40 destinations is most of the time. Deliberately local-only --
    the whole list ships in the page, so it answers instantly and works
    with the network down. Card search stays in the topbar box, which is
    always visible and already does exactly that."""
    rows = []
    seen = set()
    for title, links, _collapsed in ADMIN_NAV + TOOL_NAV:
        for href, label in links:
            if href in seen:
                continue
            seen.add(href)
            rows.append(
                f"<div class='pal-item' data-href='{href}' "
                f"data-hay='{escape((label + ' ' + title + ' ' + href).lower(), quote=True)}'>"
                f"<span class='pal-label'>{escape(label)}</span>"
                f"<span class='pal-group'>{escape(title)}</span></div>"
            )
    return (
        "<div class='palette' data-palette hidden>"
        "<div class='palette-scrim' data-palette-close></div>"
        "<div class='palette-box' role='dialog' aria-modal='true' aria-label='Jump to'>"
        "<input class='palette-input' type='text' placeholder='Jump to\u2026' "
        "autocomplete='off' spellcheck='false' data-palette-input>"
        f"<div class='palette-list' data-palette-list>{''.join(rows)}</div>"
        "<div class='palette-empty' data-palette-empty hidden>No match</div>"
        "</div></div>"
    )


NAV_SCRIPT = r"""<script>
(function(){
  var body = document.body;
  document.querySelectorAll('[data-page-refresh]').forEach(function(button){
    button.addEventListener('click', function(){
      button.disabled = true;
      button.classList.add('is-refreshing');
      button.setAttribute('aria-label', 'Refreshing page');
      location.reload();
    });
  });
  function nav(href){
    if (window.EzBayProgress) window.EzBayProgress.go(href); else location.href = href;
  }
  function set(open){
    body.classList.toggle('nav-open', open);
    var t = document.querySelector('.navtoggle');
    if (t) t.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) loadCounts();
  }
  document.querySelectorAll('[data-nav-open]').forEach(function(el){
    el.addEventListener('click', function(){ set(true); });
  });
  document.querySelectorAll('[data-nav-close]').forEach(function(el){
    el.addEventListener('click', function(){ set(false); });
  });
  // the shop is a separate app on its own port -- resolve it from whatever
  // host this page is being viewed on rather than hardcoding a hostname
  document.querySelectorAll('[data-shop-link]').forEach(function(el){
    el.href = location.protocol + '//' + location.hostname + ':8444/';
  });

  // -- active page --------------------------------------------------
  // Longest alias match wins, so /collectibles/review highlights Review
  // rather than the broader Inventory family. Done here rather than server-side because
  // page() has no request to read a path from, and threading one through
  // all fifty call sites to light up one link is a bad trade.
  (function(){
    var here = location.pathname.replace(/\/+$/, '') || '/';
    var best = null, bestLen = -1;
    document.querySelectorAll('[data-nav-href]').forEach(function(a){
      var prefixes = (a.getAttribute('data-nav-prefixes') || a.getAttribute('data-nav-href')).split('|');
      prefixes.forEach(function(raw){
        var prefix = raw.replace(/\/+$/, '') || '/';
        var hit = here === prefix || here.indexOf(prefix + '/') === 0;
        if (hit && prefix.length > bestLen) { best = a; bestLen = prefix.length; }
      });
    });
    if (best) {
      best.classList.add('is-active');
      best.setAttribute('aria-current', 'page');
      var fold = best.closest('details.navgroup-fold');
      if (fold) fold.open = true;
    }
  })();

  // -- queue counts -------------------------------------------------
  // Fetched once, the first time the drawer is opened. The drawer starts
  // closed, so a page load that never opens it never pays for these.
  var countsLoaded = false;
  function loadCounts(){
    if (countsLoaded) return;
    countsLoaded = true;
    fetch('/nav/counts', { credentials: 'same-origin' })
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(data){
        if (!data || !data.counts) return;
        Object.keys(data.counts).forEach(function(href){
          var n = data.counts[href];
          // null means the queue's cache hasn't been built yet -- that is
          // not the same as zero, so show nothing rather than a wrong 0.
          if (n === null || n === undefined || n === 0) return;
          document.querySelectorAll("[data-count-for='" + href + "']").forEach(function(el){
            el.textContent = n > 99 ? '99+' : String(n);
            el.hidden = false;
          });
        });
      })
      .catch(function(){});
  }

  // -- jump-to palette ----------------------------------------------
  var pal = document.querySelector('[data-palette]');
  var palInput = document.querySelector('[data-palette-input]');
  var palList = document.querySelector('[data-palette-list]');
  var palEmpty = document.querySelector('[data-palette-empty]');
  var palItems = pal ? Array.prototype.slice.call(pal.querySelectorAll('.pal-item')) : [];
  var palIndex = 0;

  function palVisible(){ return palItems.filter(function(el){ return !el.hidden; }); }
  function palMark(i){
    var vis = palVisible();
    if (!vis.length) return;
    palIndex = Math.max(0, Math.min(i, vis.length - 1));
    vis.forEach(function(el){ el.classList.remove('active'); });
    vis[palIndex].classList.add('active');
    vis[palIndex].scrollIntoView({ block: 'nearest' });
  }
  function palFilter(){
    var q = palInput.value.trim().toLowerCase();
    var terms = q ? q.split(/\s+/) : [];
    var shown = 0;
    palItems.forEach(function(el){
      var hay = el.getAttribute('data-hay');
      var ok = terms.every(function(t){ return hay.indexOf(t) !== -1; });
      el.hidden = !ok;
      if (ok) shown++;
    });
    palEmpty.hidden = shown > 0;
    palList.scrollTop = 0;
    palMark(0);
  }
  function palOpen(){
    if (!pal) return;
    set(false);
    pal.hidden = false;
    palInput.value = '';
    palFilter();
    palInput.focus();
  }
  function palClose(){ if (pal) pal.hidden = true; }

  if (pal) {
    document.querySelectorAll('[data-palette-open]').forEach(function(el){
      el.addEventListener('click', palOpen);
    });
    document.querySelectorAll('[data-palette-close]').forEach(function(el){
      el.addEventListener('click', palClose);
    });
    palInput.addEventListener('input', palFilter);
    palItems.forEach(function(el){
      el.addEventListener('mousedown', function(e){
        e.preventDefault();
        nav(el.getAttribute('data-href'));
      });
    });
    palInput.addEventListener('keydown', function(e){
      if (e.key === 'ArrowDown') { e.preventDefault(); palMark(palIndex + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); palMark(palIndex - 1); }
      else if (e.key === 'Enter') {
        var vis = palVisible();
        if (vis[palIndex]) { e.preventDefault(); nav(vis[palIndex].getAttribute('data-href')); }
      }
    });
  }

  document.addEventListener('keydown', function(e){
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      pal && pal.hidden ? palOpen() : palClose();
      return;
    }
    if (e.key === 'Escape') { palClose(); set(false); }
  });
})();
</script>"""


_TOPBAR_NAV_LINKS = [
    ("/ebay-sales", "Pack Assistant"),
    ("/dashboard", "Dashboard"),
    ("/inventory", "Inventory"),
    ("/review-centre", "Review"),
]


def _topbar_html(search_value: str = "") -> str:
    safe_value = search_value.replace('"', "&quot;")
    nav = "".join(f"<a href='{href}'>{label}</a>" for href, label in _TOPBAR_NAV_LINKS)
    return (
        "<header class='topbar'>"
        "<button class='navtoggle' data-nav-open aria-expanded='false' aria-label='Open navigation'>"
        "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round'>"
        "<path d='M3 6h18M3 12h18M3 18h18'/></svg></button>"
        "<a class='topbar-brand' href='/dashboard'><span class='mark'>Ez</span>Bay</a>"
        "<form class='topbar-search' method='get' action='/search'>"
        "<div class='autocomplete-wrap'>"
        f"<input id='global-search-input' name='q' value='{safe_value}' placeholder='Search cards, sets, characters…' autocomplete='off'>"
        "<div id='global-search-results' class='autocomplete-dropdown' hidden></div>"
        "</div></form>"
        "<button class='topbar-refresh' type='button' data-page-refresh aria-label='Refresh page' title='Refresh page'>"
        "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
        "<path d='M20 11a8.1 8.1 0 1 0 2.2 5.5'/><path d='M20 4v7h-7'/></svg></button>"
        f"<nav class='topbar-nav'>{nav}</nav>"
        "</header>"
    )


# Vanilla JS, no framework -- matches this project's convention throughout.
# Fixed element ids since the topbar search box only ever appears once per
# page. DOM nodes are built with textContent, never innerHTML with
# interpolated data, so a card name can't be mistaken for markup regardless
# of where it originated (pokemontcg.io import, a CSV import, or free text).
AUTOCOMPLETE_SCRIPT = """<script>
(function() {
  var input = document.getElementById('global-search-input');
  var box = document.getElementById('global-search-results');
  if (!input || !box) return;
  function navTo(href){
    if (window.EzBayProgress) window.EzBayProgress.go(href); else window.location.href = href;
  }
  var timer = null;
  var items = [];
  var activeIndex = -1;

  function clearBox() {
    box.innerHTML = '';
    box.hidden = true;
    items = [];
    activeIndex = -1;
  }

  function setActive(index) {
    var rows = box.querySelectorAll('.ac-item');
    rows.forEach(function(row) { row.classList.remove('active'); });
    if (index >= 0 && index < rows.length) {
      rows[index].classList.add('active');
      rows[index].scrollIntoView({ block: 'nearest' });
    }
    activeIndex = index;
  }

  function render(results) {
    items = results;
    activeIndex = -1;
    box.innerHTML = '';
    if (!results.length) { box.hidden = true; return; }
    results.forEach(function(r, i) {
      var row = document.createElement('div');
      row.className = 'ac-item';
      row.dataset.index = String(i);

      var name = document.createElement('span');
      name.className = 'ac-name';
      name.textContent = r.name;
      row.appendChild(name);

      var detail = document.createElement('span');
      detail.className = 'ac-detail';
      detail.textContent = r.detail;
      row.appendChild(detail);

      if (r.owned) {
        var owned = document.createElement('span');
        owned.className = 'ac-owned';
        owned.textContent = 'own ' + r.owned;
        row.appendChild(owned);
      }

      row.addEventListener('mousedown', function(e) {
        e.preventDefault();
        navTo('/prices/' + encodeURIComponent(r.id));
      });
      box.appendChild(row);
    });
    box.hidden = false;
  }

  input.addEventListener('input', function() {
    var q = input.value.trim();
    clearTimeout(timer);
    if (q.length < 2) { clearBox(); return; }
    timer = setTimeout(function() {
      fetch('/search/suggest?q=' + encodeURIComponent(q))
        .then(function(r) { return r.json(); })
        .then(function(data) { render(data.results || []); })
        .catch(function() { clearBox(); });
    }, 180);
  });

  input.addEventListener('keydown', function(e) {
    if (box.hidden) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive(Math.min(activeIndex + 1, items.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive(Math.max(activeIndex - 1, 0));
    } else if (e.key === 'Enter') {
      if (activeIndex >= 0 && items[activeIndex]) {
        e.preventDefault();
        navTo('/prices/' + encodeURIComponent(items[activeIndex].id));
      }
    } else if (e.key === 'Escape') {
      clearBox();
    }
  });

  document.addEventListener('click', function(e) {
    if (e.target !== input) clearBox();
  });
})();
</script>"""


# Shared photo field: camera-first, with the file picker as the other
# half of the same control.
#
# Every capture form in this app was a bare `<input type=file
# accept="image/*">`. On a phone that opens a chooser -- Photo Library /
# Take Photo / Browse -- so photographing a card found while out is three
# taps and a menu, and the menu's default is the library, which is the
# wrong one for capture. `capture="environment"` opens the rear camera
# directly, but setting it on the input permanently would remove the
# library path, which is the one wanted at the desk.
#
# So: one input (one form field, nothing to reconcile server-side), two
# buttons that set or clear `capture` immediately before opening it. On
# desktop, where `capture` is ignored, both buttons simply open the
# picker and the pair reads as one redundant control rather than a broken
# one. The preview is there because a blurry card is only worth
# discovering before the upload and the AI identification, not after.
PHOTO_FIELD_STYLE = """<style>
/* position:relative so the visually-hidden input below stays inside the
   field: it's absolutely positioned, and without an ancestor to anchor to
   it escapes to the viewport, taking the browser's "please fill this in"
   bubble with it. Hidden with opacity rather than display:none on
   purpose -- Chrome refuses to submit a form containing a `required`
   control it considers unfocusable, and display:none makes it one. */
.photo-field{display:flex;flex-direction:column;gap:9px;margin-bottom:16px;position:relative}
.photo-field > .pf-label{font-size:13px;color:var(--text-dim);font-weight:600}
.photo-field .pf-hint{font-size:12px;color:var(--text-faint);font-weight:400}
.photo-field input[type=file]{position:absolute;left:0;bottom:0;width:1px;height:1px;
  opacity:0;pointer-events:none}
.pf-buttons{display:flex;gap:8px;flex-wrap:wrap}
.pf-btn{display:inline-flex;align-items:center;gap:7px;background:var(--panel);
  border:1px solid var(--panel-border);border-radius:9px;padding:11px 16px;font-size:13.5px;
  font-weight:650;color:var(--text);cursor:pointer}
.pf-btn:hover{border-color:var(--accent);color:var(--accent)}
.pf-btn svg{width:16px;height:16px;flex:none}
.pf-btn:active{background:var(--surface-sunken)}
.pf-preview{display:none;align-items:center;gap:12px;background:var(--surface-sunken);
  border:1px solid var(--panel-border);border-radius:10px;padding:10px 12px}
.pf-preview.on{display:flex}
.pf-preview img{width:52px;height:52px;object-fit:cover;border-radius:7px;flex:none;
  background:var(--panel-border)}
.pf-name{font-size:12.5px;color:var(--text-dim);word-break:break-all;flex:1 1 auto;min-width:0}
.pf-clear{background:none;border:none;color:var(--text-faint);font-size:19px;line-height:1;
  cursor:pointer;padding:0 4px;flex:none;font-weight:400}
.pf-clear:hover{color:var(--danger)}
</style>"""

_CAMERA_SVG = (
    "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' "
    "stroke-linecap='round' stroke-linejoin='round'>"
    "<path d='M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z'/>"
    "<circle cx='12' cy='13' r='4'/></svg>"
)
_ALBUM_SVG = (
    "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' "
    "stroke-linecap='round' stroke-linejoin='round'>"
    "<rect x='3' y='3' width='14' height='14' rx='2'/>"
    "<circle cx='7.5' cy='7.5' r='1.3'/><path d='M17 12.5 13 9l-6 6'/>"
    "<path d='M21 7v12a2 2 0 0 1-2 2H7'/></svg>"
)


def photo_field(
    name: str,
    label: str,
    *,
    required: bool = False,
    multiple: bool = False,
    hint: str = "",
) -> str:
    """One photo input with both a camera and a file-picker path."""
    parts = [hint] if hint else []
    if multiple:
        # says what the camera button actually does here, which is not
        # obvious: each shot adds to the batch instead of replacing it
        parts.append("add as many as you like, one shot at a time")
    hint_html = f" <span class='pf-hint'>{escape(' \u00b7 '.join(parts))}</span>" if parts else ""
    return (
        "<div class='photo-field' data-photo-field>"
        f"<span class='pf-label'>{escape(label)}{hint_html}</span>"
        f"<input type='file' name='{escape(name, quote=True)}' accept='image/*'"
        f"{' required' if required else ''}{' multiple' if multiple else ''} data-pf-input>"
        "<div class='pf-buttons'>"
        f"<button type='button' class='pf-btn' data-pf-camera>{_CAMERA_SVG}"
        "<span>Take photo</span></button>"
        f"<button type='button' class='pf-btn' data-pf-browse>{_ALBUM_SVG}"
        "<span>From album</span></button>"
        "</div>"
        "<div class='pf-preview' data-pf-preview>"
        "<img alt='' data-pf-thumb>"
        "<span class='pf-name' data-pf-name></span>"
        "<button type='button' class='pf-clear' data-pf-clear aria-label='Remove photo'>&times;</button>"
        "</div></div>"
    )


PHOTO_FIELD_SCRIPT = """<script>
(function(){
  document.querySelectorAll('[data-photo-field]').forEach(function(field){
    var input   = field.querySelector('[data-pf-input]');
    var preview = field.querySelector('[data-pf-preview]');
    var thumb   = field.querySelector('[data-pf-thumb]');
    var nameEl  = field.querySelector('[data-pf-name]');
    if (!input) return;
    var multiple = input.multiple;
    var url = null;
    var picked = [];   // the running batch, for multi-shot fields

    function open(useCamera){
      // set immediately before opening: the attribute is only read when
      // the picker is invoked, so one input serves both paths
      if (useCamera) { input.setAttribute('capture', 'environment'); }
      else { input.removeAttribute('capture'); }
      input.click();
    }
    field.querySelector('[data-pf-camera]').addEventListener('click', function(){ open(true); });
    field.querySelector('[data-pf-browse]').addEventListener('click', function(){ open(false); });

    function render(){
      if (url) { URL.revokeObjectURL(url); url = null; }
      if (!picked.length) {
        preview.classList.remove('on');
        thumb.removeAttribute('src');
        return;
      }
      nameEl.textContent = picked.length > 1
        ? picked.length + ' photos ready'
        : picked[0].name;
      url = URL.createObjectURL(picked[picked.length - 1]);
      thumb.src = url;
      preview.classList.add('on');
    }

    field.querySelector('[data-pf-clear]').addEventListener('click', function(){
      picked = [];
      input.value = '';
      render();
    });

    input.addEventListener('change', function(){
      var incoming = Array.prototype.slice.call(input.files || []);
      if (!incoming.length) return;

      if (!multiple) { picked = incoming; render(); return; }

      // A camera capture returns exactly one photo and REPLACES whatever
      // the input held, so on a bulk form ten shots would upload as one.
      // Accumulate into a DataTransfer and write the batch back, which is
      // what makes photographing a pile away from the desk actually work.
      picked = picked.concat(incoming);
      try {
        var dt = new DataTransfer();
        picked.forEach(function(f){ dt.items.add(f); });
        input.files = dt.files;
      } catch (e) {
        // No DataTransfer: fall back to whatever the last pick was, so
        // the form still submits something rather than failing silently.
        picked = incoming;
      }
      render();
    });
  });
})();
</script>"""


# Shared "identify from a photo" widget -- buying calculator, purchase
# lots, potential stock, and Whatnot's item queue (2026-09-07) all embed
# one of these next to their own form. Each instance declares which of
# that page's own form fields to fill via data attributes, so this is one
# script wired generically rather than four bespoke ones; the confirm step
# the user actually wants ("select and see the value before it's used") is
# the click on a candidate row -- nothing is filled without that click, and
# the page's own submit button still has to be pressed after for anything
# to be saved.
QUICK_PRICE_STYLE = """<style>
.quick-price{background:var(--surface-sunken);border:1px dashed var(--panel-border);border-radius:10px;
  padding:12px;margin-bottom:16px;display:flex;flex-direction:column;gap:8px}
.qp-label{font-size:12px;color:var(--text-dim);display:flex;flex-direction:column;gap:5px}
.qp-btn{align-self:flex-start;background:transparent;border:1px solid var(--panel-border);color:var(--text-dim);
  padding:8px 14px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer}
.qp-btn:hover{border-color:var(--accent);color:var(--accent)}
.qp-status{font-size:12px;color:var(--text-dim)}
.qp-results{display:flex;flex-direction:column;gap:6px}
.qp-candidate{display:flex;justify-content:space-between;align-items:center;gap:10px;background:var(--panel);
  border:1px solid var(--panel-border);border-radius:8px;padding:8px 12px;cursor:pointer;font-size:13px}
.qp-candidate:hover{border-color:var(--accent)}
.qp-candidate .qp-price{font-weight:700;color:var(--success);flex:none}
.qp-candidate.qp-unmatched .qp-price{color:var(--text-faint);font-weight:600}
</style>"""

QUICK_PRICE_SCRIPT = """<script>
(function() {
  document.querySelectorAll('.quick-price').forEach(function(widget) {
    var fileInput = widget.querySelector('.qp-file');
    var btn = widget.querySelector('.qp-btn');
    var status = widget.querySelector('.qp-status');
    var results = widget.querySelector('.qp-results');
    var nameField = widget.dataset.nameField;
    var priceField = widget.dataset.priceField;

    btn.addEventListener('click', function() {
      if (!fileInput.files.length) { status.textContent = 'Choose a photo first.'; return; }
      status.textContent = 'Identifying…';
      var failed = false;
      if (window.EzBayProgress) window.EzBayProgress.begin('POST /quick-price/identify');
      results.innerHTML = '';
      var data = new FormData();
      data.append('photo', fileInput.files[0]);
      fetch('/quick-price/identify', { method: 'POST', body: data })
        .then(function(r) {
          if (!r.ok) throw new Error('request failed');
          return r.json();
        })
        .then(function(payload) {
          var items = payload.results || [];
          if (!items.length) { status.textContent = 'No cards recognized in that photo.'; return; }
          status.textContent = items.length + ' card(s) found -- select one:';
          items.forEach(function(item) {
            var row = document.createElement('div');
            row.className = 'qp-candidate' + (item.matched ? '' : ' qp-unmatched');

            var name = document.createElement('span');
            name.textContent = item.character + (item.set_name ? ' (' + item.set_name + ')' : '');
            row.appendChild(name);

            var price = document.createElement('span');
            price.className = 'qp-price';
            price.textContent = item.market_value != null ? '$' + item.market_value.toFixed(2) : 'no price data';
            row.appendChild(price);

            row.addEventListener('click', function() {
              if (nameField) {
                var nameInput = document.querySelector('[name="' + nameField + '"]');
                if (nameInput) nameInput.value = item.character;
              }
              if (priceField && item.market_value != null) {
                var priceInput = document.querySelector('[name="' + priceField + '"]');
                if (priceInput) priceInput.value = item.market_value.toFixed(2);
              }
              status.textContent = 'Filled from ' + item.character + '. Review and submit the form below.';
              results.innerHTML = '';
            });
            results.appendChild(row);
          });
        })
        .catch(function() { failed = true; status.textContent = 'Identification failed -- try again or enter manually.'; })
        // Only a successful identify tells us anything about how long one
        // takes; a fast failure would drag the estimate down.
        .then(function(){ if (window.EzBayProgress) window.EzBayProgress.done(failed ? null : 'POST /quick-price/identify'); });
    });
  });
})();
</script>"""


def quick_price_widget(price_field: str, name_field: str | None = None, label: str = "Identify from a photo (optional)") -> str:
    name_attr = f" data-name-field='{name_field}'" if name_field else ""
    return (
        f"<div class='quick-price'{name_attr} data-price-field='{price_field}'>"
        f"<label class='qp-label'>{label}<input type='file' accept='image/*' class='qp-file'></label>"
        "<button type='button' class='qp-btn'>Identify &amp; price</button>"
        "<div class='qp-status'></div>"
        "<div class='qp-results'></div>"
        "</div>"
    )


# Installable-to-home-screen basics (Module 18). The resilience worker is
# intentionally network-only: it must never cache live inventory/pricing, but
# it can still replace iOS's otherwise blank page while a navigation retries.
# Apple-specific metadata enables the standalone home-screen presentation;
# the manifest covers Android/desktop installability as well.
# --- navigation progress bar -------------------------------------------
#
# Most pages here answer in single-digit milliseconds, but a handful don't
# -- /dashboard, /review and /inventory each run real aggregate queries
# over a 23k-row catalogue, and the AI-identify endpoints take seconds --
# and until now a click on one of those produced nothing at all: the old
# page just sat there, indistinguishable from a click that missed.
#
# The bar that fixes that has to live in the *outgoing* document, because
# a full-page navigation only replaces it once the new HTML has arrived.
# So the span it can honestly cover is click -> last byte of the response,
# and that is exactly what gets measured and predicted below.
#
# Predicted, not faked. A bar that creeps on a fixed curve answers "is it
# working" but not "how long is left", which is the actual question. Each
# arrival records how long that destination really took (Navigation
# Timing, blended into a running average in localStorage) and the next
# trip to the same destination animates against that number, with the
# remaining seconds shown once the expectation is over a second. First
# visit to an unseen destination falls back to a default and the bar just
# eases along until the page lands.
PROGRESS_STYLE = """<style>
.navprogress { position: fixed; top: 0; left: 0; right: 0; height: 3px; z-index: 100; pointer-events: none; }
.navprogress[hidden] { display: none; }
.navprogress .npfill { height: 100%; width: 0; border-radius: 0 3px 3px 0;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  box-shadow: 0 0 8px rgba(var(--accent-rgb), 0.55); }
.navprogress.is-done .npfill { transition: width .18s ease, opacity .25s ease .12s; opacity: 0; }
.npremain { position: fixed; top: 10px; right: 12px; z-index: 100; pointer-events: none;
  background: var(--panel); border: 1px solid var(--panel-border); border-radius: 999px;
  padding: 2px 10px; font-size: 11px; font-weight: 700; color: var(--text-dim);
  box-shadow: var(--shadow); font-variant-numeric: tabular-nums; }
.npremain[hidden] { display: none; }
@media (prefers-reduced-motion: reduce) { .navprogress .npfill { box-shadow: none; } }
</style>"""

PROGRESS_HTML = (
    "<div class='navprogress' data-progress hidden role='progressbar' aria-label='Loading'"
    " aria-valuemin='0' aria-valuemax='100' aria-valuenow='0'>"
    "<div class='npfill' data-progress-fill></div></div>"
    "<div class='npremain' data-progress-label hidden></div>"
)

CSRF_SCRIPT = r"""<script>
(function(){
  // Double-submit-cookie CSRF (added 2026-09-29, see auth.csrf_token_for):
  // attaches the token to every form post and every fetch()-based POST
  // automatically, so no individual page or template needs to know this
  // exists. Independent of the progress bar below -- must not be gated
  // on any element existing in the DOM.
  function csrfToken(){
    var m = document.cookie.match(/(?:^|; )rbay_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  document.addEventListener('submit', function(e){
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if ((form.getAttribute('method') || 'get').toLowerCase() !== 'post') return;
    if (form.querySelector("input[name='_csrf']")) return;
    var token = csrfToken();
    if (!token) return;
    var input = document.createElement('input');
    input.type = 'hidden'; input.name = '_csrf'; input.value = token;
    form.appendChild(input);
  }, true);  // capture phase: runs before any page's own submit handler
             // (e.g. a custom fetch()-based upload flow) reads form data

  var realFetch = window.fetch;
  window.fetch = function(input, init){
    try {
      if (init && typeof init.method === 'string' && init.method.toUpperCase() === 'POST'
          && typeof FormData !== 'undefined' && init.body instanceof FormData && !init.body.has('_csrf')) {
        var token = csrfToken();
        if (token) init.body.append('_csrf', token);
      }
    } catch (e) {}
    return realFetch.call(this, input, init);
  };
})();
</script>"""

PROGRESS_SCRIPT = r"""<script>
(function(){
  var bar = document.querySelector('[data-progress]');
  var fill = bar && bar.querySelector('[data-progress-fill]');
  var label = document.querySelector('[data-progress-label]');
  if (!bar || !fill) return;

  var STORE = 'ezbay:navtimes';   // destination -> typical milliseconds
  var PENDING = 'ezbay:navpending';
  var FALLBACK = 900;             // never been here before
  var MAX_KEYS = 200;
  var SHOW_DELAY = 180;           // below this, the page beats the bar anyway
  var GIVE_UP = 45000;

  function read(){ try { return JSON.parse(localStorage.getItem(STORE)) || {}; } catch(e){ return {}; } }
  function write(m){ try { localStorage.setItem(STORE, JSON.stringify(m)); } catch(e){} }

  function expected(key){
    var v = read()[key];
    return (typeof v === 'number' && v > 0) ? v : FALLBACK;
  }

  function record(key, ms){
    if (!(ms > 0) || ms > 120000) return;
    var m = read(), keys = Object.keys(m);
    if (keys.length >= MAX_KEYS && !(key in m)) delete m[keys[0]];
    // Exponential moving average: one freak slow load shouldn't set the
    // expectation for every load after it, and a table that grows (or a
    // machine that gets faster) should be followed rather than remembered.
    m[key] = (key in m) ? Math.round(m[key] * 0.7 + ms * 0.3) : Math.round(ms);
    write(m);
  }

  // -- measure this arrival -----------------------------------------
  // Attributed to whatever key the *previous* page stashed on its way
  // out, not to the URL we landed on: a POST that 303s somewhere else
  // spends its time under the POST, and that's the wait to predict next
  // time that button is pressed.
  (function(){
    var stashed = null;
    try { stashed = sessionStorage.getItem(PENDING); sessionStorage.removeItem(PENDING); } catch(e){}
    var nav = performance.getEntriesByType && performance.getEntriesByType('navigation')[0];
    if (!nav || nav.type === 'back_forward') return;
    record(stashed || ('GET ' + location.pathname), nav.responseEnd - nav.startTime);
  })();

  // -- the bar ------------------------------------------------------
  var delayTimer = null, raf = null, startedAt = 0, target = 0, running = false;

  function paint(){
    var t = performance.now() - startedAt, pct, remain;
    if (t < target) {
      pct = 92 * (t / target);
      remain = target - t;
    } else {
      // Overrunning the estimate. Ease toward 99 rather than sitting at
      // 92 looking stuck, and stop claiming to know what's left.
      pct = 92 + 7 * (1 - Math.exp(-(t - target) / target));
      remain = null;
    }
    fill.style.width = pct.toFixed(1) + '%';
    bar.setAttribute('aria-valuenow', String(Math.round(pct)));
    if (label) {
      if (target < 1000) {
        label.hidden = true;                       // too short to be worth a number
      } else {
        label.textContent = remain === null ? 'almost there…' : (remain / 1000).toFixed(1) + 's left';
        label.hidden = false;
      }
    }
    if (t > GIVE_UP) { reset(); return; }
    raf = requestAnimationFrame(paint);
  }

  function reset(){
    running = false;
    clearTimeout(delayTimer); delayTimer = null;
    if (raf) { cancelAnimationFrame(raf); raf = null; }
    bar.hidden = true;
    bar.classList.remove('is-done');
    fill.style.width = '0';
    if (label) label.hidden = true;
  }

  function begin(key, expectedMs){
    reset();
    running = true;
    target = expectedMs || expected(key);
    startedAt = performance.now();
    if (key) { try { sessionStorage.setItem(PENDING, key); } catch(e){} }
    delayTimer = setTimeout(function(){ bar.hidden = false; paint(); }, SHOW_DELAY);
  }

  // For work that finishes without replacing the document (the fetch-based
  // identify/deal-check flows). A navigation never calls this -- its own
  // document is gone before it could.
  function done(key){
    if (!running) return;
    if (key) record(key, performance.now() - startedAt);
    running = false;
    clearTimeout(delayTimer); delayTimer = null;
    if (raf) { cancelAnimationFrame(raf); raf = null; }
    if (label) label.hidden = true;
    if (bar.hidden) { reset(); return; }
    bar.classList.add('is-done');
    fill.style.width = '100%';
    setTimeout(reset, 420);
  }

  // -- what counts as a wait ----------------------------------------
  // A path with a file extension is a download: the browser saves it and
  // leaves this document in place, so there is no navigation to track and
  // a bar started for one would hang until it gave up.
  function isDownload(pathname){ return /\.[a-z0-9]{2,5}$/i.test(pathname); }

  document.addEventListener('click', function(e){
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || a.hasAttribute('download') || a.hasAttribute('data-no-progress')) return;
    if (a.target && a.target !== '_self') return;
    var raw = a.getAttribute('href') || '';
    if (!raw || raw.charAt(0) === '#' || /^[a-z]+:/i.test(raw) && !/^https?:/i.test(raw)) return;
    var url;
    try { url = new URL(a.href, location.href); } catch(err){ return; }
    if (url.origin !== location.origin || isDownload(url.pathname)) return;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
    begin('GET ' + url.pathname);
  });

  document.addEventListener('submit', function(e){
    var f = e.target;
    if (e.defaultPrevented || !f || f.tagName !== 'FORM' || f.hasAttribute('data-no-progress')) return;
    if (f.target && f.target !== '_self') return;
    var url;
    try { url = new URL(f.getAttribute('action') || location.href, location.href); } catch(err){ return; }
    if (url.origin !== location.origin || isDownload(url.pathname)) return;
    begin((f.method || 'get').toUpperCase() + ' ' + url.pathname);
  });

  // Coming back via the back button restores this document from cache
  // mid-animation if the bar happened to be running when it was left.
  window.addEventListener('pageshow', function(e){ if (e.persisted) reset(); });

  window.EzBayProgress = {
    begin: begin,
    done: done,
    // Scripted navigations (the Ctrl-K palette, the search autocomplete)
    // don't fire a click on an <a>, so they hand the destination over here.
    go: function(href){
      var url;
      try { url = new URL(href, location.href); } catch(err){ location.href = href; return; }
      if (url.origin === location.origin && !isDownload(url.pathname)) begin('GET ' + url.pathname);
      location.href = href;
    }
  };
})();
</script>"""


NAV_RETURN_STYLE = """<style>
.nav-toast{position:fixed;left:50%;bottom:calc(20px + env(safe-area-inset-bottom));transform:translate(-50%,20px);
  background:#12161c;color:#fff;padding:10px 18px;border-radius:999px;font-size:14px;font-weight:600;
  box-shadow:0 6px 24px rgba(0,0,0,.25);opacity:0;transition:opacity .2s,transform .2s;z-index:9999;pointer-events:none}
.nav-toast.show{opacity:1;transform:translate(-50%,0)}
</style>"""


NAV_RETURN_SCRIPT = r"""<script>
(function(){
  // Return-to-where-I-was navigation (see automation_control/navigation.py).
  // The CRM is used as a home-screen app with no browser back button, so:
  // - every POST form's action gets ?_return_to=<this page>&_return_prev=
  //   <previous page>, which navigation.redirect_back() uses after a save;
  // - a per-tab trail of visited pages drives the "Back" header link;
  // - scroll position is remembered per page and restored on return;
  // - ?_saved=<text> on arrival shows a brief toast, then is removed from
  //   the address so a refresh doesn't show it again.
  var TRAIL = 'ezbay-trail', SCROLL = 'ezbay-scroll';
  // One-off messages a page shows after redirecting to itself. Ignoring
  // them keeps "?notice=Photo+removed" from counting as a new page, which
  // would turn the Back link into a reload of the same page.
  var MESSAGE_PARAMS = ['_saved', 'notice', 'error', 'refreshed', 'checking'];
  function load(key, fallback){
    try { return JSON.parse(sessionStorage.getItem(key)) || fallback; } catch(e){ return fallback; }
  }
  function save(key, value){ try { sessionStorage.setItem(key, JSON.stringify(value)); } catch(e){} }
  function here(){
    var u = new URL(location.href);
    MESSAGE_PARAMS.forEach(function(p){ u.searchParams.delete(p); });
    return u.pathname + u.search;
  }
  function rememberScroll(){
    var s = load(SCROLL, {});
    s[here()] = Math.round(window.scrollY);
    var keys = Object.keys(s);
    if (keys.length > 60) delete s[keys[0]];
    save(SCROLL, s);
  }

  var cur = here(), trail = load(TRAIL, []), returning = false;
  if (location.pathname !== '/login') {
    if (trail[trail.length - 1] === cur) {
      returning = true;                       // refresh, or a save that stayed put
    } else if (trail[trail.length - 2] === cur) {
      trail.pop(); returning = true;          // went back one page
    } else {
      trail.push(cur);
      if (trail.length > 30) trail.shift();
    }
    save(TRAIL, trail);
  }
  var prev = trail.length > 1 ? trail[trail.length - 2] : '';

  function withReturn(action){
    var u = new URL(action || location.href, location.href);
    if (u.origin !== location.origin) return null;
    u.searchParams.set('_return_to', cur);
    if (prev) u.searchParams.set('_return_prev', prev); else u.searchParams.delete('_return_prev');
    return u.pathname + u.search;
  }

  document.addEventListener('submit', function(e){
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if ((form.getAttribute('method') || 'get').toLowerCase() !== 'post') return;
    var action = withReturn(form.getAttribute('action'));
    if (action) form.setAttribute('action', action);
    rememberScroll();
  }, true);

  var realFetch = window.fetch;
  window.fetch = function(input, init){
    try {
      if (typeof input === 'string' && init && typeof init.method === 'string'
          && init.method.toUpperCase() === 'POST') {
        var withRet = withReturn(input);
        if (withRet) { input = withRet; rememberScroll(); }
      }
    } catch(e) {}
    return realFetch.call(this, input, init);
  };

  window.addEventListener('pagehide', rememberScroll);

  function arrive(){
    var u = new URL(location.href), saved = u.searchParams.get('_saved');
    if (saved !== null) {
      u.searchParams.delete('_saved');
      history.replaceState(history.state, '', u.pathname + u.search + u.hash);
      var t = document.createElement('div');
      t.className = 'nav-toast'; t.setAttribute('role', 'status'); t.textContent = saved || 'Saved';
      document.body.appendChild(t);
      requestAnimationFrame(function(){ t.classList.add('show'); });
      setTimeout(function(){ t.classList.remove('show'); setTimeout(function(){ t.remove(); }, 300); }, 2200);
    }
    if ((returning || saved !== null) && !location.hash) {
      var y = load(SCROLL, {})[cur];
      if (y) window.scrollTo(0, y);
    }
    if (prev) {
      document.querySelectorAll('a.back-link[data-nav-back]').forEach(function(a){
        a.setAttribute('href', prev);
        a.textContent = '← Back';
      });
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arrive);
  else arrive();
})();
</script>"""


_PWA_HEAD = (
    "<link rel='manifest' href='/static/manifest.json'>"
    "<meta name='theme-color' content='#0891b2'>"
    "<meta name='apple-mobile-web-app-capable' content='yes'>"
    "<meta name='apple-mobile-web-app-status-bar-style' content='default'>"
    "<meta name='apple-mobile-web-app-title' content='EzBay'>"
    "<link rel='apple-touch-icon' href='/static/icons/apple-touch-icon.png'>"
)


PWA_LIFECYCLE_SCRIPT = r"""<script>
(function(){
  // The worker is deliberately network-only. It never caches CRM pages or
  // inventory data; its sole job is to replace iOS's empty standalone-app
  // screen with a reconnecting page if a navigation overlaps a deploy or a
  // brief network interruption.
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function(){
      navigator.serviceWorker.register('/sw.js', {scope: '/'}).catch(function(){});
    });
  }

  var hiddenAt = 0, probing = false;
  function forceRepaint(){
    // iOS can restore the web view but fail to repaint its previous layer.
    // Touching a composited property forces a fresh frame without discarding
    // anything the user has typed into a form.
    document.documentElement.style.transform = 'translateZ(0)';
    void document.documentElement.offsetHeight;
    requestAnimationFrame(function(){ document.documentElement.style.transform = ''; });
  }
  function probe(){
    if (probing) return;
    probing = true;
    forceRepaint();
    fetch('/health?resume=' + Date.now(), {cache: 'no-store', credentials: 'same-origin'})
      .catch(function(){})
      .then(function(){ probing = false; });
  }
  document.addEventListener('visibilitychange', function(){
    if (document.visibilityState === 'hidden') { hiddenAt = Date.now(); return; }
    if (!hiddenAt || Date.now() - hiddenAt > 1000) probe();
  });
  window.addEventListener('pageshow', function(e){
    if (e.persisted) probe(); else forceRepaint();
  });
})();
</script>"""


def page(title: str, body: str, head_extra: str = "", show_nav: bool = True, search_value: str = "") -> str:
    chrome = (_sidenav_html() + _topbar_html(search_value) if show_nav else "")
    script = (AUTOCOMPLETE_SCRIPT + QUICK_PRICE_SCRIPT + NAV_SCRIPT + PHOTO_FIELD_SCRIPT if show_nav else "")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title>{STYLE}{TOPBAR_STYLE}{QUICK_PRICE_STYLE}{PHOTO_FIELD_STYLE}"
        f"{PROGRESS_STYLE}{NAV_RETURN_STYLE}{_PWA_HEAD}{head_extra}</head>"
        f"<body>{PROGRESS_HTML}{chrome}<main>{body}</main>{CSRF_SCRIPT}{NAV_RETURN_SCRIPT}{PROGRESS_SCRIPT}"
        f"{PWA_LIFECYCLE_SCRIPT}{script}</body></html>"
    )


def pill(text: str, kind: str = "neutral") -> str:
    return f"<span class='pill {kind}'>{text}</span>"


def brand_header(title: str, show_back: bool = True) -> str:
    # NAV_RETURN_SCRIPT points this at the previous page in the tab's trail
    # (and relabels it "Back"); the dashboard is the no-history fallback.
    back = "<a class='back-link' data-nav-back href='/dashboard'>&larr; Dashboard</a>" if show_back else ""
    return f"{back}<h1 class='page-title'>{title}</h1>"
