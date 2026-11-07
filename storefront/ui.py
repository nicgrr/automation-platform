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
  --blue:#2f6fed; --blue-dark:#1e4fc0;
  --pink:#ff5d8f; --pink-dark:#e14577;
  --radius:16px; --radius-sm:10px;
}
*{box-sizing:border-box}
body{
  margin:0;background:var(--bone);color:var(--ink);position:relative;
  font-family:Nunito,ui-sans-serif,system-ui,-apple-system,sans-serif;
  line-height:1.55;-webkit-font-smoothing:antialiased;
}

/* Quiet colour at the page edges keeps the artwork and product photos prominent. */
body::before{
  content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:
    radial-gradient(700px 360px at 0% 0%,rgba(15,158,148,.07),transparent 72%),
    radial-gradient(700px 360px at 100% 0%,rgba(179,133,47,.09),transparent 72%);
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

.shop-search{flex:1 1 auto;min-width:0;max-width:360px;position:relative}
.shop-search input{
  width:100%;padding:11px 48px 11px 16px;border-radius:999px;border:1px solid #43464c;
  background:#22252b;color:#fff;font-size:13.5px;font-family:inherit;
}
.shop-search input::placeholder{color:#b1b4bb}
.shop-search input:focus{outline:none;border-color:var(--teal)}
.shop-search-submit{position:absolute;right:3px;top:3px;bottom:3px;width:38px;border:0;border-radius:50%;background:transparent;color:#eee;display:grid;place-items:center;cursor:pointer}
.shop-search-submit:hover{background:#3b3e45}
.shop-search-submit svg{width:18px;height:18px}
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
@media (max-width:1180px){ .topnav .hide-sm{display:none} }
@media (max-width:560px){
  .topbar{display:grid;grid-template-columns:44px 1fr auto;gap:10px 12px;padding:12px 18px}
  .shop-search{grid-column:1/-1;grid-row:2;max-width:none;width:100%}
  .shop-search input{font-size:16px;padding-block:10px}
  .topnav{font-size:14px;gap:12px}
}

/* ---- layout ---- */
main{max-width:1240px;margin:0 auto;padding:36px 22px 48px;scroll-margin-top:150px}
.page-head{margin-bottom:26px}
h1{font-family:'Baloo 2',sans-serif;font-weight:800;font-size:clamp(26px,4.2vw,38px);
   line-height:1.1;margin:0 0 8px;letter-spacing:-.01em;text-wrap:balance}
.page-head p{margin:0;color:var(--muted);font-size:15px}
h2{font-family:'Baloo 2',sans-serif;font-weight:700;font-size:21px;margin:0 0 14px;letter-spacing:-.005em}

/* ---- product grid ---- */
/* Two tiles per row up through tablet -- bigger, more readable cards on
   a phone -- widening to a normal responsive fill once there's room for
   more than two comfortably. */
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
@media (min-width:640px){
  .grid{grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:20px}
}
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
  transform:rotate(-45deg);background:#bd2858;color:#fff;
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
.btn.brass{background:var(--blue);color:#fff;border-color:var(--blue)}
.btn.brass:hover{background:var(--blue-dark);border-color:var(--blue-dark)}
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
  display:inline-grid;place-items:center;width:44px;height:44px;flex:none;cursor:pointer;
  background:#22252b;border:none;border-radius:999px;color:#fff;
}
.navtoggle:hover{background:var(--teal)}
.navtoggle svg{width:17px;height:17px}

.scrim{
  position:fixed;inset:0;background:rgba(21,23,26,.5);opacity:0;pointer-events:none;
  transition:opacity .18s ease;z-index:28;
}
body.nav-open .scrim{opacity:1;pointer-events:auto}
body.nav-open{overflow:hidden}

.sidenav{
  position:fixed;top:0;left:0;bottom:0;width:272px;z-index:30;background:var(--paper);
  color:var(--ink);padding:18px 0 26px;overflow-y:auto;
  transform:translateX(-100%);visibility:hidden;transition:transform .2s ease,visibility .2s ease;
}
body.nav-open .sidenav{transform:none;visibility:visible}
.sidenav-head{display:flex;align-items:center;justify-content:space-between;padding:0 18px 16px;border-bottom:1px solid var(--rule);margin-bottom:6px}
.sidenav-close{background:none;border:none;color:var(--ink);font-size:26px;line-height:1;cursor:pointer;width:44px;height:44px}
.sidenav-head .nl-words b,.sidenav-head .nl-words i{color:var(--ink)}
.sidenav-close:hover{color:var(--teal)}
.card-art,.detail-art,.line-art{position:relative}
.art-tag{
  position:absolute;left:8px;bottom:8px;z-index:1;
  font-family:Nunito,sans-serif;
  font-size:11px;font-weight:800;
  padding:4px 9px;border-radius:999px;pointer-events:none;
}
.art-tag.own{background:var(--teal-dark);color:#fff}
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
.seg-tab.active{background:var(--teal-dark);color:#fff;border-color:var(--teal-dark)}
.sub-tab{display:inline-block;padding:9px 16px;border-radius:999px;border:1.5px solid var(--rule);background:var(--paper);color:var(--muted);font-weight:700;font-size:14px;text-decoration:none}
.sub-tab:hover{border-color:var(--brass);color:var(--ink)}
.sub-tab.active{background:var(--ink);color:#fff;border-color:var(--ink)}
.dept-grid{
  display:grid;gap:18px;margin:24px 0 0;
  grid-template-columns:repeat(4,minmax(0,1fr));
}
.dept-card{
  display:flex;flex-direction:column;text-decoration:none;overflow:hidden;
  border:1px solid var(--rule);border-radius:var(--radius);background:var(--paper);
  box-shadow:0 1px 2px rgba(21,23,26,.04);
  transition:transform .15s ease,box-shadow .15s ease;
}
.dept-card:hover{transform:translateY(-4px);box-shadow:0 12px 26px rgba(21,23,26,.12)}
.dept-hero{aspect-ratio:16/10;background:var(--sunken);overflow:hidden;position:relative}
.soon-badge{display:inline-block;background:var(--ink);color:#fff;font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;padding:6px 12px;border-radius:999px}
.dept-hero .soon-badge{position:absolute;bottom:12px;left:12px}
.dept-card.is-soon img{filter:saturate(.55)}
.coming-soon{max-width:560px;margin:24px auto}
.coming-soon-art{aspect-ratio:16/10;overflow:hidden;border-radius:14px;margin-bottom:18px;background:var(--sunken)}
.coming-soon-art img{width:100%;height:100%;object-fit:cover}
.coming-soon h1{margin:12px 0 6px}
.dept-card[href='/shop/game-pokemon'] .dept-hero{background:#fff2c8}
.dept-card[href='/shop/game-one_piece'] .dept-hero{background:#e4eefb}
.dept-card[href='/shop/game-dragon_ball'] .dept-hero{background:#ffead7}
.dept-hero img{width:100%;height:100%;object-fit:contain}
.dept-hero img.dept-logo{padding:7% 9%;box-sizing:border-box}
.dept-hero img.dept-art{object-fit:cover}
.sealed-cover{width:100%;height:100%;box-sizing:border-box;padding:18px 16px 12px;background:linear-gradient(135deg,#133b3c,#256464);display:flex;flex-direction:column;gap:12px;overflow:hidden}
.sealed-cover-label{font-size:10px;font-weight:800;letter-spacing:1.5px;color:#f5e6bb;text-align:center}
.sealed-cover-products{display:flex;align-items:center;justify-content:center;gap:8px;flex:1;min-height:0}
.dept-hero .sealed-cover-products img{width:30%;height:90%;object-fit:contain;background:#fff;border-radius:8px;padding:4px;box-sizing:border-box;box-shadow:0 6px 14px #0003}
.sealed-cover-products img:first-child{transform:rotate(-7deg)}
.sealed-cover-products img:last-child{transform:rotate(7deg)}
.dept-hero .art-tag{left:12px;bottom:12px}
.dept-hero-mark{width:100%;height:100%;display:grid;place-items:center;color:var(--rule)}
.dept-card-body{display:flex;flex-direction:column;gap:8px;padding:20px;flex:1}
.dept-card h2,.dept-card h3{font-family:'Baloo 2',sans-serif;font-weight:700;margin:0;font-size:22px;line-height:1.2;color:var(--ink)}
.dept-card p{margin:0;font-size:14px;color:var(--muted);flex:1}
.dept-meta{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:8px;font-size:12px;color:var(--teal-dark);font-weight:800}
.dept-meta .arrow{font-size:20px;line-height:1}
.dept-card[href='/shop/bulk']{grid-column:1/-1;display:grid;grid-template-columns:170px 1fr;background:#f1f5ef}
.dept-card[href='/shop/bulk'] .dept-hero{aspect-ratio:auto;min-height:156px}
.dept-card[href='/shop/bulk'] .dept-card-body{padding:24px 28px;justify-content:center}
.dept-card[href='/shop/bulk'] .dept-card-body p{flex:none}
.dept-card[href='/shop/bulk'] .dept-meta{justify-content:flex-start;gap:14px}
.collections-head{display:flex;justify-content:space-between;align-items:flex-end;gap:20px}
.collections-head h2{font-size:clamp(28px,3vw,36px);line-height:1.15;margin:6px 0 0}
.collections-head p{font-size:14px;color:var(--muted);margin:10px 0 0}
.collections-head .view-all{font-weight:800;font-size:14px;color:var(--teal-dark);white-space:nowrap;padding:12px 0}
.section-kicker{font-size:11px;font-weight:900;letter-spacing:.13em;text-transform:uppercase;color:var(--teal-dark)}
.home-collections{scroll-margin-top:155px}
.how-to-shop{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px;margin-top:38px;padding:30px 0 0;border-top:1px solid var(--rule)}
.how-to-shop article{display:grid;grid-template-columns:30px 1fr;gap:12px;align-items:start}
.how-to-shop .step{font-size:12px;font-weight:900;color:var(--teal-dark);background:#e2f0e9;width:30px;height:30px;display:grid;place-items:center;border-radius:50%}
.how-to-shop h3{font-size:14px;margin:2px 0 5px;font-weight:900}
.how-to-shop p{font-size:13px;line-height:1.6;color:var(--muted);margin:0}
@media(max-width:850px){.dept-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:560px){
  main{padding:28px 18px 36px}
  .dept-grid{gap:12px;margin-top:20px}
  .dept-card-body{padding:14px 12px}
  .dept-card h2,.dept-card h3{font-size:19px}
  .dept-card:not([href='/shop/bulk']) p{display:none}
  .dept-meta{font-size:11px;margin-top:2px}
  .dept-hero img.dept-logo{padding:12% 9%}
  .dept-card[href='/shop/bulk']{grid-template-columns:110px 1fr}
  .dept-card[href='/shop/bulk'] .dept-hero{min-height:150px}
  .dept-card[href='/shop/bulk'] .dept-card-body{padding:18px 14px}
  .dept-card[href='/shop/bulk'] p{font-size:12px}
  .collections-head{align-items:flex-start;gap:10px}
  .collections-head .view-all{font-size:12px;padding-top:25px}
  .collections-head p{font-size:13px}
  .how-to-shop{grid-template-columns:1fr;gap:20px;margin-top:28px;padding-top:24px}
}
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
  font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);
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
.filter-panel{margin-bottom:22px}
.filter-panel summary{display:none;cursor:pointer;font-weight:800;font-size:14px;list-style:none}
.filter-panel summary::-webkit-details-marker{display:none}
.filter-panel summary span{color:var(--teal-dark);font-size:20px}
.filter-panel .filters{margin-bottom:0}
@media(max-width:640px){
  .filter-panel{border:1px solid var(--rule);border-radius:var(--radius);background:var(--paper)}
  .filter-panel summary{display:flex;justify-content:space-between;align-items:center;padding:15px 18px;min-height:54px}
  .filter-panel[open] summary span{transform:rotate(45deg)}
  .filter-panel .filters{border:0;border-top:1px solid var(--rule);border-radius:0 0 var(--radius) var(--radius);padding:18px}
  .filters input,.filters select{font-size:16px!important;min-width:0}
  .seg-tabs{gap:7px;margin-bottom:16px}
  .seg-tab,.sub-tab{font-size:12px;padding:10px 13px}
}
.bulk-art{background:var(--ink);color:#fff}
.bulk-art img{object-fit:contain;padding:22px}
.bulk-count{font-family:'Baloo 2',sans-serif;font-weight:800;font-size:40px;color:#fff}
.bulk-count.small{font-size:15px;display:grid;place-items:center;height:100%;background:var(--ink);color:#fff}
.bulk-count.big{font-family:'Baloo 2',sans-serif;font-weight:800;font-size:68px;color:var(--ink)}

footer{border-top:1px solid var(--rule);margin-top:12px;padding:30px 22px;color:var(--muted);font-size:13px;background:rgba(255,255,255,.7)}
footer{font-family:Nunito,sans-serif;font-weight:700}
footer .inner{max-width:1180px;margin:0 auto;display:flex;gap:18px;flex-wrap:wrap;justify-content:space-between;align-items:center}
footer a:hover{color:var(--teal-dark)}
.social-links{display:flex;gap:14px;align-items:center}
.social-link{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:13px;font-weight:700;min-height:44px;min-width:32px}
.social-link:hover{color:var(--teal-dark)}
.social-link svg,.social-link img{width:16px;height:16px;flex:none}
.social-link span{display:none}
@media (min-width:560px){.social-link span{display:inline}}
.social-icons{display:flex;gap:12px;margin-top:22px}
.social-icon{
  display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;
  border-radius:999px;background:var(--paper);border:2px solid var(--rule);color:var(--ink);
  transition:border-color .15s ease,transform .15s ease,color .15s ease;
}
.social-icon:hover{border-color:var(--teal);color:var(--teal-dark);transform:translateY(-2px)}
.social-icon svg,.social-icon img{width:19px;height:19px}
.social-icon img[src$='ebay-icon.png']{width:32px;height:auto}
@media (prefers-reduced-motion:reduce){.social-icon{transition:none}}


/* ---- home banner (the NL Vault artwork; its own logo is part of the image) ---- */
.hero-art{max-width:1196px;margin:26px auto 0;background:var(--ink);border-radius:22px;overflow:hidden;display:grid;grid-template-columns:1fr 1.45fr}
.hero-art picture{display:block;width:100%;height:100%;grid-column:2;grid-row:1}
.hero-art img{display:block;width:100%;height:100%;object-fit:cover;object-position:center}
.hero-art-bar{grid-column:1;grid-row:1;padding:42px 34px;color:#fff;display:flex;flex-direction:column;align-items:flex-start;gap:18px}
.hero-art-bar .hero-kicker{font-size:10px;font-weight:900;letter-spacing:.16em;color:#e8c77e;text-transform:uppercase}
.hero-art-bar .hero-title{font-size:clamp(38px,4.1vw,58px);font-weight:800;line-height:1.04;letter-spacing:-.03em;margin:0}
.hero-art-bar .hero-title span{color:#e8c77e;display:block}
.hero-art-bar p{margin:0;max-width:40ch;font-weight:600;font-size:14px;line-height:1.7;color:#d4d5d6}
.hero-art-bar .btn{background:#ebc674;border-color:#ebc674;color:#17191c;font-size:15px;padding:12px 22px}
.hero-art-bar .btn:hover{background:#f7d997;border-color:#f7d997}
.hero-art-bar .social-icons{margin-top:0;gap:9px}
.hero-art-bar .social-icon{width:40px;height:40px}
.hero-channels{display:flex;align-items:center;gap:14px}
.hero-channels>span{color:#b8bbbf;font-size:11px;font-weight:700}
@media(max-width:1240px){.hero-art{margin-inline:22px}}
@media(max-width:850px){
  .hero-art{grid-template-columns:1fr}
  .hero-art picture{grid-column:1;grid-row:1;height:auto}
  .hero-art img{height:auto;aspect-ratio:1533/647}
  .hero-art-bar{grid-row:2;padding:28px 30px;gap:15px}
  .hero-art-bar .hero-title{font-size:42px}
  .hero-art-bar .hero-title span{display:inline}
  .hero-art-bar p{max-width:56ch}
}
@media(max-width:560px){
  .hero-art{margin:18px 18px 0;border-radius:18px}
  .hero-art-bar{padding:22px;gap:12px}
  .hero-art-bar .hero-title{font-size:33px}
  .hero-art-bar p{font-size:13px;line-height:1.6}
  .hero-art-bar .btn{width:100%;min-height:48px}
  .hero-channels{display:none}
  .collections-head p{display:none}
  footer .social-link span{display:inline}
}
.skip-link{position:fixed;left:16px;top:-100px;z-index:60;background:#fff;color:var(--ink);padding:12px 18px;border-radius:10px;font-weight:800}
.skip-link:focus{top:12px}
:focus-visible{outline:3px solid #1566cb;outline-offset:4px}
.topbar :focus-visible,.hero-art :focus-visible{outline-color:#f1d48e}
@media(prefers-reduced-motion:reduce){
  .card,.dept-card,.btn,.social-icon,.sidenav,.scrim{transition:none}
  .card:hover,.dept-card:hover,.btn:hover,.social-icon:hover{transform:none}
}
.photo-back{position:absolute;right:10px;bottom:10px;width:74px;display:block;border:2px solid #fff;border-radius:8px;overflow:hidden;background:var(--sunken);box-shadow:0 2px 10px rgba(0,0,0,.35);text-decoration:none}
.photo-back img{display:block;width:100%;height:auto;object-fit:cover}
.photo-back span{position:absolute;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);color:#fff;font-size:10px;font-weight:700;text-align:center;padding:1px 0}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.buy-links .btn{text-decoration:none}
/* ---- home hero ----
   The wordmark/tagline block below still carries the brand on its own --
   no photography needed there. A full card showcase used to sit in *this*
   spot too and got pulled (see git history) for reading as clutter: a
   strip of small thumbnails competing with the wordmark for attention.
   What replaced it, 2026-09-10, isn't that -- two large, deliberate
   panes (trading cards / collectibles), not a strip, so it reads as one
   curated visual statement rather than more inventory to parse. */
/* The one spot on the page that IS meant to feel like the poster: same
   four hues, no new shapes (a prior full showcase here read as clutter
   and got pulled -- see the note above), just more of what the page
   background already does, closer and slightly bolder, so the wordmark
   sits on the most energetic point of the canvas rather than a plain
   fade. Layered over the page's own body::before rather than replacing
   it -- scroll past the hero and the same system continues underneath. */
.hero-banner{
  position:relative;
  background:
    radial-gradient(70% 130% at 14% 8%, rgba(255,93,143,.26), transparent 62%),
    radial-gradient(65% 120% at 86% 100%, rgba(47,111,237,.22), transparent 60%),
    radial-gradient(55% 100% at 50% 50%, rgba(15,158,148,.14), transparent 65%),
    linear-gradient(180deg, rgba(250,247,242,.4) 0%, var(--bone) 100%);
}
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

/* ---- home hero: trading cards / collectibles split ---- */
.hero-split{display:grid;grid-template-columns:1fr 1fr;gap:3px;background:var(--ink)}
.hero-pane{
  position:relative;display:block;overflow:hidden;isolation:isolate;
  min-height:min(58vh,520px);
}
.hero-pane-tcg{background:radial-gradient(120% 130% at 20% 15%,var(--brass-lit) 0%,#3a2a10 60%,var(--ink) 100%)}
.hero-pane-collect{background:radial-gradient(120% 130% at 80% 15%,var(--pink-dark) 0%,#3a1226 60%,var(--ink) 100%)}

.hero-pane-cards{position:absolute;inset:0;display:grid;place-items:center}
.hero-card{
  position:absolute;width:min(46%,260px);border-radius:14px;box-shadow:0 22px 50px rgba(0,0,0,.5);
  object-fit:cover;aspect-ratio:5/7;
}
.hero-card-0{transform:rotate(-9deg) translate(-38%,2%);z-index:1}
.hero-card-1{transform:rotate(7deg) translate(30%,-4%);z-index:2}
.hero-pane-tcg:hover .hero-card-0{transform:rotate(-13deg) translate(-42%,0)}
.hero-pane-tcg:hover .hero-card-1{transform:rotate(11deg) translate(34%,-8%)}

.hero-pane-photo{
  position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
  transition:transform .7s cubic-bezier(.2,.7,.2,1);
}
.hero-pane-collect:hover .hero-pane-photo{transform:scale(1.06)}

.hero-pane-scrim{
  position:absolute;inset:0;
  background:linear-gradient(180deg,rgba(0,0,0,0) 38%,rgba(0,0,0,.72) 100%);
}
.hero-pane-copy{
  position:absolute;left:0;right:0;bottom:0;padding:26px 28px 30px;color:#fff;
  display:flex;flex-direction:column;gap:6px;
}
.hero-eyebrow{
  font-family:'Baloo 2',sans-serif;font-weight:700;font-size:12px;letter-spacing:.14em;
  text-transform:uppercase;opacity:.85;
}
.hero-pane-copy h2{margin:0;font-family:'Baloo 2',sans-serif;font-weight:800;font-size:clamp(26px,4vw,38px)}
.hero-pane-cta{
  font-weight:800;font-size:14px;margin-top:4px;
  border-bottom:2px solid rgba(255,255,255,.5);padding-bottom:2px;width:fit-content;
  transition:border-color .2s ease;
}
.hero-pane:hover .hero-pane-cta{border-color:#fff}
.hero-pane-fallback .hero-pane-copy{position:static;padding:30px 28px;height:100%;justify-content:center}
.hero-pane-fallback .hero-pane-scrim{display:none}

@media (max-width:760px){
  .hero-split{grid-template-columns:1fr}
  .hero-pane{min-height:64vw}
}
@media (prefers-reduced-motion:reduce){
  .hero-card, .hero-pane-photo, .hero-pane-cta{transition:none}
}

/* ---- home backdrop: ambient card collage ----
   z-index:-1 only paints below body's own children (not below the page
   root) because body itself is position:relative above -- without that,
   a fixed negative-z-index box can render entirely behind body's own
   opaque background and disappear. The ::after wash paints last (after
   the <img> children, always, by spec) to fade the collage down to
   texture rather than a legible photo strip. */
.home-backdrop{position:fixed;inset:0;z-index:-1;overflow:hidden;pointer-events:none}
.home-backdrop{
  background:
    radial-gradient(55% 45% at 12% 8%,rgba(179,133,47,.32) 0%,transparent 60%),
    radial-gradient(50% 40% at 88% 14%,rgba(255,93,143,.26) 0%,transparent 60%),
    radial-gradient(60% 55% at 50% 100%,rgba(15,158,148,.22) 0%,transparent 65%),
    var(--paper);
}
.home-backdrop::after{content:'';position:absolute;inset:0;background:var(--paper);opacity:.6}
.backdrop-card{
  position:absolute;width:200px;aspect-ratio:5/7;object-fit:cover;border-radius:14px;
  opacity:.55;filter:saturate(1.05);box-shadow:0 18px 40px rgba(0,0,0,.10);
}
.bc-0{top:-6%;left:2%;transform:rotate(-13deg)}
.bc-1{top:6%;left:76%;transform:rotate(9deg)}
.bc-2{top:34%;left:-6%;transform:rotate(8deg)}
.bc-3{top:40%;right:-6%;transform:rotate(-7deg)}
.bc-4{top:68%;left:18%;transform:rotate(6deg)}
.bc-5{top:80%;right:8%;transform:rotate(-10deg)}
@media (max-width:760px){
  .backdrop-card{width:130px}
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
  var drawer = document.getElementById('shop-navigation');
  var toggle = document.querySelector('.navtoggle');
  var previousFocus = null;
  var background = [];
  function open(){
    if (body.classList.contains('nav-open')) return;
    previousFocus = document.activeElement;
    drawer.inert = false;
    drawer.setAttribute('aria-hidden','false');
    body.classList.add('nav-open');
    toggle.setAttribute('aria-expanded','true');
    background = Array.from(body.children).filter(function(el){
      return el !== drawer && !el.classList.contains('scrim') && el.tagName !== 'SCRIPT';
    }).map(function(el){ var state = {el:el,inert:el.inert}; el.inert = true; return state; });
    drawer.querySelector('[data-nav-close]').focus();
  }
  function close(){
    if (!body.classList.contains('nav-open')) return;
    body.classList.remove('nav-open');
    toggle.setAttribute('aria-expanded','false');
    background.forEach(function(state){ state.el.inert = state.inert; });
    if (previousFocus) previousFocus.focus({preventScroll:true});
    drawer.inert = true;
    drawer.setAttribute('aria-hidden','true');
  }
  document.querySelectorAll('[data-nav-open]').forEach(function(el){ el.addEventListener('click', open); });
  document.querySelectorAll('[data-nav-close]').forEach(function(el){ el.addEventListener('click', close); });
  document.addEventListener('keydown', function(e){
    if (!body.classList.contains('nav-open')) return;
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    if (e.key === 'Tab') {
      var controls = drawer.querySelectorAll('a[href],button:not([disabled]),[tabindex="0"]');
      var first = controls[0], last = controls[controls.length-1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
  var narrow = window.matchMedia('(max-width:640px)');
  var params = new URLSearchParams(window.location.search);
  var filtered = ['q','set','sort'].some(function(key){ return !!params.get(key); });
  document.querySelectorAll('.filter-panel').forEach(function(panel){
    function adapt(){ panel.open = !narrow.matches || filtered; }
    adapt();
    narrow.addEventListener('change',adapt);
  });
})();
</script>"""

# GSAP, loaded plain (no defer/async) so these two tags run to completion
# before the inline script right after them touches the `gsap` global --
# a deferred script and a following inline script do NOT execute in that
# order, so this is deliberate, not an oversight.
#
# Kept to what GSAP is actually good at and no further: a one-time
# entrance on the hero and its two showcase panes, a fade-up the first
# time a product tile scrolls into view, and a slight parallax drift on
# the hero panes' own images while they're on screen. No bounce, no
# looping, nothing that runs forever -- motion here is a courtesy, not a
# feature, and prefers-reduced-motion skips all of it.
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

  var panes = gsap.utils.toArray('.hero-pane');
  if (panes.length) {
    gsap.from(panes, {opacity: 0, y: 24, duration: 0.7, ease: 'power2.out', stagger: 0.12, delay: 0.15});
  }

  if (typeof ScrollTrigger !== 'undefined') {
    gsap.registerPlugin(ScrollTrigger);
    gsap.utils.toArray('.dept-card, .grid .card').forEach(function(el){
      gsap.from(el, {
        opacity: 0, y: 20, duration: 0.5, ease: 'power2.out',
        scrollTrigger: {trigger: el, start: 'top 92%', once: true},
      });
    });

    var heroSplit = document.querySelector('.hero-split');
    if (heroSplit) {
      gsap.to('.hero-pane-photo, .hero-pane-cards', {
        yPercent: 8, ease: 'none',
        scrollTrigger: {trigger: heroSplit, start: 'top top', end: 'bottom top', scrub: true},
      });
    }
  }
})();
</script>"""


def sidenav(links: str = "", show_cart: bool = True) -> str:
    """The slide-out navigation. Hidden by default and summoned by the
    toggle, rather than a permanent column: on a phone a fixed sidebar
    would eat half the screen, and on desktop the product grid is the
    thing people came for."""
    return (
        "<div class='scrim' data-nav-close></div>"
        "<aside class='sidenav' id='shop-navigation' role='dialog' aria-modal='true' "
        "aria-label='Shop navigation' aria-hidden='true' inert>"
        "<div class='sidenav-head'>"
        f"{logo_lockup()}"
        "<button class='sidenav-close' data-nav-close aria-label='Close navigation'>&times;</button>"
        "</div>"
        "<div class='navgroup'><h4>Shop</h4>"
        "<a href='/'>Home</a>"
        "<a href='/browse'>Browse everything</a>"
        + ("<a href='/bulk'>Bulk Master</a>" if show_cart else "<a href='/shop/bulk'>Bulk &amp; Bundles</a>")
        +
        "</div>"
        f"{links}"
        "<hr>"
        "<div class='navgroup'><h4>Info</h4>"
        "<a href='/shipping'>Shipping &amp; returns</a>"
        "<a href='/contact'>Contact us</a>"
        + ("<a href='/cart'>Your cart</a>" if show_cart else "")
        + "</div>"
        "</aside>"
    )


def header(cart_count: int = 0, query: str = "", dept_links: str = "", show_cart: bool = True) -> str:
    safe_query = escape(query, quote=True)
    badge = f"<span class='cart-pill'>Cart {cart_count}</span>" if cart_count else "<span class='cart-pill'>Cart</span>"
    return (
        "<header class='topbar'>"
        "<button class='navtoggle' data-nav-open aria-expanded='false' aria-controls='shop-navigation' aria-label='Open navigation'>"
        "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round'>"
        "<path d='M3 6h18M3 12h18M3 18h18'/></svg></button>"
        f"<a class='brand-link' href='/' aria-label='NL Vault home'>{logo_lockup()}</a>"
        "<form class='shop-search' method='get' action='/browse' role='search' aria-label='Search the vault'>"
        "<label class='sr-only' for='vault-search'>Search cards and collectables</label>"
        f"<input id='vault-search' type='search' name='q' value='{safe_query}' placeholder='Search the vault…' autocomplete='off'>"
        "<button class='shop-search-submit' type='submit' aria-label='Search'>"
        "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' aria-hidden='true'>"
        "<circle cx='10.5' cy='10.5' r='6.5'/><path d='m16 16 5 5'/></svg></button></form>"
        "<nav class='topnav' aria-label='Main navigation'><a class='browse-link' href='/browse'>Browse all</a>"
        # Departments come from stock rather than being hardcoded, for the
        # same reason the drawer's do: a header link is the most prominent
        # place to promise a section, and the worst place to land someone
        # on an empty page.
        f"{dept_links}"
        + ("<a href='/bulk' class='hide-sm'>Bulk Master</a>" if show_cart
           else "<a href='/shop/bulk' class='hide-sm'>Bulk &amp; Bundles</a>")
        +
        "<a href='/shipping' class='hide-sm'>Shipping</a>"
        + (f"<a href='/cart'>{badge}</a>" if show_cart else "")
        + "</nav></header>"
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
         crumbs: tuple[tuple[str, str], ...] = (), extra_jsonld: tuple[dict, ...] = (),
         show_cart: bool = True) -> str:
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
        "<a class='skip-link' href='#main-content'>Skip to content</a>"
        + ("<div class='announce'>Tracked shipping Australia-wide, every order &nbsp;·&nbsp; "
           "<a href='/shipping'>See rates</a></div>" if show_cart else "")
        + f"{sidenav(nav_links, show_cart)}"
        f"{header(cart_count, query, dept_links, show_cart)}"
        f"{hero}"
        f"<main id='main-content' tabindex='-1'>{_breadcrumb_nav(crumbs)}{body}</main>"
        "<footer><div class='inner'>"
        "<span>NL Vault — trading cards &amp; collectables, shipped from Australia.</span>"
        f"{social_html}"
        "<span><a href='/shipping'>Shipping &amp; returns</a> &nbsp;·&nbsp; "
        "<a href='/contact'>Contact us</a> &nbsp;·&nbsp; "
        "<a href='/privacy'>Privacy</a> &nbsp;·&nbsp; <a href='/terms'>Terms</a></span>"
        "</div></footer>"
        f"{NAV_SCRIPT}"
        f"{GSAP_SCRIPT}"
        "</body></html>"
    )
