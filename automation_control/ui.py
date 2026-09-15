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

# Grouped by what you're doing, not by which pipeline the code grew out of.
# The old grouping ("Card capture & listings" vs "Bulk scan & catalogue")
# named two code lineages rather than two activities, which is why the same
# task -- review something -- was split across two groups, why the label
# "Review queue" appeared twice pointing at two different queues, and why
# "Business" ended up a thirteen-item drawer holding shop administration,
# a personal collection and a link-identify tool side by side.
#
# Each group is (title, links, collapsed); a link is (href, label).
# `collapsed` puts the group behind a disclosure that's shut by default.
# Those features are built, working and still one click away -- they just
# have no data yet, and ten empty modules competing for attention with the
# six used daily was most of why anything was hard to find. Move a group
# out of `collapsed` the day its table has rows.
ADMIN_NAV = [
    ("Today", [
        ("/dashboard", "Dashboard"),
        ("/analytics", "Analytics"),
        ("/feed", "Scan feed"),
        ("/scan-ingest", "Scanner status & logs"),
        ("/settings", "Settings"),
    ], False),
    ("Review", [
        ("/review", "Scan crops"),
        ("/foil-review", "Foil"),
        ("/inventory-audit", "Photo audit"),
        ("/collectibles/review", "Photographed collectibles"),
        ("/cards/review", "Photographed cards"),
        ("/cards/pricing", "Prices"),
    ], False),
    ("Stock", [
        ("/inventory", "Inventory"),
        ("/search", "Search catalogue"),
        ("/sealed-products", "Sealed products"),
        ("/collectibles", "Collectibles"),
        ("/personal-collection", "Personal collection"),
    ], False),
    # Both ways stock enters the business, together: the flatbed scanner
    # for pile-processing at the desk, the camera for anything found while
    # out. They were previously three groups apart.
    ("Add stock", [
        ("/cards/capture", "Photograph a card"),
        ("/collectibles/capture", "Photograph a collectible"),
        ("/cards/capture/bulk", "Bulk upload cards"),
        ("/collectibles/capture/bulk", "Bulk upload collectibles"),
        ("/collectibles/identify", "Identify from a link"),
    ], False),
    ("Sell & buy", [
        ("/shop-listings", "Shop listings"),
        ("/shop-pricing", "Shop pricing"),
        ("/shop-orders", "Shop orders"),
        ("/contact-messages", "Contact messages"),
        ("/listings/review", "eBay drafts (unpublished)"),
        ("/ebay-listings", "eBay live listings"),
        ("/market-search", "Market search"),
        ("/deal-check", "Deal check"),
        ("/buying-calculator", "Buying calculator"),
        ("/ebay-export", "eBay bulk upload file"),
        ("/export", "Export data"),
    ], False),
    ("Not set up yet", [
        ("/customers", "Customers"),
        ("/suppliers", "Suppliers"),
        ("/goals", "Goals"),
        ("/release-calendar", "Release calendar"),
        ("/purchase-lots", "Purchase lots"),
        ("/potential-stock", "Potential stock"),
        ("/whatnot", "Whatnot shows"),
        ("/sales", "Sales"),
        ("/marketplaces", "Marketplaces"),
    ], True),
]

# Which links get a "work waiting" badge. Kept next to the nav rather than
# inside nav_counts.py so the two halves of the contract -- what the server
# counts, and what the drawer shows -- are visible together.
NAV_COUNT_HREFS = {
    "/review", "/foil-review", "/inventory-audit",
    "/collectibles/review", "/cards/review", "/cards/pricing",
    "/listings/review",
}


def _nav_link(href: str, label: str) -> str:
    badge = (
        f"<span class='navcount' data-count-for='{href}' hidden></span>"
        if href in NAV_COUNT_HREFS else ""
    )
    return (
        f"<a href='{href}' data-nav-href='{href}'>"
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
    """Every module in one drawer. Until now only /dashboard listed them
    all, so any other page was two clicks from everywhere -- back to the
    dashboard, then out again. /dashboard now renders its own quicklinks
    from this same ADMIN_NAV (see dashboard.py) rather than keeping a
    second hand-maintained copy, which is what let the two drift apart:
    photo audit existed only on the dashboard, and eight links existed
    only here."""
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
    for title, links, _collapsed in ADMIN_NAV:
        for href, label in links:
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
  // Longest match wins, so /cards/capture/bulk highlights the bulk link
  // rather than its parent. Done here rather than server-side because
  // page() has no request to read a path from, and threading one through
  // all fifty call sites to light up one link is a bad trade.
  (function(){
    var here = location.pathname.replace(/\/+$/, '') || '/';
    var best = null, bestLen = -1;
    document.querySelectorAll('[data-nav-href]').forEach(function(a){
      var href = a.getAttribute('data-nav-href').replace(/\/+$/, '') || '/';
      var hit = here === href || here.indexOf(href + '/') === 0;
      if (hit && href.length > bestLen) { best = a; bestLen = href.length; }
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
        location.href = el.getAttribute('data-href');
      });
    });
    palInput.addEventListener('keydown', function(e){
      if (e.key === 'ArrowDown') { e.preventDefault(); palMark(palIndex + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); palMark(palIndex - 1); }
      else if (e.key === 'Enter') {
        var vis = palVisible();
        if (vis[palIndex]) { e.preventDefault(); location.href = vis[palIndex].getAttribute('data-href'); }
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
    ("/dashboard", "Dashboard"),
    ("/analytics", "Analytics"),
    ("/inventory", "Inventory"),
    ("/feed", "Scan feed"),
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
        window.location.href = '/prices/' + encodeURIComponent(r.id);
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
        window.location.href = '/prices/' + encodeURIComponent(items[activeIndex].id);
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
        .catch(function() { status.textContent = 'Identification failed -- try again or enter manually.'; });
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


# Installable-to-home-screen basics (Module 18) -- no service worker by
# design: this tool shows live inventory/pricing, and a caching layer
# risks showing stale numbers on exactly the data a phone check exists to
# get right. iOS (the only device this has actually been used from all
# session) reads apple-mobile-web-app-* and apple-touch-icon directly, no
# service worker required for "Add to Home Screen"; the manifest covers
# Android/desktop installability the same way.
_PWA_HEAD = (
    "<link rel='manifest' href='/static/manifest.json'>"
    "<meta name='theme-color' content='#0891b2'>"
    "<meta name='apple-mobile-web-app-capable' content='yes'>"
    "<meta name='apple-mobile-web-app-status-bar-style' content='default'>"
    "<meta name='apple-mobile-web-app-title' content='EzBay'>"
    "<link rel='apple-touch-icon' href='/static/icons/apple-touch-icon.png'>"
)


def page(title: str, body: str, head_extra: str = "", show_nav: bool = True, search_value: str = "") -> str:
    chrome = (_sidenav_html() + _topbar_html(search_value) if show_nav else "")
    script = (AUTOCOMPLETE_SCRIPT + QUICK_PRICE_SCRIPT + NAV_SCRIPT + PHOTO_FIELD_SCRIPT if show_nav else "")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title>{STYLE}{TOPBAR_STYLE}{QUICK_PRICE_STYLE}{PHOTO_FIELD_STYLE}{_PWA_HEAD}{head_extra}</head>"
        f"<body>{chrome}<main>{body}</main>{script}</body></html>"
    )


def pill(text: str, kind: str = "neutral") -> str:
    return f"<span class='pill {kind}'>{text}</span>"


def brand_header(title: str, show_back: bool = True) -> str:
    back = "<a class='back-link' href='/dashboard'>&larr; Dashboard</a>" if show_back else ""
    return f"{back}<h1 class='page-title'>{title}</h1>"
