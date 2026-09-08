"""Page shell for the public shop.

Its own shell rather than automation_control.ui's: the admin tool is a
dense light-grey dashboard, and a shop needs to look like a shop. Same
plain-string-HTML convention though, and no CDN assets beyond two Google
Fonts, so the storefront has no build step either.

Visual language is inspired by Framer's "Nordframe" template (a
monochrome editorial photography-portfolio look): near-black-on-white,
small-caps monospace labels doing the structural work (nav, captions,
footer), a heavy grotesk carrying the few big display moments, and flat
cards with no shadow -- the imagery is what's meant to carry colour and
interest, not the chrome around it. Functional colour (the brass/teal
accents) is kept, but demoted to meaning rather than decoration: a price,
a savings figure, "this is your own photo" -- never a background.
"""

import json
from html import escape

from .brand import BRASS, logo_lockup

STYLE = """<style>
:root{
  --ink:#111111; --paper:#fff; --bone:#fafafa; --sunken:#eeeeec;
  --muted:#6b6b6b; --rule:#e4e4e2;
  --brass:#b3852f; --brass-lit:#8a6423; --teal:#14625f;
  --radius:3px;
}
*{box-sizing:border-box}
body{
  margin:0;background:var(--paper);color:var(--ink);
  font-family:Archivo,ui-sans-serif,system-ui,-apple-system,sans-serif;
  line-height:1.55;-webkit-font-smoothing:antialiased;
}
a{color:inherit;text-decoration:none}
img{max-width:100%;display:block}

/* small-caps monospace label -- the structural voice: nav, captions,
   meta, footer. Reused everywhere something is a LABEL rather than
   content a shopper reads for its own sake. */
.mono{
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  text-transform:uppercase;letter-spacing:.08em;
}

/* ---- header ---- */
.topbar{
  position:sticky;top:0;z-index:20;background:var(--paper);color:var(--ink);
  display:flex;align-items:center;gap:20px;padding:16px 22px;
  border-bottom:1px solid var(--rule);
}
.nl-lockup{display:inline-flex;align-items:center;gap:10px}
.nl-mark{flex:none}
.nl-words{display:flex;flex-direction:column;line-height:1}
.nl-words b{font-family:Archivo,sans-serif;font-weight:800;font-size:17px;letter-spacing:-.01em}
.nl-words i{font-style:normal;font-size:9px;letter-spacing:.32em;opacity:.6;margin-top:2px}

.shop-search{flex:1 1 auto;min-width:0;max-width:420px}
.shop-search input{
  width:100%;padding:9px 15px;border-radius:999px;border:1px solid var(--rule);
  background:var(--bone);color:var(--ink);font-size:13.5px;font-family:inherit;
}
.shop-search input::placeholder{color:#999}
.shop-search input:focus{outline:none;border-color:var(--ink)}
.topnav{
  display:flex;align-items:center;gap:20px;margin-left:auto;flex:none;
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  font-size:11.5px;letter-spacing:.09em;text-transform:uppercase;
}
.topnav a:hover{color:var(--muted)}
.cart-pill{
  display:inline-flex;align-items:center;gap:7px;background:var(--ink);color:var(--paper);
  padding:7px 14px;border-radius:999px;font-weight:600;font-size:11px;
}
@media (max-width:720px){ .topnav .hide-sm{display:none} }

/* ---- layout ---- */
main{max-width:1180px;margin:0 auto;padding:32px 22px 90px}
.page-head{margin-bottom:26px}
h1{font-family:Archivo,sans-serif;font-weight:800;font-size:clamp(26px,4.2vw,36px);
   line-height:1.05;margin:0 0 8px;letter-spacing:-.02em;text-wrap:balance}
.page-head p{margin:0;color:var(--muted);font-size:15px}
h2{font-family:Archivo,sans-serif;font-weight:800;font-size:20px;margin:0 0 14px;letter-spacing:-.01em}

/* ---- product grid ---- */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:18px}
.card{
  background:var(--paper);border:1px solid var(--rule);border-radius:var(--radius);
  overflow:hidden;display:flex;flex-direction:column;
  transition:border-color .12s ease, transform .12s ease;
}
.card:hover{border-color:var(--ink);transform:translateY(-2px)}
.card-art{aspect-ratio:5/7;background:var(--sunken);display:grid;place-items:center;overflow:hidden}
.card-art img{width:100%;height:100%;object-fit:contain}
.card-art .noart{color:var(--muted);font-size:12px}
.card-body{padding:12px 13px 14px;display:flex;flex-direction:column;gap:4px;flex:1}
.card-title{
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  font-weight:700;font-size:12.5px;letter-spacing:.02em;line-height:1.35;
}
.card-sub{
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
}
.card-price{font-family:Archivo,sans-serif;font-weight:800;font-size:18px;margin-top:auto;padding-top:8px}
.tag{
  display:inline-block;font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;
  color:var(--ink);background:var(--bone);border:1px solid var(--rule);padding:2px 8px;border-radius:999px;
}
.tag.low{color:var(--brass-lit);background:#faf3e8;border-color:#eddcc0}

/* ---- buttons ---- */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:8px;
  background:var(--paper);color:var(--ink);border:1px solid var(--ink);padding:11px 22px;border-radius:999px;
  font-weight:600;font-size:14px;font-family:inherit;cursor:pointer;
  transition:background .12s ease,color .12s ease;
}
.btn:hover{background:var(--ink);color:var(--paper)}
.btn.brass{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.btn.brass:hover{background:var(--brass-lit);border-color:var(--brass-lit)}
.btn.ghost{background:transparent;color:var(--ink);border:1px solid var(--rule)}
.btn.ghost:hover{background:var(--bone);color:var(--ink)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn:disabled:hover{background:var(--paper);color:var(--ink)}

/* ---- panels / detail ---- */
.panel{background:var(--paper);border:1px solid var(--rule);border-radius:var(--radius);padding:22px}
.detail{display:grid;grid-template-columns:minmax(0,420px) minmax(0,1fr);gap:34px;align-items:start}
@media (max-width:780px){ .detail{grid-template-columns:1fr} }
.detail-art{background:var(--paper);border:1px solid var(--rule);border-radius:var(--radius);padding:18px}
.detail-price{font-family:Archivo,sans-serif;font-weight:800;font-size:32px;margin:6px 0 4px;letter-spacing:-.01em}
.spec-list{list-style:none;margin:18px 0 0;padding:0;border-top:1px solid var(--rule)}
.spec-list li{
  display:flex;justify-content:space-between;gap:16px;padding:10px 0;
  border-bottom:1px solid var(--rule);font-size:13px;
}
.spec-list li span:first-child{
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  color:var(--muted);font-size:11px;letter-spacing:.06em;text-transform:uppercase;
}

/* ---- cart ---- */
.line{display:flex;gap:14px;align-items:center;padding:14px 0;border-bottom:1px solid var(--rule)}
.line:last-child{border-bottom:none}
.line-art{width:52px;aspect-ratio:5/7;background:var(--sunken);border-radius:2px;overflow:hidden;flex:none}
.line-art img{width:100%;height:100%;object-fit:contain}
.line-main{flex:1;min-width:0}
.line-title{font-weight:600;font-size:14px}
.totals{margin-top:18px;border-top:2px solid var(--ink);padding-top:14px}
.totals div{display:flex;justify-content:space-between;padding:5px 0;font-size:14px}
.totals .grand{font-family:Archivo,sans-serif;font-weight:800;font-size:20px;padding-top:10px}
.totals .discount-row span:last-child{color:var(--teal)}
.discountpanel{margin-top:18px}
.discountlist{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:9px}
.discountlist li{display:flex;justify-content:space-between;gap:14px;font-size:14px}
.discountlist li span:last-child{color:var(--teal);font-weight:600;white-space:nowrap}
.discountlist li.nudge{color:var(--muted);font-style:italic}
.discountlist li.nudge span{color:inherit;font-weight:400}

.empty{text-align:center;padding:60px 20px;color:var(--muted)}
.empty .mark{opacity:.2;margin:0 auto 16px}
.muted{color:var(--muted);font-size:14px}
.notice{border-left:2px solid var(--ink);padding:10px 0 10px 15px;margin:16px 0;font-size:14px;color:var(--muted)}

label.field{
  display:flex;flex-direction:column;gap:6px;margin-bottom:14px;
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
}
label.field input,label.field select,label.field textarea{
  border:1px solid var(--rule);border-radius:2px;padding:11px 13px;font-size:14.5px;
  font-family:Archivo,sans-serif;text-transform:none;letter-spacing:normal;color:var(--ink);background:var(--paper);
}
label.field input:focus,label.field select:focus,label.field textarea:focus{
  outline:none;border-color:var(--ink)}

/* ---- side navigation drawer ---- */
.navtoggle{
  display:inline-grid;place-items:center;width:36px;height:36px;flex:none;cursor:pointer;
  background:transparent;border:1px solid var(--rule);border-radius:999px;color:var(--ink);
}
.navtoggle:hover{border-color:var(--ink)}
.navtoggle svg{width:17px;height:17px}

.scrim{
  position:fixed;inset:0;background:rgba(17,17,17,.4);opacity:0;pointer-events:none;
  transition:opacity .18s ease;z-index:28;
}
body.nav-open .scrim{opacity:1;pointer-events:auto}

.sidenav{
  position:fixed;top:0;left:0;bottom:0;width:272px;z-index:30;background:var(--paper);
  color:var(--ink);padding:18px 0 26px;overflow-y:auto;
  transform:translateX(-100%);transition:transform .2s ease;
  border-right:1px solid var(--rule);
}
body.nav-open .sidenav{transform:none}
.sidenav-head{display:flex;align-items:center;justify-content:space-between;padding:0 18px 16px;border-bottom:1px solid var(--rule);margin-bottom:6px}
.sidenav-close{background:none;border:none;color:var(--ink);font-size:22px;line-height:1;cursor:pointer;padding:0 4px}
.sidenav-close:hover{color:var(--muted)}
.card-art,.detail-art,.line-art{position:relative}
.art-tag{
  position:absolute;left:8px;bottom:8px;z-index:1;
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  font-size:9.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
  padding:3px 7px;border-radius:2px;pointer-events:none;
}
.art-tag.own{background:var(--teal);color:#eefaf8}
.art-tag.stock{background:rgba(17,17,17,.68);color:#fff}
.detail-art .art-tag{left:12px;bottom:12px;font-size:10.5px;padding:4px 9px}
.line-art .art-tag{display:none}
.seg-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 20px}
.seg-tab{
  padding:8px 15px;border-radius:999px;font-size:11.5px;font-weight:600;
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;letter-spacing:.06em;text-transform:uppercase;
  background:var(--paper);border:1px solid var(--rule);color:var(--muted);
}
.seg-tab:hover{border-color:var(--ink);color:var(--ink)}
.seg-tab.active{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.dept-grid{
  display:grid;gap:16px;margin:22px 0 34px;
  grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
}
.dept-card{
  display:flex;flex-direction:column;text-decoration:none;overflow:hidden;
  border:1px solid var(--rule);border-radius:var(--radius);background:var(--paper);
  transition:border-color .15s ease,transform .15s ease;
}
.dept-card:hover{border-color:var(--ink);transform:translateY(-3px)}
.dept-hero{aspect-ratio:16/9;background:var(--sunken);overflow:hidden;position:relative}
.dept-hero img{width:100%;height:100%;object-fit:contain}
.dept-hero .art-tag{left:12px;bottom:12px}
.dept-hero-mark{width:100%;height:100%;display:grid;place-items:center;color:var(--rule)}
.dept-card-body{display:flex;flex-direction:column;gap:8px;padding:20px 22px 22px}
.dept-card h2{margin:0;font-size:19px;color:var(--ink)}
.dept-card p{margin:0;font-size:14px;color:var(--muted);flex:1}
.dept-meta{
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;
}
.section-head{
  margin:34px 0 16px;font-family:Archivo,sans-serif;font-weight:800;font-size:18px;
  letter-spacing:-.01em;color:var(--ink);
}
.breadcrumbs{
  display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin:0 0 18px;
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);
}
.breadcrumbs a{color:var(--muted)}
.breadcrumbs a:hover{color:var(--ink)}
.breadcrumbs span[aria-current]{color:var(--ink);font-weight:600}
.breadcrumbs .sep{opacity:.5}
@media (prefers-reduced-motion:reduce){.dept-card{transition:none}}
.navgroup{padding:14px 0 4px}
.navgroup h4{
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:#999;
  margin:0 0 8px;padding:0 18px;font-weight:700;
}
.sidenav a{
  display:flex;align-items:center;justify-content:space-between;gap:10px;
  padding:9px 18px;font-size:14px;color:var(--ink);
}
.sidenav a:hover{background:var(--bone)}
.sidenav a .n{
  font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  font-size:11px;color:var(--muted);
}
.sidenav hr{border:none;border-top:1px solid var(--rule);margin:12px 18px}

.filters{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end;margin-bottom:22px}
.filters .field{margin-bottom:0;flex:1 1 150px;min-width:120px}
.filters .btn{flex:none}
.bulk-art{background:var(--ink);color:var(--paper)}
.bulk-art img{object-fit:contain;padding:22px}
.bulk-count{font-family:Archivo,sans-serif;font-weight:800;font-size:40px;color:var(--paper)}
.bulk-count.small{font-size:15px;display:grid;place-items:center;height:100%;background:var(--ink);color:var(--paper)}
.bulk-count.big{font-family:Archivo,sans-serif;font-weight:800;font-size:68px;color:var(--ink)}

footer{border-top:1px solid var(--rule);margin-top:70px;padding:26px 22px;color:var(--muted);font-size:12px}
footer{font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;letter-spacing:.03em}
footer .inner{max-width:1180px;margin:0 auto;display:flex;gap:18px;flex-wrap:wrap;justify-content:space-between;align-items:center}
footer a:hover{color:var(--ink)}
.social-links{display:flex;gap:14px;align-items:center}
.social-link{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:12px;font-weight:600}
.social-link:hover{color:var(--ink)}
.social-link svg{width:16px;height:16px;flex:none}
.social-link span{display:none}
@media (min-width:560px){.social-link span{display:inline}}
.social-icons{display:flex;gap:12px;margin-top:22px}
.social-icon{
  display:inline-flex;align-items:center;justify-content:center;width:42px;height:42px;
  border-radius:999px;background:var(--paper);border:1px solid var(--rule);color:var(--ink);
  transition:border-color .15s ease,transform .15s ease,color .15s ease;
}
.social-icon:hover{border-color:var(--ink);transform:translateY(-2px)}
.social-icon svg{width:19px;height:19px}
@media (prefers-reduced-motion:reduce){.social-icon{transition:none}}

/* ---- home hero: an oversized wordmark moment, Nordframe-style ----
   The reference builds its hero from one massive typographic wordmark
   and nothing else -- no background art, no gradient. NL Vault's own
   lockup (mark + "NL VAULT") stands in for that wordmark here, blown up
   to the same scale, rather than introducing a second brand treatment
   just for the hero. */
.hero-banner{background:var(--paper);border-bottom:1px solid var(--rule)}
.hero-inner{
  max-width:1180px;margin:0 auto;padding:80px 22px 64px;
  display:flex;flex-direction:column;align-items:center;text-align:center;
}
.hero-brand{margin:0 0 22px}
.hero-brand .nl-lockup{display:flex;flex-direction:column;align-items:center;gap:14px}
.hero-brand .nl-words{align-items:center;gap:2px}
.hero-brand .nl-words b{
  font-family:Archivo,sans-serif;font-weight:800;letter-spacing:-.03em;line-height:.86;
  font-size:clamp(56px,12vw,140px);color:var(--ink);
}
.hero-brand .nl-words i{
  font-style:normal;font-family:'JetBrains Mono',ui-monospace,Menlo,monospace;
  font-size:clamp(12px,1.8vw,18px);letter-spacing:.5em;color:var(--muted);
  margin-top:4px;opacity:1;
}
.hero-inner p{color:var(--muted);margin:0 auto;font-size:14.5px;max-width:46ch;line-height:1.6}
.hero-inner .social-icons{margin-top:26px}

@media (max-width:560px){
  .hero-inner{padding:52px 22px 44px}
}
</style>"""

FONTS = (
    "<link rel='preconnect' href='https://fonts.googleapis.com'>"
    "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
    "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?"
    "family=Archivo:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap'>"
)


NAV_SCRIPT = """<script>
(function(){
  var body = document.body;
  function open(){ body.classList.add('nav-open'); document.querySelector('.navtoggle').setAttribute('aria-expanded','true'); }
  function close(){ body.classList.remove('nav-open'); document.querySelector('.navtoggle').setAttribute('aria-expanded','false'); }
  document.querySelectorAll('[data-nav-open]').forEach(function(el){ el.addEventListener('click', open); });
  document.querySelectorAll('[data-nav-close]').forEach(function(el){ el.addEventListener('click', close); });
  document.addEventListener('keydown', function(e){ if(e.key === 'Escape') close(); });
})();
</script>"""

# GSAP, loaded plain (no defer/async) so these two tags run to completion
# before the inline script right after them touches the `gsap` global --
# a deferred script and a following inline script do NOT execute in that
# order, so this is deliberate, not an oversight.
#
# Kept to what GSAP is actually good at and no further: a one-time
# entrance on the hero, and a fade-up the first time a product tile
# scrolls into view. No bounce, no colour, no looping -- motion here is
# a courtesy, not a feature, and prefers-reduced-motion skips all of it.
GSAP_SCRIPT = """<script src='https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js'></script>
<script src='https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/ScrollTrigger.min.js'></script>
<script>
(function(){
  if (typeof gsap === 'undefined') return;
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  var hero = document.querySelector('.hero-inner');
  if (hero && hero.children.length) {
    gsap.from(hero.children, {opacity: 0, y: 16, duration: 0.6, ease: 'power2.out', stagger: 0.08});
  }

  if (typeof ScrollTrigger !== 'undefined') {
    gsap.registerPlugin(ScrollTrigger);
    gsap.utils.toArray('.dept-card, .grid .card').forEach(function(el){
      gsap.from(el, {
        opacity: 0, y: 20, duration: 0.5, ease: 'power2.out',
        scrollTrigger: {trigger: el, start: 'top 92%', once: true},
      });
    });
  }
})();
</script>"""


def sidenav(links: str = "") -> str:
    """The slide-out navigation. Hidden by default and summoned by the
    toggle, rather than a permanent column: on a phone a fixed sidebar
    would eat half the screen, and on desktop the product grid is the
    thing people came for."""
    return (
        "<div class='scrim' data-nav-close></div>"
        "<aside class='sidenav' aria-label='Shop navigation'>"
        "<div class='sidenav-head'>"
        f"{logo_lockup()}"
        "<button class='sidenav-close' data-nav-close aria-label='Close navigation'>&times;</button>"
        "</div>"
        "<div class='navgroup'><h4>Shop</h4>"
        "<a href='/'>Home</a>"
        "<a href='/browse'>Browse everything</a>"
        "<a href='/bulk'>Bulk Master</a>"
        "</div>"
        f"{links}"
        "<hr>"
        "<div class='navgroup'><h4>Info</h4>"
        "<a href='/shipping'>Shipping &amp; returns</a>"
        "<a href='/contact'>Contact us</a>"
        "<a href='/cart'>Your cart</a>"
        "</div>"
        "</aside>"
    )


def header(cart_count: int = 0, query: str = "", dept_links: str = "") -> str:
    safe_query = query.replace("'", "&#39;").replace('"', "&quot;")
    badge = f"<span class='cart-pill'>Cart {cart_count}</span>" if cart_count else "<span class='cart-pill'>Cart</span>"
    return (
        "<header class='topbar'>"
        "<button class='navtoggle' data-nav-open aria-expanded='false' aria-label='Open navigation'>"
        "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round'>"
        "<path d='M3 6h18M3 12h18M3 18h18'/></svg></button>"
        f"<a href='/'>{logo_lockup()}</a>"
        "<form class='shop-search' method='get' action='/browse'>"
        f"<input name='q' value='{safe_query}' placeholder='Search the vault…' autocomplete='off'>"
        "</form>"
        "<nav class='topnav'>"
        # Departments come from stock rather than being hardcoded, for the
        # same reason the drawer's do: a header link is the most prominent
        # place to promise a section, and the worst place to land someone
        # on an empty page.
        f"{dept_links}"
        "<a href='/bulk' class='hide-sm'>Bulk Master</a>"
        "<a href='/shipping' class='hide-sm'>Shipping</a>"
        f"<a href='/cart'>{badge}</a>"
        "</nav></header>"
    )


DEFAULT_DESCRIPTION = "Hand-picked trading cards and collectibles, shipped tracked from Australia."


def _breadcrumb_nav(crumbs: tuple[tuple[str, str], ...]) -> str:
    """The visible trail. The last crumb is never a link, regardless of
    what href it was given -- it's the page you're already on."""
    if not crumbs:
        return ""
    parts = []
    last = len(crumbs) - 1
    for i, (label, href) in enumerate(crumbs):
        if i == last or not href:
            parts.append(f"<span aria-current='page'>{escape(label)}</span>")
        else:
            parts.append(f"<a href='{escape(href)}'>{escape(label)}</a>")
    return "<nav class='breadcrumbs' aria-label='Breadcrumb'>" + "<span class='sep'>/</span>".join(parts) + "</nav>"


def _breadcrumb_jsonld(crumbs: tuple[tuple[str, str], ...], site_url: str) -> dict | None:
    if not crumbs or not site_url:
        return None
    items = []
    for i, (label, href) in enumerate(crumbs, start=1):
        entry = {"@type": "ListItem", "position": i, "name": label}
        if href:
            entry["item"] = site_url + href
        items.append(entry)
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}


def page(title: str, body: str, *, cart_count: int = 0, query: str = "",
         nav_links: str = "", dept_links: str = "", description: str = DEFAULT_DESCRIPTION,
         social_html: str = "", hero: str = "", canonical: str = "", site_url: str = "",
         og_image: str = "", robots: str = "", same_as: tuple[str, ...] = (),
         crumbs: tuple[tuple[str, str], ...] = (), extra_jsonld: tuple[dict, ...] = ()) -> str:
    title_safe = escape(title)
    description_safe = escape(description)
    image = og_image or (f"{site_url}/social-share.png" if site_url else "")

    organization: dict = {
        "@context": "https://schema.org",
        "@type": "OnlineStore",
        "name": "NL Vault",
        "description": DEFAULT_DESCRIPTION,
    }
    if site_url:
        organization["url"] = site_url
    if same_as:
        organization["sameAs"] = list(same_as)
    json_ld_blocks: list[dict] = [organization]
    crumb_ld = _breadcrumb_jsonld(crumbs, site_url)
    if crumb_ld:
        json_ld_blocks.append(crumb_ld)
    json_ld_blocks.extend(extra_jsonld)
    structured_data = "".join(
        f"<script type='application/ld+json'>{json.dumps(block)}</script>" for block in json_ld_blocks
    )

    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title_safe}</title>"
        f"<meta name='description' content='{description_safe}'>"
        + (f"<meta name='robots' content='{escape(robots)}'>" if robots else "")
        + (f"<link rel='canonical' href='{escape(canonical)}'>" if canonical else "")
        + f"<meta property='og:title' content='{title_safe}'>"
        f"<meta property='og:description' content='{description_safe}'>"
        "<meta property='og:type' content='website'>"
        "<meta property='og:site_name' content='NL Vault'>"
        + (f"<meta property='og:url' content='{escape(canonical)}'>" if canonical else "")
        + (f"<meta property='og:image' content='{escape(image)}'>" if image else "")
        + f"<meta name='twitter:card' content='{'summary_large_image' if image else 'summary'}'>"
        + (f"<meta name='twitter:image' content='{escape(image)}'>" if image else "")
        + f"<meta name='theme-color' content='{BRASS}'>"
        "<link rel='icon' type='image/svg+xml' href='/favicon.svg'>"
        "<link rel='icon' type='image/png' href='/favicon.ico'>"
        "<link rel='apple-touch-icon' href='/apple-touch-icon.png'>"
        f"{FONTS}{STYLE}{structured_data}</head><body>"
        f"{sidenav(nav_links)}"
        f"{header(cart_count, query, dept_links)}"
        f"{hero}"
        f"<main>{_breadcrumb_nav(crumbs)}{body}</main>"
        "<footer><div class='inner'>"
        "<span>NL Vault — trading cards &amp; collectibles, shipped from Australia.</span>"
        f"{social_html}"
        "<span><a href='/shipping'>Shipping &amp; returns</a> &nbsp;·&nbsp; "
        "<a href='/contact'>Contact us</a> &nbsp;·&nbsp; "
        "<a href='/privacy'>Privacy</a> &nbsp;·&nbsp; <a href='/terms'>Terms</a></span>"
        "</div></footer>"
        f"{NAV_SCRIPT}"
        f"{GSAP_SCRIPT}"
        "</body></html>"
    )
