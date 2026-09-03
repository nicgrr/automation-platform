"""Shared page shell and styling for the EzBay dashboard. Plain strings, no
template engine or external assets -- keeps this private admin tool
self-contained (no CDN fonts/scripts) and consistent with the rest of this
codebase's no-framework-magic style.
"""

STYLE = """<style>
:root {
  --bg: #05070d;
  --panel: #0d1220;
  --panel-border: rgba(94, 234, 255, 0.15);
  --accent: #5eeaff;
  --accent-2: #a78bfa;
  --text: #e6f1ff;
  --text-dim: #7d90ac;
  --success: #34d399;
  --danger: #f87171;
  --radius: 14px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background:
    radial-gradient(circle at 20% -10%, rgba(94, 234, 255, 0.08), transparent 40%),
    radial-gradient(circle at 90% 10%, rgba(167, 139, 250, 0.10), transparent 45%),
    var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, ui-sans-serif, sans-serif;
  line-height: 1.5;
  min-height: 100vh;
}
main { max-width: 1080px; margin: 0 auto; padding: 40px 24px 80px; }
.brand { display: flex; align-items: baseline; gap: 12px; margin-bottom: 4px; }
.brand h1 {
  font-size: 30px; margin: 0; letter-spacing: 0.01em; font-weight: 700;
  background: linear-gradient(120deg, var(--accent), var(--accent-2));
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.brand .tag { color: var(--text-dim); font-size: 12px; text-transform: uppercase; letter-spacing: 0.14em; }
.brand-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 4px; }
.brand-row .brand { margin-bottom: 0; }
.subtitle { color: var(--text-dim); margin: 0 0 32px; font-size: 14px; }

.stat-group { margin-bottom: 28px; }
.stat-group:last-of-type { margin-bottom: 32px; }
.group-label { font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.12em; margin: 0 0 12px; }

.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 32px; }
.stat-group .stat-grid { margin-bottom: 0; }
.stat-card { background: var(--panel); border: 1px solid var(--panel-border); border-radius: var(--radius); padding: 18px 20px; position: relative; overflow: hidden; }
.stat-card::before { content: ""; position: absolute; inset: 0; background: linear-gradient(135deg, rgba(94, 234, 255, 0.06), transparent 60%); pointer-events: none; }
.stat-card .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-dim); margin-bottom: 8px; }
.stat-card .value { font-size: 20px; font-weight: 600; }
.stat-card .value .value-sub { font-size: 13px; font-weight: 500; color: var(--text-dim); }

.pill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 11px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.pill.ok { background: rgba(52, 211, 153, 0.12); color: var(--success); }
.pill.bad { background: rgba(248, 113, 113, 0.12); color: var(--danger); }
.pill.neutral { background: rgba(94, 234, 255, 0.10); color: var(--accent); }

.panel { background: var(--panel); border: 1px solid var(--panel-border); border-radius: var(--radius); padding: 24px; margin-bottom: 24px; }
.panel h2 { margin: 0 0 16px; font-size: 15px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-dim); display: flex; align-items: center; gap: 8px; }
.panel h2::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 10px var(--accent); flex: none; }
.panel h3 { font-size: 12px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.08em; margin: 22px 0 10px; }
.panel h3:first-of-type { margin-top: 0; }
.panel > p { color: var(--text-dim); font-size: 13px; }

.btn { display: inline-flex; align-items: center; gap: 8px; background: linear-gradient(120deg, var(--accent), var(--accent-2)); color: #04101a; font-weight: 700; padding: 10px 20px; border-radius: 10px; text-decoration: none; font-size: 14px; box-shadow: 0 0 24px rgba(94, 234, 255, 0.25); }
.btn:hover { filter: brightness(1.08); }

.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; white-space: nowrap; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--panel-border); }
th { color: var(--text-dim); font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.06em; }
tr:hover td { background: rgba(94, 234, 255, 0.04); }

ul.events { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
ul.events li { font-size: 13px; color: var(--text-dim); border-left: 2px solid var(--panel-border); padding-left: 10px; }

details { background: #0a0f1c; border: 1px solid var(--panel-border); border-radius: 10px; padding: 10px 14px; }
details summary { cursor: pointer; font-size: 13px; list-style: revert; }
details summary::marker { color: var(--accent); }

form { display: flex; flex-direction: column; gap: 14px; max-width: 320px; }
label { font-size: 13px; color: var(--text-dim); display: flex; flex-direction: column; gap: 6px; }
input { background: #0a0f1c; border: 1px solid var(--panel-border); border-radius: 8px; padding: 10px 12px; color: var(--text); font-size: 14px; }
input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(94, 234, 255, 0.15); }
button { background: linear-gradient(120deg, var(--accent), var(--accent-2)); border: none; padding: 11px; border-radius: 8px; color: #04101a; font-weight: 700; cursor: pointer; font-size: 14px; }
</style>"""


def page(title: str, body: str, head_extra: str = "") -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title>{STYLE}{head_extra}</head><body><main>{body}</main></body></html>"
    )


def pill(text: str, kind: str = "neutral") -> str:
    return f"<span class='pill {kind}'>{text}</span>"


def brand_header(tagline: str) -> str:
    return f"<div class='brand'><h1>EzBay</h1><span class='tag'>{tagline}</span></div>"
