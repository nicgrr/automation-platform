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
@media (max-width: 680px) {
  .topbar-nav { display: none; }
  .topbar-search { max-width: none; }
}
</style>"""

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
        f"<label class='qp-label'>{label}<input type='file' accept='image/*' capture='environment' class='qp-file'></label>"
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
    chrome = (_topbar_html(search_value) if show_nav else "")
    script = (AUTOCOMPLETE_SCRIPT + QUICK_PRICE_SCRIPT if show_nav else "")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title>{STYLE}{TOPBAR_STYLE}{QUICK_PRICE_STYLE}{_PWA_HEAD}{head_extra}</head>"
        f"<body>{chrome}<main>{body}</main>{script}</body></html>"
    )


def pill(text: str, kind: str = "neutral") -> str:
    return f"<span class='pill {kind}'>{text}</span>"


def brand_header(title: str, show_back: bool = True) -> str:
    back = "<a class='back-link' href='/dashboard'>&larr; Dashboard</a>" if show_back else ""
    return f"{back}<h1 class='page-title'>{title}</h1>"
