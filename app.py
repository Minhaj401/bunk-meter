from flask import Flask, render_template, request, redirect, url_for, session
from bs4 import BeautifulSoup
import os

IS_VERCEL = os.environ.get("VERCEL") == "1"

if not IS_VERCEL:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
import calendar
import difflib
import json
import math

import pathlib
import re
import sqlite3
import traceback
import urllib.request
import uuid
from datetime import date, timedelta

app = Flask(__name__)
app.secret_key = "your_secret_key_here"

TIMETABLE_URL = "https://christ.etlab.app/student/timetable"
LOGIN_URL     = "https://christ.etlab.app/user/login"
ATTENDANCE_URL = "https://christ.etlab.app/ktuacademics/student/viewattendancesubject/25"

BASE_DIR  = pathlib.Path(__file__).parent
DB_PATH   = BASE_DIR / "bunkmaster.db"


if IS_VERCEL:
    STORE_DIR = pathlib.Path("/tmp/sessions")
else:
    STORE_DIR = BASE_DIR / "sessions"
STORE_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# SQLite helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_mappings (
                username    TEXT NOT NULL,
                att_header  TEXT NOT NULL,
                tt_key      TEXT NOT NULL,
                method      TEXT NOT NULL DEFAULT 'manual',
                PRIMARY KEY (username, att_header)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_timetables (
                username   TEXT PRIMARY KEY,
                timetable  TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_attendance (
                username    TEXT PRIMARY KEY,
                attendance  TEXT NOT NULL,
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        db.commit()

init_db()

def db_load_mappings(username):
    """Return {att_header: {key, method}} from SQLite for this user."""
    with get_db() as db:
        rows = db.execute(
            "SELECT att_header, tt_key, method FROM user_mappings WHERE username=?",
            (username,)
        ).fetchall()
    return {r["att_header"]: {"key": r["tt_key"], "method": r["method"]} for r in rows}

def db_save_mapping(username, att_header, tt_key, method="manual"):
    with get_db() as db:
        db.execute("""
            INSERT INTO user_mappings (username, att_header, tt_key, method)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(username, att_header) DO UPDATE SET tt_key=excluded.tt_key, method=excluded.method
        """, (username, att_header, tt_key, method))
        db.commit()

def db_delete_mapping(username, att_header):
    with get_db() as db:
        db.execute(
            "DELETE FROM user_mappings WHERE username=? AND att_header=?",
            (username, att_header)
        )
        db.commit()

def db_load_timetable(username):
    """Return parsed timetable dict from SQLite, or None."""
    with get_db() as db:
        row = db.execute(
            "SELECT timetable FROM user_timetables WHERE username=?",
            (username,)
        ).fetchone()
    if row:
        try:
            return json.loads(row["timetable"])
        except Exception:
            return None
    return None

def db_load_attendance(username):
    """Return parsed attendance dict from SQLite, or None."""
    with get_db() as db:
        row = db.execute(
            "SELECT attendance FROM user_attendance WHERE username=?",
            (username,)
        ).fetchone()
    if row:
        try:
            return json.loads(row["attendance"])
        except Exception:
            return None
    return None

def db_save_attendance(username, attendance):
    with get_db() as db:
        db.execute("""
            INSERT INTO user_attendance (username, attendance)
            VALUES (?, ?)
            ON CONFLICT(username) DO UPDATE SET attendance=excluded.attendance, updated_at=datetime('now')
        """, (username, json.dumps(attendance)))
        db.commit()

def db_save_timetable(username, timetable):
    with get_db() as db:
        db.execute("""
            INSERT INTO user_timetables (username, timetable, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(username) DO UPDATE SET timetable=excluded.timetable, updated_at=excluded.updated_at
        """, (username, json.dumps(timetable)))
        db.commit()

# ─────────────────────────────────────────────────────────────────────────────
# Server-side session store (avoids Flask's 4 KB cookie limit)
# ─────────────────────────────────────────────────────────────────────────────
def save_store(sid, payload):
    with open(STORE_DIR / f"{sid}.json", "w") as f:
        json.dump(payload, f)

def load_store(sid):
    if not sid:
        return {}
    path = STORE_DIR / f"{sid}.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)

# ─────────────────────────────────────────────────────────────────────────────
# Timetable Parser
# ─────────────────────────────────────────────────────────────────────────────
def parse_timetable(html):
    """Parse /student/timetable page into {Day: [entries]}."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#timetable table.items")
    if not table:
        raise ValueError("Timetable table not found in page")

    tt = {}
    for row in table.select("tbody tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        m_day = re.match(r"[A-Za-z]+", cells[0].get_text(strip=True))
        if not m_day:
            continue
        day = m_day.group(0).capitalize()

        entries = []
        for td in cells[1:]:
            lines = [s.strip() for s in td.stripped_strings if s.strip()]
            if not lines:
                continue
            first = lines[0]
            if first.lower() == "free period":
                continue
            if first == "TA":
                entries.append({"activity": " ".join(lines[1:]) or "Activity"})
                continue
            typ = next((l.strip("[] ") for l in lines if l.startswith("[")), None)
            faculty = None
            if len(lines) > 1 and not lines[-1].startswith("[") and lines[-1] != first:
                faculty = lines[-1]
            m_code = re.match(r"^([\w&]+)\s*-\s*(.+)$", first)
            if m_code:
                entries.append({"course_code": m_code.group(1), "course_name": m_code.group(2).strip(),
                                "type": typ, "faculty": faculty})
            else:
                entries.append({"course_name": first, "type": typ, "faculty": faculty})
        tt[day] = entries
    return tt

# ─────────────────────────────────────────────────────────────────────────────
# Subject Matching (regex → fuzzy → AI → manual)
# ─────────────────────────────────────────────────────────────────────────────
def entry_key(e):
    return e.get("course_code") or e.get("course_name")

def normalize(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def extract_code(s):
    m = re.search(r"\b([A-Z]{2,}\d+[A-Z0-9]*)\b", (s or "").upper())
    return m.group(1) if m else None

def timetable_subjects(timetable):
    seen, out = set(), []
    for classes in (timetable or {}).values():
        for cls in classes:
            if "course_code" not in cls and "course_name" not in cls:
                continue
            key = entry_key(cls)
            if key and key not in seen:
                seen.add(key)
                out.append(cls)
    return out

def match_subject(header, candidates):
    """Layers 1-2: exact code, then fuzzy name."""
    code = extract_code(header)
    if code:
        for c in candidates:
            if c.get("course_code") == code:
                return entry_key(c), "exact"
    hn = normalize(header)
    best, best_score = None, 0.0
    for c in candidates:
        for text in (c.get("course_code") or "", c.get("course_name") or ""):
            s = difflib.SequenceMatcher(None, hn, normalize(text)).ratio()
            if s > best_score:
                best, best_score = c, s
    if best is not None and best_score >= 0.8:
        return entry_key(best), "fuzzy"
    return None, None

def build_subject_map(attendance_data, timetable):
    """Auto-match all attendance headers. Returns (mapping, unmatched)."""
    candidates = timetable_subjects(timetable)
    mapping, unmatched = {}, []
    for header in attendance_data:
        if header in ("TOTAL", "PERCENTAGE"):
            continue
        key, method = match_subject(header, candidates)
        if key:
            mapping[header] = {"key": key, "method": method}
        else:
            unmatched.append(header)
    return mapping, unmatched

def _ai_prompt(headers, candidates):
    options = [{"key": entry_key(c), "name": c.get("course_name", "")} for c in candidates]
    return ("Map each attendance subject to the timetable subject key. "
            "Reply ONLY with a JSON object like {\"attendance_subject\": \"key\"}.\nAttendance: "
            + json.dumps(headers) + "\nTimetable: " + json.dumps(options))

def _ai_validate(content, headers, candidates):
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    raw = json.loads(content)
    valid = {entry_key(c) for c in candidates}
    return {h: k for h, k in raw.items() if h in headers and k in valid}

def groq_map_misses(headers, candidates):
    """Layer 3: ask Groq to map leftover headers."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key or not headers:
        return {}
    body = json.dumps({"model": "llama-3.1-8b-instant",
                       "messages": [{"role": "user", "content": _ai_prompt(headers, candidates)}],
                       "temperature": 0}).encode()
    try:
        req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions",
                                     data=body,
                                     headers={"Authorization": "Bearer " + api_key,
                                              "Content-Type": "application/json",
                                              "User-Agent": "BunkMeter/1.0",
                                              "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            content = json.loads(r.read())["choices"][0]["message"]["content"]
        return _ai_validate(content, headers, candidates)
    except Exception:
        return {}

def gemini_map_misses(headers, candidates):
    """Layer 3b: Gemini fallback for leftover headers."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not headers:
        return {}
    body = json.dumps({"contents": [{"parts": [{"text": _ai_prompt(headers, candidates)}]}],
                       "generationConfig": {"temperature": 0,
                                            "responseMimeType": "application/json"}}).encode()
    try:
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key=" + api_key,
            data=body,
            headers={"Content-Type": "application/json",
                     "User-Agent": "BunkMeter/1.0",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            content = json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"]
        return _ai_validate(content, headers, candidates)
    except Exception:
        return {}

def ai_map_misses(headers, candidates):
    """Layer 3: Groq first, Gemini fallback. Returns {header: (key, method)}."""
    out = {}
    for h, k in groq_map_misses(headers, candidates).items():
        out[h] = (k, "groq")
    rest = [h for h in headers if h not in out]
    for h, k in gemini_map_misses(rest, candidates).items():
        out[h] = (k, "gemini")
    return out

def resolve_map(attendance_data, timetable, subject_map):
    """Merge stored map with fresh auto-match for any new headers."""
    if not timetable:
        raise ValueError("No timetable loaded — log in again.")
    mapping, unmatched = build_subject_map(attendance_data, timetable)
    for header, m in (subject_map or {}).items():
        if isinstance(m, dict) and m.get("key"):
            mapping[header] = m
            if header in unmatched:
                unmatched.remove(header)
    return mapping, unmatched

def display_names(attendance_data, timetable, subject_map):
    """Map each attendance header to a friendly timetable course name."""
    try:
        mapping, _ = resolve_map(attendance_data, timetable, subject_map)
    except ValueError:
        return {}
    names = {}
    cands = {(entry_key(c) or ""): c for c in timetable_subjects(timetable)}
    for header in attendance_data:
        if header in ("TOTAL", "PERCENTAGE"):
            continue
        m = mapping.get(header)
        cand = cands.get(m.get("key")) if isinstance(m, dict) else None
        if cand and cand.get("course_name"):
            names[header] = cand["course_name"]
        else:
            names[header] = header
    return names

# ─────────────────────────────────────────────────────────────────────────────
# Attendance Parser
# ─────────────────────────────────────────────────────────────────────────────
def parse_subjectwise_attendance(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.items")
    if not table:
        raise ValueError("❌ Attendance table not found in page")

    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    subjects = headers[3:-2]

    row = table.select_one("tbody tr")
    cols = [td.get_text(strip=True) for td in row.find_all("td")]

    data = {}
    for subject, col in zip(subjects, cols[3:-2]):
        match = re.match(r"(\d+)/(\d+).*?\((\d+)%\)", col)
        if match:
            attended, total, percent = map(int, match.groups())
            if total == 0:
                status = "N/A"
            elif percent < 75:
                needed = math.ceil((0.75 * total - attended) / 0.25)
                status = f"⚠️ Need to attend {needed} more class(es)"
            else:
                bunkable = math.floor((attended / 0.75) - total)
                if bunkable < 0:
                    bunkable = 0
                status = f"✅ You can bunk {bunkable} class(es)"
        else:
            status = "N/A"
        data[subject] = {"raw": col, "status": status}

    data["TOTAL"] = cols[-2]
    data["PERCENTAGE"] = cols[-1]
    return data

# ─────────────────────────────────────────────────────────────────────────────
# Leave Impact Calculator
# ─────────────────────────────────────────────────────────────────────────────
def calculate_leave_impact(attendance_data, days, timetable=None, subject_map=None):
    if isinstance(days, str):
        days = [days]
    
    tt = timetable
    if not tt:
        raise ValueError("No timetable loaded — log in again.")
    
    valid_days = [d for d in days if d in tt]
    if not valid_days:
        return None

    mapping, _ = resolve_map(attendance_data, tt, subject_map)
    name_by_header = display_names(attendance_data, tt, subject_map)

    impact = {"day": ", ".join(valid_days), "is_allowed": True,
              "subjects_affected": [], "subjects_below_75": [], "overall_impact": {}}

    day_subjects = {}
    for day in valid_days:
        for cls in tt[day]:
            if "course_code" in cls or "course_name" in cls:
                key = entry_key(cls)
                day_subjects[key] = day_subjects.get(key, 0) + 1

    total_current_attended = total_current_classes = 0
    total_new_attended = total_new_classes = 0
    total_gain_attended = total_gain_classes = 0

    for key, classes_count in day_subjects.items():
        for att_subject, att_info in attendance_data.items():
            if att_subject in ["TOTAL", "PERCENTAGE"]:
                continue
            if isinstance(att_info, dict) and "raw" in att_info:
                if mapping.get(att_subject, {}).get("key") == key:
                    match = re.match(r"(\d+)/(\d+).*?\((\d+)%\)", att_info["raw"])
                    if match:
                        attended, total, percent = map(int, match.groups())
                        # --- BUNK scenario: attend nothing, total goes up ---
                        new_total    = total + classes_count
                        new_attended = attended
                        new_percent  = round((new_attended / new_total) * 100, 2) if new_total > 0 else 0
                        percent_loss = round(percent - new_percent, 2)
                        # --- GAIN scenario: attend everything, both go up ---
                        gain_total    = total + classes_count
                        gain_attended = attended + classes_count
                        gain_percent  = round((gain_attended / gain_total) * 100, 2) if gain_total > 0 else 0
                        percent_gain  = round(gain_percent - percent, 2)

                        subject_impact = {
                            "subject": name_by_header.get(att_subject, att_subject),
                            "current_attendance": f"{attended}/{total} ({percent}%)",
                            "after_leave": f"{new_attended}/{new_total} ({new_percent}%)",
                            "after_attend": f"{gain_attended}/{gain_total} ({gain_percent}%)",
                            "percent_loss": percent_loss,
                            "percent_gain": percent_gain,
                            "new_percent": new_percent,
                            "gain_percent": gain_percent,
                            "classes_missed": classes_count
                        }
                        if new_percent < 75:
                            impact["is_allowed"] = False
                            impact["subjects_below_75"].append(name_by_header.get(att_subject, att_subject))
                            subject_impact["below_threshold"] = True
                        else:
                            subject_impact["below_threshold"] = False
                        impact["subjects_affected"].append(subject_impact)
                        total_current_attended += attended
                        total_current_classes  += total
                        total_new_attended     += new_attended
                        total_new_classes      += new_total
                        total_gain_attended    += gain_attended
                        total_gain_classes     += gain_total

    if total_current_classes > 0:
        current_overall  = round((total_current_attended / total_current_classes) * 100, 2)
        new_overall      = round((total_new_attended / total_new_classes) * 100, 2) if total_new_classes > 0 else 0
        gain_overall     = round((total_gain_attended / total_gain_classes) * 100, 2) if total_gain_classes > 0 else 0
        impact["overall_impact"] = {
            "current":    current_overall,
            "after_leave": new_overall,
            "loss":        round(current_overall - new_overall, 2),
            "after_attend": gain_overall,
            "gain":        round(gain_overall - current_overall, 2),
        }

    return impact

# ─────────────────────────────────────────────────────────────────────────────
# Simulate Bunking
# ─────────────────────────────────────────────────────────────────────────────
def simulate_bunking(attendance_data, days_to_bunk, timetable=None, subject_map=None):
    tt = timetable
    if not tt:
        raise ValueError("No timetable loaded — log in again.")
    mapping, _ = resolve_map(attendance_data, tt, subject_map)
    simulated_data = {}

    for subject, info in attendance_data.items():
        if subject in ["TOTAL", "PERCENTAGE"]:
            simulated_data[subject] = info
            continue
        if isinstance(info, dict) and "raw" in info:
            simulated_data[subject] = info.copy()
        else:
            simulated_data[subject] = info

    classes_missed = {}
    for day in days_to_bunk:
        if day not in tt:
            continue
        for cls in tt[day]:
            if "course_code" in cls or "course_name" in cls:
                key = entry_key(cls)
                classes_missed[key] = classes_missed.get(key, 0) + 1

    for key, missed_count in classes_missed.items():
        for subject, info in simulated_data.items():
            if subject in ["TOTAL", "PERCENTAGE"]:
                continue
            if isinstance(info, dict) and "raw" in info:
                if mapping.get(subject, {}).get("key") == key:
                    match = re.match(r"(\d+)/(\d+).*?\((\d+)%\)", info["raw"])
                    if match:
                        attended, total, _ = map(int, match.groups())
                        new_total = total + missed_count
                        new_attended = attended
                        new_percent = round((new_attended / new_total) * 100) if new_total > 0 else 0
                        simulated_data[subject]["raw"] = f"{new_attended}/{new_total} ({new_percent}%)"
                        if new_percent < 75:
                            needed = math.ceil((0.75 * new_total - new_attended) / 0.25)
                            simulated_data[subject]["status"] = f"⚠️ Need to attend {needed} more class(es)"
                        else:
                            bunkable = math.floor((new_attended / 0.75) - new_total)
                            if bunkable < 0:
                                bunkable = 0
                            simulated_data[subject]["status"] = f"✅ You can bunk {bunkable} class(es)"

    return simulated_data

# ─────────────────────────────────────────────────────────────────────────────
# Safe Days Analyzer
# ─────────────────────────────────────────────────────────────────────────────
def analyze_safe_days(attendance_data, simulate_days=None, timetable=None, subject_map=None):
    tt = timetable
    if not tt:
        raise ValueError("No timetable loaded — log in again.")
    if simulate_days:
        attendance_data = simulate_bunking(attendance_data, simulate_days, tt, subject_map)
    mapping, _ = resolve_map(attendance_data, tt, subject_map)

    safe_days = {}
    for day, classes in tt.items():
        day_info = {"is_safe": True, "reason": [], "classes": [], "risky_subjects": []}

        day_subjects = {}
        for cls in classes:
            if "course_code" in cls or "course_name" in cls:
                key = entry_key(cls)
                name = cls.get("course_name") or key
                if key not in day_subjects:
                    day_subjects[key] = {"name": name, "count": 0}
                day_subjects[key]["count"] += 1

        for key, subject_info in day_subjects.items():
            classes_count = subject_info["count"]
            subject_name = subject_info["name"]
            day_info["classes"].append(f"{subject_name} ({classes_count}x)")

            for att_subject, att_info in attendance_data.items():
                if att_subject in ["TOTAL", "PERCENTAGE"]:
                    continue
                if isinstance(att_info, dict) and "raw" in att_info:
                    if mapping.get(att_subject, {}).get("key") == key:
                        match = re.match(r"(\d+)/(\d+).*?\((\d+)%\)", att_info["raw"])
                        if match:
                            attended, total, current_percent = map(int, match.groups())
                            new_total = total + classes_count
                            new_attended = attended
                            new_percent = (new_attended / new_total) * 100 if new_total > 0 else 0
                            if new_percent < 75:
                                day_info["is_safe"] = False
                                day_info["risky_subjects"].append(att_subject)
                                day_info["reason"].append(
                                    f"{att_subject}: Currently {current_percent}%, would drop to {new_percent:.1f}% (below 75%)"
                                )

        if day_info["is_safe"] and day_info["classes"]:
            day_info["reason"] = ["✅ All subjects will stay above 75% attendance"]
        elif not day_info["classes"]:
            day_info["reason"] = ["No classes scheduled"]

        safe_days[day] = day_info

    return safe_days

# ─────────────────────────────────────────────────────────────────────────────
# Forward Projection + Monthly Calendar
# ─────────────────────────────────────────────────────────────────────────────
def project_forward(attendance_data, timetable, subject_map, start, end):
    proj = {h: (dict(v) if isinstance(v, dict) else v) for h, v in attendance_data.items()}
    if start > end:
        return proj
    mapping, _ = resolve_map(attendance_data, timetable, subject_map)
    header_by_key = {}
    for h, m in mapping.items():
        header_by_key.setdefault(m["key"], h)
    counts = {}
    d = start
    while d <= end:
        for cls in (timetable or {}).get(d.strftime("%A"), []):
            if "course_code" in cls or "course_name" in cls:
                key = entry_key(cls)
                counts[key] = counts.get(key, 0) + 1
        d += timedelta(days=1)
    for key, n in counts.items():
        h = header_by_key.get(key)
        if not h or not isinstance(proj.get(h), dict):
            continue
        mth = re.match(r"(\d+)/(\d+)", proj[h].get("raw", ""))
        if not mth:
            continue
        a, t = int(mth.group(1)) + n, int(mth.group(2)) + n
        pct = round(a / t * 100) if t else 0
        proj[h]["raw"] = f"{a}/{t} ({pct}%)"
    return proj

def calculate_leave_on_date(attendance_data, date_str, timetable, subject_map):
    target = date.fromisoformat(date_str)
    today = date.today()
    if target < today:
        return None
    if target > today:
        attendance_data = project_forward(attendance_data, timetable, subject_map,
                                          today + timedelta(days=1), target - timedelta(days=1))
    wd = target.strftime("%A")
    impact = calculate_leave_impact(attendance_data, wd, timetable=timetable, subject_map=subject_map)
    if impact is None:
        return None
    impact["day"] = target.strftime("%d %b") + f" ({wd})"
    impact["date"] = date_str
    impact["projected"] = target > today
    return impact

def build_month(year, month, timetable, attendance_data=None, subject_map=None):
    weeks = []
    for week in calendar.monthcalendar(year, month):
        row = []
        for daynum in week:
            if not daynum:
                row.append(None)
                continue
            d = date(year, month, daynum)
            wd = d.strftime("%A")
            n = sum(1 for c in (timetable or {}).get(wd, [])
                    if "course_code" in c or "course_name" in c)
            cell = {"num": daynum, "iso": d.isoformat(), "weekday": wd,
                    "past": d < date.today(), "today": d == date.today(),
                    "has_classes": bool(n), "count": n, "allowed": None}
            if n and d >= date.today() and attendance_data is not None:
                imp = calculate_leave_on_date(attendance_data, d.isoformat(), timetable, subject_map)
                cell["allowed"] = imp["is_allowed"] if imp else None
            row.append(cell)
        weeks.append(row)
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return {"title": first.strftime("%B %Y"), "weeks": weeks,
            "prev": (first - timedelta(days=1)).strftime("%Y-%m"),
            "next": nxt.strftime("%Y-%m"), "current": first.strftime("%Y-%m")}

# ─────────────────────────────────────────────────────────────────────────────
# Selenium Scraper
# ─────────────────────────────────────────────────────────────────────────────
def _make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,1024")
    return webdriver.Chrome(options=opts)

def scrape_attendance_only(username, password):
    """Login and scrape only the attendance table. Returns attendance_data dict."""
    driver = _make_driver()
    try:
        driver.get(LOGIN_URL)
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "LoginForm_username"))
        )
        driver.find_element(By.ID, "LoginForm_username").send_keys(username)
        driver.find_element(By.ID, "LoginForm_password").send_keys(password)
        driver.find_element(By.NAME, "yt0").click()

        try:
            WebDriverWait(driver, 30).until(lambda d: "/user/login" not in d.current_url)
        except TimeoutException:
            err = ""
            for sel in (".errorMessage", ".error", "#LoginForm_username_em_", "#LoginForm_password_em_"):
                try:
                    t = driver.find_element(By.CSS_SELECTOR, sel).text.strip()
                    if t:
                        err = t
                        break
                except Exception:
                    pass
            raise ValueError("ETLAB rejected the login" + (f": {err}" if err else " — check your ID/password."))

        driver.get(ATTENDANCE_URL)
        try:
            WebDriverWait(driver, 45).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "table.items"))
            )
        except TimeoutException:
            raise ValueError(
                f"Logged in, but the attendance page did not load (landed on {driver.current_url}). Try again."
            )

        return parse_subjectwise_attendance(driver.page_source)
    finally:
        driver.quit()

def scrape_attendance_and_timetable(username, password):
    """Login and scrape both attendance and timetable. Returns (data, timetable)."""
    driver = _make_driver()
    try:
        driver.get(LOGIN_URL)
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "LoginForm_username"))
        )
        driver.find_element(By.ID, "LoginForm_username").send_keys(username)
        driver.find_element(By.ID, "LoginForm_password").send_keys(password)
        driver.find_element(By.NAME, "yt0").click()

        try:
            WebDriverWait(driver, 30).until(lambda d: "/user/login" not in d.current_url)
        except TimeoutException:
            err = ""
            for sel in (".errorMessage", ".error", "#LoginForm_username_em_", "#LoginForm_password_em_"):
                try:
                    t = driver.find_element(By.CSS_SELECTOR, sel).text.strip()
                    if t:
                        err = t
                        break
                except Exception:
                    pass
            raise ValueError("ETLAB rejected the login" + (f": {err}" if err else " — check your ID/password."))

        driver.get(ATTENDANCE_URL)
        try:
            WebDriverWait(driver, 45).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "table.items"))
            )
        except TimeoutException:
            raise ValueError(
                f"Logged in, but the attendance page did not load (landed on {driver.current_url}). Try again."
            )

        data = parse_subjectwise_attendance(driver.page_source)

        try:
            driver.get(TIMETABLE_URL)
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#timetable table.items"))
            )
            timetable = parse_timetable(driver.page_source)
        except Exception:
            raise ValueError("Logged in, but timetable page failed to load. Try again.")

        if not timetable:
            raise ValueError("Timetable came back empty. Try again.")

        return data, timetable
    finally:
        driver.quit()

# ─────────────────────────────────────────────────────────────────────────────
# Helper: build full mapping (auto + DB) and compute session store
# ─────────────────────────────────────────────────────────────────────────────
def _build_session(username, data, timetable):
    """
    Merge auto-matching with DB-stored manual mappings, run AI on leftovers,
    save any new AI matches to DB, compute safe_days, persist to server store.
    Returns (sid, mapping, unmatched).
    """
    # Start with auto-matching
    mapping, unmatched = build_subject_map(data, timetable)

    # Layer: DB-stored manual mappings override / fill in
    db_map = db_load_mappings(username)
    for header, m in db_map.items():
        if header in data and header not in ("TOTAL", "PERCENTAGE"):
            mapping[header] = m
            if header in unmatched:
                unmatched.remove(header)

    # Layer: AI for remaining unmatched
    still_unmatched = [h for h in unmatched if h not in mapping]
    for header, (key, method) in ai_map_misses(still_unmatched, timetable_subjects(timetable)).items():
        mapping[header] = {"key": key, "method": method}
        db_save_mapping(username, header, key, method)
        if header in unmatched:
            unmatched.remove(header)

    # Recompute final unmatched
    unmatched = [h for h in data if h not in ("TOTAL", "PERCENTAGE") and h not in mapping]

    safe_days = analyze_safe_days(data, timetable=timetable, subject_map=mapping)

    sid = str(uuid.uuid4())
    save_store(sid, {"data": data, "timetable": timetable, "safe_days": safe_days})

    return sid, mapping, unmatched

# ─────────────────────────────────────────────────────────────────────────────
# Flask Routes
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        try:
            if IS_VERCEL:
                # Vercel: no Chrome/Selenium available, go straight to DB cache
                raise RuntimeError("Vercel deployment — using cached data")

            # Check if timetable is cached in SQLite
            cached_tt = db_load_timetable(username)

            if cached_tt:
                # Only scrape attendance — saves ~30s
                data = scrape_attendance_only(username, password)
                timetable = cached_tt
                timetable_source = "cached"
            else:
                # First login: fetch both attendance and timetable
                data, timetable = scrape_attendance_and_timetable(username, password)
                db_save_timetable(username, timetable)
                timetable_source = "fresh"

            db_save_attendance(username, data)

            sid, mapping, unmatched = _build_session(username, data, timetable)

            session.clear()
            session["sid"]         = sid
            session["username"]    = username
            session["subject_map"] = mapping
            session["unmatched"]   = unmatched
            session["tt_source"]   = timetable_source
            session["is_cached"]   = False
            session.modified = True

            return redirect(url_for("dashboard"))

        except Exception as e:
            # Fallback to database
            cached_tt = db_load_timetable(username)
            cached_att = db_load_attendance(username)
            if cached_tt and cached_att:
                sid, mapping, unmatched = _build_session(username, cached_att, cached_tt)
                session.clear()
                session["sid"]         = sid
                session["username"]    = username
                session["subject_map"] = mapping
                session["unmatched"]   = unmatched
                session["tt_source"]   = "cached"
                session["is_cached"]   = True
                session.modified = True
                return redirect(url_for("dashboard"))
                
            if not IS_VERCEL and isinstance(e, TimeoutException):
                return render_template("login.html", error="ETLAB did not respond. It may be down — try again in a minute.")
            elif isinstance(e, ValueError):
                return render_template("login.html", error=str(e))
            elif IS_VERCEL:
                return render_template("login.html", error="Offline mode: no cached data found for this account. Log in via the local app first to populate the cache.")
            else:
                return f"<h1 style='color:red'>❌ Error:</h1><pre>{traceback.format_exc()}</pre>"

    return render_template("login.html")

def _get_ctx():
    """Load store + session into a single context dict for all routes."""
    store = load_store(session.get("sid"))
    return {
        "data":        store.get("data", {}),
        "timetable":   store.get("timetable"),
        "safe_days":   store.get("safe_days", {}),
        "subject_map": session.get("subject_map", {}),
        "unmatched":   session.get("unmatched", []),
        "username":    session.get("username", ""),
    }

@app.route("/dashboard")
def dashboard():
    ctx = _get_ctx()
    data, timetable, safe_days = ctx["data"], ctx["timetable"], ctx["safe_days"]
    subject_map = ctx["subject_map"]
    cal = None
    if timetable:
        try:
            y, m = map(int, request.args.get("month", date.today().strftime("%Y-%m")).split("-"))
            cal = build_month(y, m, timetable, data or None, subject_map)
        except ValueError:
            cal = build_month(date.today().year, date.today().month, timetable, data or None, subject_map)
    return render_template("dashboard.html",
                           data=data, safe_days=safe_days,
                           subject_map=subject_map,
                           display_names=display_names(data, timetable, subject_map),
                           unmatched=ctx["unmatched"],
                           timetable_subjects=timetable_subjects(timetable),
                           tt_source=session.get("tt_source", ""),
                           cal=cal, month_arg=request.args.get("month", ""))

@app.route("/confirm_mapping", methods=["POST"])
def confirm_mapping():
    """Save manual overrides to SQLite, then recompute."""
    ctx = _get_ctx()
    data, timetable, username = ctx["data"], ctx["timetable"], ctx["username"]
    if not data or not timetable:
        return redirect(url_for("login"))

    mapping = dict(ctx["subject_map"])  # mutable copy

    for header in request.form:
        if header in ("TOTAL", "PERCENTAGE"):
            continue
        key = request.form[header].strip()
        if key:
            mapping[header] = {"key": key, "method": "manual"}
            if username:
                db_save_mapping(username, header, key, "manual")
        # skip → do NOT delete existing mapping

    unmatched = [h for h in data if h not in ("TOTAL", "PERCENTAGE") and h not in mapping]

    # Recompute safe_days and update store
    sid = session.get("sid")
    store = load_store(sid)
    store["safe_days"] = analyze_safe_days(data, timetable=timetable, subject_map=mapping)
    save_store(sid, store)

    session["subject_map"] = mapping
    session["unmatched"]   = unmatched
    session.modified = True
    return redirect(url_for("dashboard"))

@app.route("/remove_mapping/<path:header>", methods=["POST"])
def remove_mapping(header):
    """Delete a specific mapping entry from DB + session."""
    ctx = _get_ctx()
    data, timetable, username = ctx["data"], ctx["timetable"], ctx["username"]
    if not data or not timetable:
        return redirect(url_for("login"))

    mapping = dict(ctx["subject_map"])
    if header in mapping:
        del mapping[header]
    if username:
        db_delete_mapping(username, header)

    unmatched = [h for h in data if h not in ("TOTAL", "PERCENTAGE") and h not in mapping]

    sid = session.get("sid")
    store = load_store(sid)
    store["safe_days"] = analyze_safe_days(data, timetable=timetable, subject_map=mapping)
    save_store(sid, store)

    session["subject_map"] = mapping
    session["unmatched"]   = unmatched
    session.modified = True
    return redirect(url_for("dashboard") + "#ledger")

@app.route("/refresh_timetable", methods=["POST"])
def refresh_timetable():
    """Force-refresh timetable from ETLAB (deletes cached copy first)."""
    username = session.get("username", "")
    password = request.form.get("password", "")
    if not username or not password:
        return redirect(url_for("dashboard"))
    try:
        data, timetable = scrape_attendance_and_timetable(username, password)
        db_save_timetable(username, timetable)
        sid, mapping, unmatched = _build_session(username, data, timetable)
        session["sid"]         = sid
        session["subject_map"] = mapping
        session["unmatched"]   = unmatched
        session["tt_source"]   = "fresh"
        session.modified = True
    except Exception:
        pass  # silently fall back
    return redirect(url_for("dashboard"))

@app.route("/simulate_dates", methods=["POST"])
def simulate_dates_route():
    ctx = _get_ctx()
    data, timetable = ctx["data"], ctx["timetable"]
    if not data or not timetable:
        return redirect(url_for("login"))

    dates_to_simulate = request.form.getlist("dates")
    month_arg = request.form.get("month_arg", "")
    
    subject_map = ctx["subject_map"]
    
    # We still need cal to render the calendar in the template
    try:
        y, m = map(int, month_arg.split("-")) if month_arg else (date.today().year, date.today().month)
        cal = build_month(y, m, timetable, data, subject_map)
    except Exception:
        cal = build_month(date.today().year, date.today().month, timetable, data, subject_map)
        
    if not dates_to_simulate:
        return render_template("dashboard.html",
                           data=data, safe_days=ctx["safe_days"],
                           subject_map=subject_map,
                           display_names=display_names(data, timetable, subject_map),
                           unmatched=ctx["unmatched"],
                           timetable_subjects=timetable_subjects(timetable),
                           cal=cal, month_arg=month_arg)

    if len(dates_to_simulate) == 1:
        impact = calculate_leave_on_date(data, dates_to_simulate[0], timetable, subject_map)
    else:
        weekdays = []
        for d_str in dates_to_simulate:
            try:
                dt = date.fromisoformat(d_str)
                weekdays.append(dt.strftime("%A"))
            except Exception:
                pass
        impact = calculate_leave_impact(data, weekdays, timetable=timetable, subject_map=subject_map)
        if impact:
            impact["projected"] = True

    return render_template("dashboard.html",
                           data=data, safe_days=ctx["safe_days"],
                           simulated_days=dates_to_simulate,
                           leave_impact=impact,
                           subject_map=subject_map,
                           display_names=display_names(data, timetable, subject_map),
                           unmatched=ctx["unmatched"],
                           timetable_subjects=timetable_subjects(timetable),
                           cal=cal, month_arg=month_arg)

@app.route("/calculate_leave/<day>")
def calculate_leave(day):
    ctx = _get_ctx()
    data, timetable = ctx["data"], ctx["timetable"]
    if not data:
        return redirect(url_for("login"))
    subject_map = ctx["subject_map"]
    impact    = calculate_leave_impact(data, day, timetable=timetable, subject_map=subject_map)
    safe_days = ctx["safe_days"]

    return render_template("dashboard.html", data=data, safe_days=safe_days,
                           leave_impact=impact, selected_day=day,
                           subject_map=subject_map,
                           display_names=display_names(data, timetable, subject_map),
                           unmatched=ctx["unmatched"],
                           timetable_subjects=timetable_subjects(timetable))

@app.route("/calculate_date/<date_str>")
def calculate_date(date_str):
    ctx = _get_ctx()
    data, timetable = ctx["data"], ctx["timetable"]
    if not data or not timetable:
        return redirect(url_for("login"))
    subject_map = ctx["subject_map"]
    try:
        impact = calculate_leave_on_date(data, date_str, timetable, subject_map)
    except ValueError:
        return redirect(url_for("dashboard"))
    if impact is None:
        return redirect(url_for("dashboard"))
    y, m = map(int, date_str.split("-")[:2])
    cal = build_month(y, m, timetable, data, subject_map)
    return render_template("dashboard.html", data=data, safe_days=ctx["safe_days"],
                           leave_impact=impact, selected_date=date_str,
                           subject_map=subject_map,
                           display_names=display_names(data, timetable, subject_map),
                           unmatched=ctx["unmatched"],
                           timetable_subjects=timetable_subjects(timetable),
                           cal=cal, month_arg=f"{y:04d}-{m:02d}")

if __name__ == "__main__":
    app.run(debug=True)
