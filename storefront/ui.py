"""Page shell for the public shop.

Its own shell rather than automation_control.ui's: the admin tool is a
dense light-grey dashboard, and a shop needs to look like a shop. Same
plain-string-HTML convention though, and no CDN assets beyond the two
Google fonts, so the storefront has no build step either.
"""

from .brand import BRASS, logo_lockup

STYLE = """<style>
:root{
  --ink:#14181d; --ink-2:#20262d; --bone:#f7f5f1; --paper:#fff;
  --brass:#b3852f; --brass-lit:#d9ab53; --teal:#14625f;
  --rule:#e3ddd3; --muted:#6d6558;
  --shadow:0 1px 2px rgba(20,24,29,.05), 0 10px 30px rgba(20,24,29,.07);
  --radius:14px;
}
*{box-sizing:border-box}
body{
  margin:0;background:var(--bone);color:var(--ink);
  font-family:Archivo,ui-sans-serif,system-ui,-apple-system,sans-serif;
  line-height:1.55;-webkit-font-smoothing:antialiased;
}
a{color:inherit;text-decoration:none}
img{max-width:100%;display:block}

/* ---- header ---- */
.topbar{
  position:sticky;top:0;z-index:20;background:var(--ink);color:var(--bone);
  display:flex;align-items:center;gap:20px;padding:14px 22px;
}
.nl-lockup{display:inline-flex;align-items:center;gap:10px}
.nl-mark{flex:none}
.nl-words{display:flex;flex-direction:column;line-height:1}
.nl-words b{font-family:Fraunces,Georgia,serif;font-weight:900;font-size:19px;letter-spacing:-.01em}
.nl-words i{font-style:normal;font-size:9.5px;letter-spacing:.34em;opacity:.75;margin-top:2px}

.shop-search{flex:1 1 auto;min-width:0;max-width:420px}
.shop-search input{
  width:100%;padding:9px 15px;border-radius:999px;border:1px solid #333c46;
  background:#1c222a;color:var(--bone);font-size:13.5px;font-family:inherit;
}
.shop-search input::placeholder{color:#7f8894}
.shop-search input:focus{outline:none;border-color:var(--brass)}
.topnav{display:flex;align-items:center;gap:18px;margin-left:auto;font-size:13.5px;flex:none}
.topnav a:hover{color:var(--brass-lit)}
.cart-pill{
  display:inline-flex;align-items:center;gap:7px;background:var(--brass);color:#1a1206;
  padding:7px 14px;border-radius:999px;font-weight:700;font-size:13px;
}
@media (max-width:720px){ .topnav .hide-sm{display:none} }

/* ---- layout ---- */
main{max-width:1180px;margin:0 auto;padding:32px 22px 90px}
.page-head{margin-bottom:26px}
h1{font-family:Fraunces,Georgia,serif;font-weight:900;font-size:clamp(28px,4.5vw,40px);
   line-height:1.05;margin:0 0 8px;letter-spacing:-.02em;text-wrap:balance}
.page-head p{margin:0;color:var(--muted);font-size:15px}
h2{font-family:Fraunces,Georgia,serif;font-weight:700;font-size:22px;margin:0 0 14px}

/* ---- product grid ---- */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:18px}
.card{
  background:var(--paper);border:1px solid var(--rule);border-radius:var(--radius);
  overflow:hidden;box-shadow:var(--shadow);display:flex;flex-direction:column;
  transition:transform .12s ease, box-shadow .12s ease;
}
.card:hover{transform:translateY(-2px);box-shadow:0 4px 10px rgba(20,24,29,.08),0 18px 40px rgba(20,24,29,.10)}
.card-art{aspect-ratio:5/7;background:#eee9e1;display:grid;place-items:center;overflow:hidden}
.card-art img{width:100%;height:100%;object-fit:contain}
.card-art .noart{color:var(--muted);font-size:12px}
.card-body{padding:12px 13px 14px;display:flex;flex-direction:column;gap:3px;flex:1}
.card-title{font-weight:600;font-size:14px;line-height:1.3}
.card-sub{font-size:12px;color:var(--muted)}
.card-price{font-family:Fraunces,Georgia,serif;font-weight:700;font-size:19px;margin-top:auto;padding-top:8px}
.tag{
  display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--teal);background:#e4f0ef;padding:2px 8px;border-radius:999px;
}
.tag.low{color:#8a4b12;background:#fbeedd}

/* ---- buttons ---- */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:8px;
  background:var(--ink);color:var(--bone);border:none;padding:12px 22px;border-radius:10px;
  font-weight:600;font-size:14.5px;font-family:inherit;cursor:pointer;
}
.btn:hover{background:var(--ink-2)}
.btn.brass{background:var(--brass);color:#1a1206}
.btn.brass:hover{background:var(--brass-lit)}
.btn.ghost{background:transparent;color:var(--ink);border:1px solid var(--rule)}
.btn:disabled{opacity:.45;cursor:not-allowed}

/* ---- panels / detail ---- */
.panel{background:var(--paper);border:1px solid var(--rule);border-radius:var(--radius);
       padding:22px;box-shadow:var(--shadow)}
.detail{display:grid;grid-template-columns:minmax(0,420px) minmax(0,1fr);gap:34px;align-items:start}
@media (max-width:780px){ .detail{grid-template-columns:1fr} }
.detail-art{background:var(--paper);border:1px solid var(--rule);border-radius:var(--radius);
            padding:18px;box-shadow:var(--shadow)}
.detail-price{font-family:Fraunces,Georgia,serif;font-weight:900;font-size:34px;margin:6px 0 4px}
.spec-list{list-style:none;margin:18px 0 0;padding:0;border-top:1px solid var(--rule)}
.spec-list li{display:flex;justify-content:space-between;gap:16px;padding:9px 0;
              border-bottom:1px solid var(--rule);font-size:14px}
.spec-list li span:first-child{color:var(--muted)}

/* ---- cart ---- */
.line{display:flex;gap:14px;align-items:center;padding:14px 0;border-bottom:1px solid var(--rule)}
.line:last-child{border-bottom:none}
.line-art{width:52px;aspect-ratio:5/7;background:#eee9e1;border-radius:6px;overflow:hidden;flex:none}
.line-art img{width:100%;height:100%;object-fit:contain}
.line-main{flex:1;min-width:0}
.line-title{font-weight:600;font-size:14px}
.totals{margin-top:18px;border-top:2px solid var(--ink);padding-top:14px}
.totals div{display:flex;justify-content:space-between;padding:5px 0;font-size:14.5px}
.totals .grand{font-family:Fraunces,Georgia,serif;font-weight:900;font-size:22px;padding-top:10px}
.totals .discount-row span:last-child{color:var(--teal)}
.discountpanel{margin-top:18px}
.discountlist{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:9px}
.discountlist li{display:flex;justify-content:space-between;gap:14px;font-size:14px}
.discountlist li span:last-child{color:var(--teal);font-weight:600;white-space:nowrap}
.discountlist li.nudge{color:var(--muted);font-style:italic}
.discountlist li.nudge span{color:inherit;font-weight:400}

.empty{text-align:center;padding:60px 20px;color:var(--muted)}
.empty .mark{opacity:.25;margin:0 auto 16px}
.muted{color:var(--muted);font-size:14px}
.notice{border-left:3px solid var(--brass);padding:10px 0 10px 15px;margin:16px 0;font-size:14px}

label.field{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--muted);margin-bottom:14px}
label.field input,label.field select,label.field textarea{
  border:1px solid var(--rule);border-radius:9px;padding:11px 13px;font-size:14.5px;
  font-family:inherit;color:var(--ink);background:var(--paper);
}
label.field input:focus,label.field select:focus,label.field textarea:focus{
  outline:none;border-color:var(--brass);box-shadow:0 0 0 3px rgba(179,133,47,.15)}

/* ---- side navigation drawer ---- */
.navtoggle{
  display:inline-grid;place-items:center;width:36px;height:36px;flex:none;cursor:pointer;
  background:transparent;border:1px solid #333c46;border-radius:9px;color:var(--bone);
}
.navtoggle:hover{border-color:var(--brass);color:var(--brass-lit)}
.navtoggle svg{width:17px;height:17px}

.scrim{
  position:fixed;inset:0;background:rgba(10,13,16,.5);opacity:0;pointer-events:none;
  transition:opacity .18s ease;z-index:28;
}
body.nav-open .scrim{opacity:1;pointer-events:auto}

.sidenav{
  position:fixed;top:0;left:0;bottom:0;width:272px;z-index:30;background:var(--ink);
  color:var(--bone);padding:18px 0 26px;overflow-y:auto;
  transform:translateX(-100%);transition:transform .2s ease;
  border-right:1px solid #262d35;
}
body.nav-open .sidenav{transform:none}
.sidenav-head{display:flex;align-items:center;justify-content:space-between;padding:0 18px 16px}
.sidenav-close{background:none;border:none;color:var(--bone);font-size:22px;line-height:1;cursor:pointer;padding:0 4px}
.sidenav-close:hover{color:var(--brass-lit)}
.card-art,.detail-art,.line-art{position:relative}
.art-tag{
  position:absolute;left:8px;bottom:8px;z-index:1;
  font-size:10.5px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;
  padding:3px 7px;border-radius:99px;pointer-events:none;
}
.art-tag.own{background:var(--teal);color:#eefaf8}
.art-tag.stock{background:rgba(20,24,29,.62);color:var(--bone)}
.detail-art .art-tag{left:12px;bottom:12px;font-size:11.5px;padding:4px 9px}
.line-art .art-tag{display:none}
.dept-grid{
  display:grid;gap:16px;margin:22px 0 34px;
  grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
}
.dept-card{
  display:flex;flex-direction:column;gap:8px;padding:22px;text-decoration:none;
  border:1px solid var(--rule);border-radius:14px;background:var(--paper);
  transition:border-color .15s ease,transform .15s ease;
}
.dept-card:hover{border-color:var(--brass);transform:translateY(-2px)}
.dept-card h2{margin:0;font-size:20px;color:var(--ink)}
.dept-card p{margin:0;font-size:14px;color:var(--muted);flex:1}
.dept-meta{
  display:flex;justify-content:space-between;gap:10px;font-size:13px;
  color:var(--brass);font-variant-numeric:tabular-nums;
}
.section-head{margin:30px 0 14px;font-size:19px;color:var(--ink)}
@media (prefers-reduced-motion:reduce){.dept-card{transition:none}}
.navgroup{padding:14px 0 4px}
.navgroup h4{
  font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:#7f8894;
  margin:0 0 8px;padding:0 18px;font-weight:700;
}
.sidenav a{
  display:flex;align-items:center;justify-content:space-between;gap:10px;
  padding:9px 18px;font-size:14px;color:#d8d2c7;
}
.sidenav a:hover{background:#1c222a;color:var(--brass-lit)}
.sidenav a .n{font-size:11.5px;color:#7f8894;font-variant-numeric:tabular-nums}
.sidenav a:hover .n{color:inherit}
.sidenav hr{border:none;border-top:1px solid #262d35;margin:12px 18px}

.filters{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end;margin-bottom:22px}
.filters .field{margin-bottom:0;flex:1 1 150px;min-width:120px}
.filters .btn{flex:none}
.bulk-art{background:linear-gradient(160deg,#20262d,#14181d);color:var(--bone)}
.bulk-art img{object-fit:contain;padding:22px}
.bulk-count{font-family:Fraunces,Georgia,serif;font-weight:900;font-size:44px;color:var(--brass)}
.bulk-count.small{font-size:16px;display:grid;place-items:center;height:100%;background:var(--ink);color:var(--brass)}
.bulk-count.big{font-family:Fraunces,Georgia,serif;font-weight:900;font-size:76px;color:var(--brass)}

footer{border-top:1px solid var(--rule);margin-top:70px;padding:26px 22px;color:var(--muted);font-size:13px}
footer .inner{max-width:1180px;margin:0 auto;display:flex;gap:18px;flex-wrap:wrap;justify-content:space-between}
</style>"""

FONTS = (
    "<link rel='preconnect' href='https://fonts.googleapis.com'>"
    "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
    "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?"
    "family=Fraunces:opsz,wght@9..144,700;9..144,900&family=Archivo:wght@400;500;600;700&display=swap'>"
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


def sidenav(links: str = "") -> str:
    """The slide-out navigation. Hidden by default and summoned by the
    toggle, rather than a permanent column: on a phone a fixed sidebar
    would eat half the screen, and on desktop the product grid is the
    thing people came for."""
    return (
        "<div class='scrim' data-nav-close></div>"
        "<aside class='sidenav' aria-label='Shop navigation'>"
        "<div class='sidenav-head'>"
        f"{logo_lockup(30)}"
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


def page(title: str, body: str, *, cart_count: int = 0, query: str = "",
         nav_links: str = "", dept_links: str = "", description: str = DEFAULT_DESCRIPTION) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title>"
        f"<meta name='description' content='{description}'>"
        f"<meta property='og:title' content='{title}'>"
        f"<meta property='og:description' content='{description}'>"
        "<meta property='og:type' content='website'>"
        "<meta property='og:site_name' content='NL Vault'>"
        "<meta name='twitter:card' content='summary'>"
        f"<meta name='theme-color' content='{BRASS}'>"
        f"{FONTS}{STYLE}</head><body>"
        f"{sidenav(nav_links)}"
        f"{header(cart_count, query, dept_links)}"
        f"<main>{body}</main>"
        "<footer><div class='inner'>"
        "<span>NL Vault — trading cards &amp; collectibles, shipped from Australia.</span>"
        "<span><a href='/shipping'>Shipping &amp; returns</a></span>"
        "</div></footer>"
        f"{NAV_SCRIPT}"
        "</body></html>"
    )
