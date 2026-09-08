"""Page shell for the public shop.

Its own shell rather than automation_control.ui's: the admin tool is a
dense light-grey dashboard, and a shop needs to look like a shop. Same
plain-string-HTML convention though, and no CDN assets beyond two Google
Fonts, so the storefront has no build step either.

Visual language is a playful, colourful collectibles-store look (a
customer-supplied reference, x-playground.com) rather than the earlier
monochrome-editorial one: a solid-ink header and hero, a bold rounded
display face (Baloo 2) for headings/prices/buttons, a warm rounded body
face (Nunito) everywhere else, generously rounded cards, and a diagonal
ribbon badge for stock urgency -- built for a hobby audience (Pokémon,
One Piece, blind-box collectibles) who want fun and colour, not a
gallery aesthetic.
"""

import json
from html import escape

from .brand import BRASS, logo_lockup

STYLE = """<style>
:root{
  --ink:#15171a; --paper:#fff; --bone:#faf7f2; --sunken:#f1ede6;
  --muted:#6b6b6b; --rule:#e7e2d8;
  --brass:#b3852f; --brass-lit:#8a6423;
  --teal:#0f9e94; --teal-dark:#0b7a72;
  --pink:#ff5d8f; --pink-dark:#e14577;
  --radius:16px; --radius-sm:10px;
}
*{box-sizing:border-box}
body{
  margin:0;background:var(--paper);color:var(--ink);
  font-family:Nunito,ui-sans-serif,system-ui,-apple-system,sans-serif;
  line-height:1.55;-webkit-font-smoothing:antialiased;
}
a{color:inherit;text-decoration:none}
img{max-width:100%;display:block}

/* ---- announcement bar ---- */
.announce{
  background:var(--bone);color:var(--ink);text-align:center;padding:9px 16px;
  font-family:'Baloo 2',sans-serif;font-weight:700;font-size:13px;letter-spacing:.01em;
}
.announce a{border-bottom:2px solid var(--teal)}

/* ---- header ---- */
.topbar{
  position:sticky;top:0;z-index:20;background:var(--ink);color:#fff;
  display:flex;align-items:center;gap:20px;padding:14px 22px;
}
.nl-lockup{display:inline-flex;align-items:center;gap:10px}
.nl-mark{flex:none}
.nl-words{display:flex;flex-direction:column;line-height:1}
.nl-words b{font-family:'Baloo 2',sans-serif;font-weight:800;font-size:19px;letter-spacing:-.01em;color:#fff}
.nl-words i{font-style:normal;font-family:Nunito,sans-serif;font-size:9px;letter-spacing:.28em;opacity:.65;margin-top:2px;color:#fff}

.shop-search{flex:1 1 auto;min-width:0;max-width:420px}
.shop-search input{
  width:100%;padding:9px 15px;border-radius:999px;border:1px solid #33363c;
  background:#22252b;color:#fff;font-size:13.5px;font-family:inherit;
}
.shop-search input::placeholder{color:#8a8d94}
.shop-search input:focus{outline:none;border-color:var(--teal)}
.topnav{
  display:flex;align-items:center;gap:20px;margin-left:auto;flex:none;
  font-family:'Baloo 2',sans-serif;font-weight:600;
  font-size:13px;letter-spacing:.01em;
}
.topnav a{color:#fff}
.topnav a:hover{color:var(--teal)}
.cart-pill{
  display:inline-flex;align-items:center;gap:7px;background:var(--teal);color:#fff;
  padding:7px 16px;border-radius:999px;font-weight:800;font-size:13px;
}
@media (max-width:720px){ .topnav .hide-sm{display:none} }

/* ---- layout ---- */
main{max-width:1180px;margin:0 auto;padding:32px 22px 90px}
.page-head{margin-bottom:26px}
h1{font-family:'Baloo 2',sans-serif;font-weight:800;font-size:clamp(26px,4.2vw,38px);
   line-height:1.1;margin:0 0 8px;letter-spacing:-.01em;text-wrap:balance}
.page-head p{margin:0;color:var(--muted);font-size:15px}
h2{font-family:'Baloo 2',sans-serif;font-weight:700;font-size:21px;margin:0 0 14px;letter-spacing:-.005em}

/* ---- product grid ---- */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:20px}
.card{
  background:var(--paper);border:1px solid var(--rule);border-radius:var(--radius);
  overflow:hidden;display:flex;flex-direction:column;box-shadow:0 1px 2px rgba(21,23,26,.04);
  transition:transform .14s ease, box-shadow .14s ease;
}
.card:hover{transform:translateY(-3px);box-shadow:0 10px 22px rgba(21,23,26,.10)}
.card-art{aspect-ratio:5/7;background:var(--sunken);display:grid;place-items:center;overflow:hidden}
.card-art img{width:100%;height:100%;object-fit:contain}
.card-art .noart{color:var(--muted);font-size:12px}
.card-body{padding:13px 14px 15px;display:flex;flex-direction:column;gap:4px;flex:1}
.card-title{font-family:Nunito,sans-serif;font-weight:800;font-size:14.5px;line-height:1.3;color:var(--ink)}
.card-sub{font-family:Nunito,sans-serif;font-size:12.5px;color:var(--muted);font-weight:600}
.card-price{font-family:'Baloo 2',sans-serif;font-weight:700;font-size:19px;margin-top:auto;padding-top:8px;color:var(--ink)}
.tag{
  display:inline-block;font-family:Nunito,sans-serif;
  font-size:11px;font-weight:800;letter-spacing:.01em;
  color:var(--pink-dark);background:#ffe7ee;padding:3px 10px;border-radius:999px;
}
.tag.low{color:var(--brass-lit);background:#fbeecb}

/* diagonal ribbon badge -- stock urgency lives here now instead of a
   pill next to the price, tucked into the product photo's corner. */
.ribbon-wrap{position:absolute;top:0;left:0;width:96px;height:96px;overflow:hidden;pointer-events:none;z-index:2}
.ribbon{
  position:absolute;top:16px;left:-30px;width:150px;text-align:center;
  transform:rotate(-45deg);background:var(--pink);color:#fff;
  font-family:'Baloo 2',sans-serif;font-weight:700;font-size:11px;letter-spacing:.03em;
  text-transform:uppercase;padding:4px 0;box-shadow:0 2px 5px rgba(0,0,0,.15);
}

/* ---- buttons ---- */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:8px;
  background:var(--ink);color:#fff;border:2px solid var(--ink);padding:11px 24px;border-radius:999px;
  font-weight:800;font-size:14.5px;font-family:'Baloo 2',sans-serif;cursor:pointer;
  transition:transform .1s ease,background .12s ease,border-color .12s ease;
}
.btn:hover{transform:translateY(-1px)}
.btn.brass{background:var(--teal);color:#fff;border-color:var(--teal)}
.btn.brass:hover{background:var(--teal-dark);border-color:var(--teal-dark)}
.btn.ghost{background:transparent;color:var(--ink);border:2px solid var(--rule)}
.btn.ghost:hover{border-color:var(--ink)}
.btn:disabled{opacity:.4;cursor:not-allowed;transform:none}

/* ---- panels / detail ---- */
.panel{background:var(--paper);border:1px solid var(--rule);border-radius:var(--radius);padding:22px}
.detail{display:grid;grid-template-columns:minmax(0,420px) minmax(0,1fr);gap:34px;align-items:start}
@media (max-width:780px){ .detail{grid-template-columns:1fr} }
.detail-art{background:var(--paper);border:1px solid var(--rule);border-radius:var(--radius);padding:18px}
.detail-price{font-family:'Baloo 2',sans-serif;font-weight:800;font-size:32px;margin:6px 0 4px;letter-spacing:-.01em}
.spec-list{list-style:none;margin:18px 0 0;padding:0;border-top:1px solid var(--rule)}
.spec-list li{
  display:flex;justify-content:space-between;gap:16px;padding:10px 0;
  border-bottom:1px solid var(--rule);font-size:14px;
}
.spec-list li span:first-child{color:var(--muted);font-weight:700}

/* ---- cart ---- */
.line{display:flex;gap:14px;align-items:center;padding:14px 0;border-bottom:1px solid var(--rule)}
.line:last-child{border-bottom:none}
.line-art{width:52px;aspect-ratio:5/7;background:var(--sunken);border-radius:var(--radius-sm);overflow:hidden;flex:none}
.line-art img{width:100%;height:100%;object-fit:contain}
.line-main{flex:1;min-width:0}
.line-title{font-weight:700;font-size:14px}
.totals{margin-top:18px;border-top:3px solid var(--ink);padding-top:14px}
.totals div{display:flex;justify-content:space-between;padding:5px 0;font-size:14px}
.totals .grand{font-family:'Baloo 2',sans-serif;font-weight:800;font-size:21px;padding-top:10px}
.totals .discount-row span:last-child{color:var(--teal-dark)}
.discountpanel{margin-top:18px}
.discountlist{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:9px}
.discountlist li{display:flex;justify-content:space-between;gap:14px;font-size:14px}
.discountlist li span:last-child{color:var(--teal-dark);font-weight:700;white-space:nowrap}
.discountlist li.nudge{color:var(--muted);font-style:italic}
.discountlist li.nudge span{color:inherit;font-weight:400}

.empty{text-align:center;padding:60px 20px;color:var(--muted)}
.empty .mark{opacity:.2;margin:0 auto 16px}
.muted{color:var(--muted);font-size:14px}
.notice{border-left:3px solid var(--teal);background:var(--bone);border-radius:0 var(--radius-sm) var(--radius-sm) 0;padding:10px 14px;margin:16px 0;font-size:14px;color:var(--ink)}

label.field{
  display:flex;flex-direction:column;gap:6px;margin-bottom:14px;
  font-family:Nunito,sans-serif;font-size:13px;font-weight:700;color:var(--muted);
}
label.field input,label.field select,label.field textarea{
  border:2px solid var(--rule);border-radius:var(--radius-sm);padding:11px 13px;font-size:14.5px;
  font-family:Nunito,sans-serif;color:var(--ink);background:var(--paper);
}
label.field input:focus,label.field select:focus,label.field textarea:focus{
  outline:none;border-color:var(--teal)}

/* ---- side navigation drawer ---- */
.navtoggle{
  display:inline-grid;place-items:center;width:38px;height:38px;flex:none;cursor:pointer;
  background:#22252b;border:none;border-radius:999px;color:#fff;
}
.navtoggle:hover{background:var(--teal)}
.navtoggle svg{width:17px;height:17px}

.scrim{
  position:fixed;inset:0;background:rgba(21,23,26,.5);opacity:0;pointer-events:none;
  transition:opacity .18s ease;z-index:28;
}
body.nav-open .scrim{opacity:1;pointer-events:auto}

.sidenav{
  position:fixed;top:0;left:0;bottom:0;width:272px;z-index:30;background:var(--paper);
  color:var(--ink);padding:18px 0 26px;overflow-y:auto;
  transform:translateX(-100%);transition:transform .2s ease;
}
body.nav-open .sidenav{transform:none}
.sidenav-head{display:flex;align-items:center;justify-content:space-between;padding:0 18px 16px;border-bottom:1px solid var(--rule);margin-bottom:6px}
.sidenav-close{background:none;border:none;color:var(--ink);font-size:22px;line-height:1;cursor:pointer;padding:0 4px}
.sidenav-close:hover{color:var(--teal)}
.card-art,.detail-art,.line-art{position:relative}
.art-tag{
  position:absolute;left:8px;bottom:8px;z-index:1;
  font-family:Nunito,sans-serif;
  font-size:11px;font-weight:800;
  padding:4px 9px;border-radius:999px;pointer-events:none;
}
.art-tag.own{background:var(--teal);color:#fff}
.art-tag.stock{background:rgba(21,23,26,.72);color:#fff}
.detail-art .art-tag{left:12px;bottom:12px;font-size:12px;padding:5px 10px}
.line-art .art-tag{display:none}
.seg-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 20px}
.seg-tab{
  padding:8px 16px;border-radius:999px;font-size:13.5px;font-weight:700;
  font-family:Nunito,sans-serif;
  background:var(--paper);border:2px solid var(--rule);color:var(--muted);
}
.seg-tab:hover{border-color:var(--teal);color:var(--ink)}
.seg-tab.active{background:var(--teal);color:#fff;border-color:var(--teal)}
.dept-grid{
  display:grid;gap:18px;margin:22px 0 34px;
  grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
}
.dept-card{
  display:flex;flex-direction:column;text-decoration:none;overflow:hidden;
  border:1px solid var(--rule);border-radius:var(--radius);background:var(--paper);
  box-shadow:0 1px 2px rgba(21,23,26,.04);
  transition:transform .15s ease,box-shadow .15s ease;
}
.dept-card:hover{transform:translateY(-4px);box-shadow:0 12px 26px rgba(21,23,26,.12)}
.dept-hero{aspect-ratio:16/9;background:var(--sunken);overflow:hidden;position:relative}
.dept-hero img{width:100%;height:100%;object-fit:contain}
.dept-hero .art-tag{left:12px;bottom:12px}
.dept-hero-mark{width:100%;height:100%;display:grid;place-items:center;color:var(--rule)}
.dept-card-body{display:flex;flex-direction:column;gap:8px;padding:20px 22px 22px}
.dept-card h2{margin:0;font-size:20px;color:var(--ink)}
.dept-card p{margin:0;font-size:14px;color:var(--muted);flex:1}
.dept-meta{font-family:Nunito,sans-serif;font-size:13px;color:var(--muted);font-weight:700}
.section-head{
  display:flex;align-items:baseline;justify-content:space-between;gap:14px;
  margin:34px 0 16px;
}
.section-head h2{margin:0;font-size:21px}
.section-head .view-all{
  font-family:Nunito,sans-serif;font-weight:800;font-size:13.5px;color:var(--teal-dark);
  border-bottom:2px solid var(--teal);white-space:nowrap;
}
.section-head .view-all:hover{color:var(--teal)}
.breadcrumbs{
  display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin:0 0 18px;
  font-family:Nunito,sans-serif;font-size:13px;font-weight:700;color:var(--muted);
}
.breadcrumbs a{color:var(--muted)}
.breadcrumbs a:hover{color:var(--teal-dark)}
.breadcrumbs span[aria-current]{color:var(--ink)}
.breadcrumbs .sep{opacity:.5}
@media (prefers-reduced-motion:reduce){.dept-card{transition:none}}
.navgroup{padding:14px 0 4px}
.navgroup h4{
  font-family:'Baloo 2',sans-serif;
  font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:#999;
  margin:0 0 8px;padding:0 18px;font-weight:700;
}
.sidenav a{
  display:flex;align-items:center;justify-content:space-between;gap:10px;
  padding:10px 18px;font-size:15px;font-weight:700;color:var(--ink);
}
.sidenav a:hover{background:var(--bone);color:var(--teal-dark)}
.sidenav a .n{font-family:Nunito,sans-serif;font-size:12px;font-weight:700;color:var(--muted)}
.sidenav hr{border:none;border-top:1px solid var(--rule);margin:12px 18px}

.filters{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end;margin-bottom:22px}
.filters .field{margin-bottom:0;flex:1 1 150px;min-width:120px}
.filters .btn{flex:none}
.bulk-art{background:var(--ink);color:#fff}
.bulk-art img{object-fit:contain;padding:22px}
.bulk-count{font-family:'Baloo 2',sans-serif;font-weight:800;font-size:40px;color:#fff}
.bulk-count.small{font-size:15px;display:grid;place-items:center;height:100%;background:var(--ink);color:#fff}
.bulk-count.big{font-family:'Baloo 2',sans-serif;font-weight:800;font-size:68px;color:var(--ink)}

footer{border-top:1px solid var(--rule);margin-top:70px;padding:26px 22px;color:var(--muted);font-size:13px}
footer{font-family:Nunito,sans-serif;font-weight:700}
footer .inner{max-width:1180px;margin:0 auto;display:flex;gap:18px;flex-wrap:wrap;justify-content:space-between;align-items:center}
footer a:hover{color:var(--teal-dark)}
.social-links{display:flex;gap:14px;align-items:center}
.social-link{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:13px;font-weight:700}
.social-link:hover{color:var(--teal-dark)}
.social-link svg{width:16px;height:16px;flex:none}
.social-link span{display:none}
@media (min-width:560px){.social-link span{display:inline}}
.social-icons{display:flex;gap:12px;margin-top:22px}
.social-icon{
  display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;
  border-radius:999px;background:var(--paper);border:2px solid var(--rule);color:var(--ink);
  transition:border-color .15s ease,transform .15s ease,color .15s ease;
}
.social-icon:hover{border-color:var(--teal);color:var(--teal-dark);transform:translateY(-2px)}
.social-icon svg{width:19px;height:19px}
@media (prefers-reduced-motion:reduce){.social-icon{transition:none}}

/* ---- home hero ----
   A warm colour block instead of a plain white gallery wall -- the
   wordmark and tagline carry the personality with a bold rounded face
   instead of relying on background photography (a card showcase here
   read as clutter -- see git history -- so the colour itself is the
   visual interest, not a strip of thumbnails). */
.hero-banner{background:linear-gradient(180deg,var(--bone) 0%,var(--paper) 100%)}
.hero-inner{
  max-width:1180px;margin:0 auto;padding:64px 22px 56px;
  display:flex;flex-direction:column;align-items:center;text-align:center;
}
.hero-brand{margin:0 0 14px}
.hero-brand .nl-lockup{display:flex;flex-direction:column;align-items:center;gap:6px}
.hero-brand .nl-words{align-items:center;gap:0}
.hero-brand .nl-words b{
  font-family:'Baloo 2',sans-serif;font-weight:800;letter-spacing:-.02em;line-height:1;
  font-size:clamp(48px,10vw,104px);color:var(--ink);
}
.hero-brand .nl-words i{
  font-style:normal;font-family:Nunito,sans-serif;font-weight:800;
  font-size:clamp(13px,1.6vw,17px);letter-spacing:.4em;color:var(--teal-dark);
  margin-top:2px;
}
.hero-inner p{color:var(--muted);margin:0 auto;font-size:15.5px;font-weight:600;max-width:46ch;line-height:1.6}
.hero-inner .btn{margin-top:22px}
.hero-inner .social-icons{margin-top:22px}

@media (max-width:560px){
  .hero-inner{padding:44px 22px 40px}
}
</style>"""

FONTS = (
    "<link rel='preconnect' href='https://fonts.googleapis.com'>"
    "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
    "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?"
    "family=Baloo+2:wght@500;600;700;800&family=Nunito:wght@400;600;700;800;900&display=swap'>"
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
        "<div class='announce'>Tracked shipping Australia-wide, every order &nbsp;·&nbsp; "
        "<a href='/shipping'>See rates</a></div>"
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
