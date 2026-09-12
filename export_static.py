#!/usr/bin/env python3
"""
export_static.py
Reads the most recent session file from ./sessions/ and generates public/index.html
Run this locally before deploying to Vercel:
    python3 export_static.py
"""
import json
import math
import pathlib
import re
import sys
from datetime import date

SESSIONS_DIR = pathlib.Path(__file__).parent / "sessions"
OUT = pathlib.Path(__file__).parent / "public" / "index.html"
OUT.parent.mkdir(exist_ok=True)

# Pick the most recently modified session
session_files = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
if not session_files:
    print("No session files found in ./sessions/  — log in locally first.")
    sys.exit(1)

store = json.loads(session_files[0].read_text())
data = store.get("data", {})
safe_days = store.get("safe_days", {})

overall_raw = data.get("TOTAL", "")
overall_pct_str = data.get("PERCENTAGE", "—")

def pct(raw):
    m = re.search(r'\((\d+(?:\.\d+)?)%\)', raw or "")
    if m: return float(m.group(1))
    m = re.search(r'(\d+(?:\.\d+)?)\s*%', raw or "")
    return float(m.group(1)) if m else None

def att_tot(raw):
    m = re.search(r'(\d+)\s*/\s*(\d+)', raw or "")
    return (int(m.group(1)), int(m.group(2))) if m else None

overall_p = pct(overall_pct_str)
total_pair = att_tot(overall_raw)
bunk_bank = 0
if total_pair:
    a, t = total_pair
    bunk_bank = max(0, math.floor(a / 0.75 - t))

subjects = {k: v for k, v in data.items()
            if k not in ("TOTAL", "PERCENTAGE") and isinstance(v, dict) and "raw" in v}

def subject_card(code, info):
    raw = info.get("raw", "")
    status = info.get("status", "")
    p = pct(raw)
    pair = att_tot(raw)
    if p is None:
        bar_w, tier, bar_col = 0, "UNKNOWN", "#888"
    else:
        bar_w = min(p, 100)
        if p < 75:
            tier, bar_col = "DEFICIT", "#ba1a1a"
        elif p <= 80:
            tier, bar_col = "CAUTION", "#f0c000"
        else:
            tier, bar_col = "SAFE", "#004d40"

    floor_txt = ""
    if pair:
        a2, t2 = pair
        floor_txt = f"Attended: {a2} / {t2} (Floor: {math.ceil(0.75*t2)}/{t2})"

    below = p is not None and p < 75
    card_border = "border-left: 8px solid #ba1a1a;" if below else ""
    card_bg = "background: #ffdad6;" if below else "background: #f5f5f5;"

    tier_colors = {"DEFICIT": "background:#ba1a1a;color:#fff",
                   "CAUTION": "background:#f0c000;color:#000",
                   "SAFE": "background:#00bfa5;color:#000",
                   "UNKNOWN": "background:#888;color:#fff"}
    tier_style = tier_colors.get(tier, "")

    return f"""
<div class="subject-card" style="{card_bg}{card_border}">
  <div class="subject-header">
    <span class="subject-code">{code.split()[0][:8]}</span>
    <span class="subject-name">{code}</span>
    <span class="tier-pill" style="{tier_style}">{tier}</span>
  </div>
  <div class="subject-stats">
    <span class="pct-big">{p if p is not None else '—'}%</span>
    <span class="raw-line">{raw}</span>
  </div>
  <div class="progress-bar-wrap">
    <div class="progress-bar-fill" style="width:{bar_w}%;background:{bar_col}"></div>
    <div class="progress-75-marker"></div>
  </div>
  <div class="subject-footer">{floor_txt}</div>
</div>"""

cards_html = "\n".join(subject_card(code, info) for code, info in subjects.items()
                       if info.get("status") != "N/A")

buf = overall_p - 75 if overall_p else 0
buf_sign = "+" if buf >= 0 else ""

generated_at = date.today().strftime("%d %b %Y")

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BUNKMETER v2.0 // TELEMETRY</title>
<meta name="description" content="BunkMeter — Christ University attendance tracker. 75% floor analysis."/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Inter:wght@400;600;700;900&display=swap" rel="stylesheet"/>
<style>
  :root{{
    --black:#0E0E0D;--white:#FAFAF8;--yellow:#F5E642;--cyan:#00E5CC;
    --magenta:#E040FB;--error:#ba1a1a;--error-c:#ffdad6;
    --gray:#E0E0E0;--dgray:#757575;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Inter',sans-serif;background:var(--white);color:var(--black);min-height:100vh}}
  /* TICKER */
  .ticker-wrap{{background:var(--yellow);border-bottom:3px solid var(--black);overflow:hidden;white-space:nowrap;padding:6px 0}}
  .ticker-inner{{display:inline-block;animation:ticker 25s linear infinite}}
  .ticker-inner span{{margin-right:80px;font-family:'Space Mono',monospace;font-size:.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase}}
  @keyframes ticker{{0%{{transform:translateX(0)}}100%{{transform:translateX(-50%)}}}}
  /* HEADER */
  header{{background:var(--white);border-bottom:4px solid var(--black);padding:12px 24px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100}}
  .logo{{font-family:'Space Mono',monospace;font-weight:700;font-size:1.1rem;text-transform:uppercase;background:var(--yellow);border:2px solid var(--black);padding:4px 10px;box-shadow:2px 2px 0 var(--black);text-decoration:none;color:var(--black)}}
  .cached-badge{{background:var(--error-c);border:2px solid var(--error);color:var(--error);font-size:.65rem;font-weight:700;font-family:'Space Mono',monospace;padding:3px 8px;text-transform:uppercase}}
  .header-date{{margin-left:auto;font-size:.7rem;font-family:'Space Mono',monospace;color:var(--dgray);text-transform:uppercase}}
  /* MAIN LAYOUT */
  main{{max-width:1100px;margin:0 auto;padding:32px 16px;display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start}}
  @media(max-width:700px){{main{{grid-template-columns:1fr}}}}
  /* PANEL */
  .panel{{border:4px solid var(--black);background:var(--white);box-shadow:6px 6px 0 var(--black);padding:20px}}
  .panel-label{{font-family:'Space Mono',monospace;font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;background:var(--gray);border-bottom:2px solid var(--black);margin:-20px -20px 16px;padding:8px 16px;display:flex;justify-content:space-between}}
  .panel-label span{{background:var(--black);color:var(--white);padding:1px 6px;font-size:.6rem}}
  /* AGGREGATE */
  .agg-pct{{font-family:'Space Mono',monospace;font-size:4rem;font-weight:700;line-height:1}}
  .agg-sub{{font-size:.7rem;text-transform:uppercase;color:var(--dgray);margin-top:2px;font-family:'Space Mono',monospace}}
  .agg-buffer{{background:var(--cyan);border:2px solid var(--black);padding:8px 12px;text-align:right;box-shadow:3px 3px 0 var(--black)}}
  .agg-buffer .buf-val{{font-family:'Space Mono',monospace;font-weight:700;font-size:.8rem}}
  .agg-buffer .buf-label{{font-size:.65rem;font-family:'Space Mono',monospace;text-transform:uppercase}}
  .hero-row{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px}}
  /* PROGRESS BAR */
  .prog-labels{{display:flex;justify-content:space-between;font-size:.6rem;font-family:'Space Mono',monospace;text-transform:uppercase;margin-bottom:4px}}
  .prog-labels .cutoff{{background:var(--black);color:var(--white);padding:0 4px}}
  .prog-track{{height:28px;background:var(--gray);border:2px solid var(--black);position:relative}}
  .prog-fill{{height:100%;border-right:2px solid var(--black);display:flex;align-items:center;justify-content:flex-end;padding-right:6px;font-size:.7rem;font-family:'Space Mono',monospace;font-weight:700;transition:width .6s ease}}
  .prog-75{{position:absolute;left:75%;top:0;bottom:0;width:2px;background:var(--black);z-index:2}}
  /* STAT CARDS */
  .stat-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-top:16px}}
  .stat-card{{border:2px solid var(--black);padding:10px;background:var(--white);box-shadow:3px 3px 0 var(--black)}}
  .stat-card.bank{{background:var(--magenta);color:var(--white)}}
  .stat-card .stat-val{{font-family:'Space Mono',monospace;font-size:1.4rem;font-weight:700}}
  .stat-card .stat-key{{font-size:.6rem;text-transform:uppercase;font-family:'Space Mono',monospace;opacity:.7;margin-bottom:2px}}
  .stat-card .stat-sub{{font-size:.55rem;text-transform:uppercase;font-family:'Space Mono',monospace;margin-top:3px}}
  /* SUBJECT CARDS */
  .ledger-panel{{grid-column:1/-1}}
  .subject-card{{border:2px solid var(--black);padding:14px;margin-bottom:10px;box-shadow:3px 3px 0 var(--black)}}
  .subject-header{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px}}
  .subject-code{{background:var(--black);color:var(--white);font-family:'Space Mono',monospace;font-size:.6rem;padding:2px 6px;font-weight:700}}
  .subject-name{{font-weight:700;font-size:.85rem;text-transform:uppercase;flex:1}}
  .tier-pill{{font-family:'Space Mono',monospace;font-size:.6rem;font-weight:700;padding:2px 8px;border:1px solid var(--black)}}
  .subject-stats{{display:flex;align-items:baseline;gap:16px;margin-bottom:8px}}
  .pct-big{{font-family:'Space Mono',monospace;font-size:1.6rem;font-weight:700}}
  .raw-line{{font-family:'Space Mono',monospace;font-size:.8rem;color:var(--dgray)}}
  .progress-bar-wrap{{height:10px;background:var(--gray);border:1px solid var(--black);position:relative;margin-bottom:6px}}
  .progress-bar-fill{{height:100%;transition:width .6s ease}}
  .progress-75-marker{{position:absolute;left:75%;top:0;bottom:0;width:2px;background:var(--black)}}
  .subject-footer{{font-size:.65rem;font-family:'Space Mono',monospace;color:var(--dgray)}}
  /* ADVICE */
  .advice-box{{background:#f0f0f0;border:2px solid var(--black);padding:10px;font-size:.75rem;margin-top:12px}}
  .advice-box strong{{text-transform:uppercase;font-family:'Space Mono',monospace;font-size:.65rem}}
  /* SEARCH */
  #search{{width:100%;border:2px solid var(--black);padding:8px 12px;font-family:'Space Mono',monospace;font-size:.8rem;background:var(--white);margin-bottom:14px;outline:none}}
  #search:focus{{border-color:var(--cyan);box-shadow:3px 3px 0 var(--cyan)}}
  /* FOOTER */
  footer{{text-align:center;font-family:'Space Mono',monospace;font-size:.6rem;color:var(--dgray);padding:24px;text-transform:uppercase;border-top:2px solid var(--black);margin-top:24px}}
</style>
</head>
<body>
<div class="ticker-wrap">
  <div class="ticker-inner">
    <span>⚡ BUNKMETER v2.0</span>
    <span>STATUS: {overall_pct_str}</span>
    <span>CHRIST UNIVERSITY · BTECH CSE</span>
    <span>75% FLOOR MANDATE</span>
    <span>CACHED DATA · GENERATED {generated_at}</span>
    <span>NO REGRETS POLICY</span>
    <span>⚡ BUNKMETER v2.0</span>
    <span>STATUS: {overall_pct_str}</span>
    <span>CHRIST UNIVERSITY · BTECH CSE</span>
    <span>75% FLOOR MANDATE</span>
    <span>CACHED DATA · GENERATED {generated_at}</span>
    <span>NO REGRETS POLICY</span>
  </div>
</div>

<header>
  <a class="logo" href="#">BUNKMETER_v2.0</a>
  <div class="cached-badge">⚠ CACHED DATA — {generated_at}</div>
  <div class="header-date">Christ University · B.Tech CSE</div>
</header>

<main>
  <!-- LEFT: AGGREGATE -->
  <div>
    <div class="panel">
      <div class="panel-label">▪ Aggregate Attendance Engine <span>CACHED</span></div>
      <div class="hero-row">
        <div>
          <div class="agg-sub">Net Semester Quotient</div>
          <div class="agg-pct">{overall_pct_str}</div>
        </div>
        <div class="agg-buffer">
          <div class="buf-val" id="buf-val">{buf_sign}{buf:.1f}% BUFFER</div>
          <div class="buf-label">{bunk_bank} PASSES LEFT</div>
        </div>
      </div>
      <div class="prog-labels">
        <span style="color:var(--error)">0% EXPELLED</span>
        <span class="cutoff">75% MIN CUTOFF</span>
        <span>100% NERD</span>
      </div>
      <div class="prog-track">
        <div class="prog-75"></div>
        <div class="prog-fill" id="prog-fill" style="width:{min(overall_p or 0,100)}%;background:{'#f0c000' if (overall_p or 0)<80 else '#004d40'}">{overall_pct_str}</div>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:.6rem;font-family:'Space Mono',monospace;text-transform:uppercase;margin-top:4px;color:var(--dgray)">
        <span>Critical Floor</span>
        <span>Buffer Headroom: {buf_sign}{buf:.1f}%</span>
        <span>Ceiling</span>
      </div>
      <div class="advice-box">
        <strong>⚠ Advisory Policy:</strong><br/>
        {'Academic headroom is optimal. Hall ticket clearance confirmed at current velocity.' if (overall_p or 0)>=80 else 'Headroom thin. One bad week drops you below the 75% floor. Pick safe days only.' if (overall_p or 0)>=75 else 'BELOW FLOOR. Attend everything until back above 75%.'}
      </div>
    </div>

    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-key">Conducted</div>
        <div class="stat-val">{total_pair[1] if total_pair else '—'}</div>
        <div class="stat-sub">Total Slots</div>
      </div>
      <div class="stat-card">
        <div class="stat-key">Attended</div>
        <div class="stat-val">{total_pair[0] if total_pair else '—'}</div>
        <div class="stat-sub">Verified Presence</div>
      </div>
      <div class="stat-card bank">
        <div class="stat-key">Bunk Bank</div>
        <div class="stat-val">{bunk_bank} LEC</div>
        <div class="stat-sub">Unspent Tokens</div>
      </div>
    </div>
  </div>

  <!-- RIGHT: VERDICT PLACEHOLDER -->
  <div class="panel" style="background:var(--white)">
    <div class="panel-label">▪ Tactical Directive <span>OFFLINE</span></div>
    <div style="font-family:'Space Mono',monospace;font-size:.7rem;text-transform:uppercase;margin-bottom:8px;opacity:.6">⊙ Verified</div>
    <h2 style="font-family:'Space Mono',monospace;font-size:1.6rem;font-weight:700;text-transform:uppercase;line-height:1.2;margin-bottom:12px">
      PICK A DAY.<br/>GET A VERDICT.
    </h2>
    <p style="font-size:.8rem;color:var(--dgray)">This is a static export. Real-time bunking simulation requires the full Flask app locally.</p>
    <div style="margin-top:16px;border:2px solid var(--black);padding:10px;background:#f9f9f9;font-family:'Space Mono',monospace;font-size:.7rem">
      <div style="font-weight:700;text-transform:uppercase;margin-bottom:4px">Quick Stats</div>
      <div>Overall: {overall_pct_str}</div>
      <div>Buffer above 75%: {buf_sign}{buf:.1f}%</div>
      <div>Safe to bunk: {bunk_bank} more lectures</div>
      <div>Generated: {generated_at}</div>
    </div>
  </div>

  <!-- ATTENDANCE LEDGER (full width) -->
  <div class="panel ledger-panel">
    <div class="panel-label">▪ Attendance Ledger // Courses <span>OFFLINE · {len(subjects)} SUBJECTS</span></div>
    <input id="search" type="text" placeholder="Search subject..." oninput="filterCards(this.value)"/>
    <div id="cards-container">
      {cards_html}
    </div>
  </div>
</main>

<footer>
  BunkMeter v2.0 · Static Export · Christ University · B.Tech CSE ·
  Data cached on {generated_at} · Real-time scraping requires local Flask app
</footer>

<script>
function filterCards(q) {{
  q = q.toLowerCase();
  document.querySelectorAll('.subject-card').forEach(function(c) {{
    c.style.display = (c.textContent.toLowerCase().includes(q)) ? '' : 'none';
  }});
}}
</script>
</body>
</html>"""

OUT.write_text(html, encoding="utf-8")
print(f"✅ Static export written to: {OUT}")
print(f"   Subjects: {len(subjects)}, Overall: {overall_pct_str}, Bank: {bunk_bank} lectures")
