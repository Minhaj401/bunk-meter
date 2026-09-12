from flask import Flask, render_template, request, redirect, url_for, session
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup
import calendar
import difflib
import json
import math
import os
import re
import traceback
import urllib.request
from datetime import date, timedelta

app = Flask(__name__)
app.secret_key = "your_secret_key_here"

TIMETABLE_URL = "https://christ.etlab.app/student/timetable"

# ---------- Timetable Parser (per-student, scraped) ----------
def parse_timetable(html):
    """Parse /student/timetable page into {Day: [entries]}.
    Entry: {course_code?, course_name, type?, faculty?} or {activity}.
    Free periods and TA activities (placement/library/gate) skipped."""
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

# ---------- Subject Matching (regex -> fuzzy -> Groq -> manual) ----------
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
    """Layers 1-2: exact code, then fuzzy name. Returns (key, method) or (None, None)."""
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

def groq_map_misses(headers, candidates):
    """Layer 3: ask Groq to map leftover headers. Returns {header: key}."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key or not headers:
        return {}
    options = [{"key": entry_key(c), "name": c.get("course_name", "")} for c in candidates]
    prompt = ("Map each attendance subject to the timetable subject key. "
              "Reply ONLY with a JSON object like {\"attendance_subject\": \"key\"}.\nAttendance: "
              + json.dumps(headers) + "\nTimetable: " + json.dumps(options))
    body = json.dumps({"model": "llama-3.1-8b-instant",
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0}).encode()
    try:
        req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions",
                                     data=body,
                                     headers={"Authorization": "Bearer " + api_key,
                                              "Content-Type": "application/json",
                                              "User-Agent": "BunkMaster/1.0",
                                              "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            content = json.loads(r.read())["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
        raw = json.loads(content)
        valid = {entry_key(c) for c in candidates}
        return {h: k for h, k in raw.items() if h in headers and k in valid}
    except Exception:
        return {}

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

# ---------- Attendance Parser ----------
def parse_subjectwise_attendance(html):
    soup = BeautifulSoup(html, "html.parser")

    table = soup.select_one("table.items")
    if not table:
        raise ValueError("❌ Attendance table not found in page")

    # Headers (subjects are between 4th and second-last)
    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    subjects = headers[3:-2]

    # First (and only) row of attendance data
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
                # classes needed to reach 75%
                needed = math.ceil((0.75 * total - attended) / 0.25)
                status = f"⚠️ Need to attend {needed} more class(es)"
            else:
                # max bunkable classes
                bunkable = math.floor((attended / 0.75) - total)
                if bunkable < 0:
                    bunkable = 0
                status = f"✅ You can bunk {bunkable} class(es)"
        else:
            status = "N/A"

        data[subject] = {"raw": col, "status": status}

    # Add totals from last two columns
    data["TOTAL"] = cols[-2]
    data["PERCENTAGE"] = cols[-1]

    return data

# ---------- Leave Impact Calculator ----------
def calculate_leave_impact(attendance_data, day, timetable=None, subject_map=None):
    """
    Calculate the impact of taking leave on a specific day
    Returns impact details including new percentages and allowed status
    """
    tt = timetable
    if not tt:
        raise ValueError("No timetable loaded — log in again.")
    if day not in tt:
        return None

    mapping, _ = resolve_map(attendance_data, tt, subject_map)

    impact = {
        "day": day,
        "is_allowed": True,
        "subjects_affected": [],
        "subjects_below_75": [],
        "overall_impact": {}
    }
    
    # Get classes for the day (grouped by subject key: code or name)
    day_classes = tt[day]
    day_subjects = {}

    for cls in day_classes:
        if "course_code" in cls or "course_name" in cls:
            key = entry_key(cls)
            day_subjects[key] = day_subjects.get(key, 0) + 1
    
    # Calculate impact for each subject
    total_current_attended = 0
    total_current_classes = 0
    total_new_attended = 0
    total_new_classes = 0
    
    for key, classes_count in day_subjects.items():
        # Find matching subject via resolved map (no substring hack)
        for att_subject, att_info in attendance_data.items():
            if att_subject in ["TOTAL", "PERCENTAGE"]:
                continue
            
            if isinstance(att_info, dict) and "raw" in att_info:
                # Check if mapped key matches
                if mapping.get(att_subject, {}).get("key") == key:
                    # Parse current attendance
                    match = re.match(r"(\d+)/(\d+).*?\((\d+)%\)", att_info["raw"])
                    if match:
                        attended, total, percent = map(int, match.groups())
                        
                        # Calculate new attendance after taking leave
                        new_total = total + classes_count
                        new_attended = attended  # Same attended, just more total classes
                        new_percent = round((new_attended / new_total) * 100, 2) if new_total > 0 else 0
                        percent_loss = round(percent - new_percent, 2)
                        
                        subject_impact = {
                            "subject": att_subject,
                            "current_attendance": f"{attended}/{total} ({percent}%)",
                            "after_leave": f"{new_attended}/{new_total} ({new_percent}%)",
                            "percent_loss": percent_loss,
                            "new_percent": new_percent,
                            "classes_missed": classes_count
                        }
                        
                        # Check if it will go below 75%
                        if new_percent < 75:
                            impact["is_allowed"] = False
                            impact["subjects_below_75"].append(att_subject)
                            subject_impact["below_threshold"] = True
                        else:
                            subject_impact["below_threshold"] = False
                        
                        impact["subjects_affected"].append(subject_impact)
                        
                        # Add to totals
                        total_current_attended += attended
                        total_current_classes += total
                        total_new_attended += new_attended
                        total_new_classes += new_total
    
    # Calculate overall impact
    if total_current_classes > 0:
        current_overall = round((total_current_attended / total_current_classes) * 100, 2)
        new_overall = round((total_new_attended / total_new_classes) * 100, 2) if total_new_classes > 0 else 0
        overall_loss = round(current_overall - new_overall, 2)
        
        impact["overall_impact"] = {
            "current": current_overall,
            "after_leave": new_overall,
            "loss": overall_loss
        }
    
    return impact

# ---------- Simulate Bunking Days ----------
def simulate_bunking(attendance_data, days_to_bunk, timetable=None, subject_map=None):
    """
    Simulate what attendance would be after bunking specified days
    Returns updated attendance data
    """
    tt = timetable
    if not tt:
        raise ValueError("No timetable loaded — log in again.")
    mapping, _ = resolve_map(attendance_data, tt, subject_map)
    simulated_data = {}
    
    # Deep copy the attendance data
    for subject, info in attendance_data.items():
        if subject in ["TOTAL", "PERCENTAGE"]:
            simulated_data[subject] = info
            continue
        
        if isinstance(info, dict) and "raw" in info:
            simulated_data[subject] = info.copy()
        else:
            simulated_data[subject] = info
    
    # Calculate total classes missed per subject
    classes_missed = {}
    for day in days_to_bunk:
        if day not in tt:
            continue
        
        for cls in tt[day]:
            if "course_code" in cls or "course_name" in cls:
                key = entry_key(cls)
                classes_missed[key] = classes_missed.get(key, 0) + 1
    
    # Update attendance data
    for key, missed_count in classes_missed.items():
        for subject, info in simulated_data.items():
            if subject in ["TOTAL", "PERCENTAGE"]:
                continue
            
            if isinstance(info, dict) and "raw" in info:
                if mapping.get(subject, {}).get("key") == key:
                    # Parse current attendance
                    match = re.match(r"(\d+)/(\d+).*?\((\d+)%\)", info["raw"])
                    if match:
                        attended, total, _ = map(int, match.groups())
                        
                        # Update with missed classes
                        new_total = total + missed_count
                        new_attended = attended  # Same attended
                        new_percent = round((new_attended / new_total) * 100) if new_total > 0 else 0
                        
                        # Update raw data
                        simulated_data[subject]["raw"] = f"{new_attended}/{new_total} ({new_percent}%)"
                        
                        # Update status
                        if new_percent < 75:
                            needed = math.ceil((0.75 * new_total - new_attended) / 0.25)
                            simulated_data[subject]["status"] = f"⚠️ Need to attend {needed} more class(es)"
                        else:
                            bunkable = math.floor((new_attended / 0.75) - new_total)
                            if bunkable < 0:
                                bunkable = 0
                            simulated_data[subject]["status"] = f"✅ You can bunk {bunkable} class(es)"
    
    return simulated_data

# ---------- Safe Days Analyzer ----------
def analyze_safe_days(attendance_data, simulate_days=None, timetable=None, subject_map=None):
    """
    Analyze which days are safe to bunk based on current attendance
    Returns a dict with day names and their safety status
    Each day is checked to ensure bunking won't drop ANY subject below 75%
    
    Args:
        attendance_data: Current attendance data
        simulate_days: Optional list of days to simulate bunking before checking safety
    """
    tt = timetable
    if not tt:
        raise ValueError("No timetable loaded — log in again.")
    # If simulating days, update attendance first
    if simulate_days:
        attendance_data = simulate_bunking(attendance_data, simulate_days, tt, subject_map)
    mapping, _ = resolve_map(attendance_data, tt, subject_map)

    safe_days = {}
    
    for day, classes in tt.items():
        day_info = {
            "is_safe": True,
            "reason": [],
            "classes": [],
            "risky_subjects": []
        }
        
        # Get unique subjects for this day
        day_subjects = {}
        for cls in classes:
            if "course_code" in cls or "course_name" in cls:
                key = entry_key(cls)
                name = cls.get("course_name") or key
                if key not in day_subjects:
                    day_subjects[key] = {"name": name, "count": 0}
                day_subjects[key]["count"] += 1
        
        # Check each subject's attendance - calculate if 75% will be maintained
        for key, subject_info in day_subjects.items():
            classes_count = subject_info["count"]
            subject_name = subject_info["name"]
            day_info["classes"].append(f"{subject_name} ({classes_count}x)")
            
            # Find matching subject via resolved map
            for att_subject, att_info in attendance_data.items():
                if att_subject in ["TOTAL", "PERCENTAGE"]:
                    continue
                    
                if isinstance(att_info, dict) and "raw" in att_info:
                    # Check if mapped key matches
                    if mapping.get(att_subject, {}).get("key") == key:
                        # Parse current attendance
                        match = re.match(r"(\d+)/(\d+).*?\((\d+)%\)", att_info["raw"])
                        if match:
                            attended, total, current_percent = map(int, match.groups())
                            
                            # Calculate what percentage would be AFTER bunking this day
                            new_total = total + classes_count
                            new_attended = attended  # Same attended (bunking means not attending)
                            new_percent = (new_attended / new_total) * 100 if new_total > 0 else 0
                            
                            # Check if it would drop below 75%
                            if new_percent < 75:
                                day_info["is_safe"] = False
                                day_info["risky_subjects"].append(att_subject)
                                day_info["reason"].append(
                                    f"{att_subject}: Currently {current_percent}%, would drop to {new_percent:.1f}% (below 75%)"
                                )
                            else:
                                # Even if safe, mention the impact
                                pass  # Only report issues, not safe subjects
        
        if day_info["is_safe"] and day_info["classes"]:
            day_info["reason"] = ["✅ All subjects will stay above 75% attendance"]
        elif not day_info["classes"]:
            day_info["reason"] = ["No classes scheduled"]
            
        safe_days[day] = day_info
    
    return safe_days

# ---------- Forward Projection + Monthly Calendar ----------
def project_forward(attendance_data, timetable, subject_map, start, end):
    """Assume every class from start to end (inclusive) attended. Returns updated copy."""
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
    """Leave impact on a calendar date. Intervening days assumed fully attended."""
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
    """Month grid for calendar. Each day: link/safety when projectable."""
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

# ---------- Scraper with Selenium ----------
def scrape_attendance(username, password):
    """Returns (attendance_data, timetable). Raises ValueError on any failure."""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    driver = webdriver.Chrome(options=chrome_options)

    try:
        driver.get("https://christ.etlab.app/user/login")

        # Fill login form
        driver.find_element(By.ID, "LoginForm_username").send_keys(username)
        driver.find_element(By.ID, "LoginForm_password").send_keys(password)
        driver.find_element(By.NAME, "yt0").click()

        # Go directly to attendance page
        driver.get("https://christ.etlab.app/ktuacademics/student/viewattendancesubject/25")

        # Wait for attendance table to load
        WebDriverWait(driver, 45).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.items"))
        )

        data = parse_subjectwise_attendance(driver.page_source)

        # Fetch per-student timetable with same session (no hardcoded fallback)
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

# ---------- Flask Routes ----------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        try:
            data, timetable = scrape_attendance(username, password)
            mapping, unmatched = build_subject_map(data, timetable)
            # Layer 3: Groq maps leftovers (misses only), cached in session
            for header, key in groq_map_misses(unmatched, timetable_subjects(timetable)).items():
                mapping[header] = {"key": key, "method": "groq"}
                unmatched.remove(header)
            safe_days = analyze_safe_days(data, timetable=timetable, subject_map=mapping)
            session["data"] = data
            session["timetable"] = timetable
            session["subject_map"] = mapping
            session["unmatched"] = unmatched
            session["safe_days"] = safe_days
            return redirect(url_for("dashboard"))
        except TimeoutException:
            return render_template("login.html", error="Login failed — wrong ID/password or attendance page timed out. Try again.")
        except ValueError as e:
            return render_template("login.html", error=str(e))
        except Exception:
            error_details = traceback.format_exc()
            return f"<h1 style='color:red'>❌ Error:</h1><pre>{error_details}</pre>"

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    data = session.get("data", {})
    safe_days = session.get("safe_days", {})
    timetable = session.get("timetable")
    subject_map = session.get("subject_map", {})
    cal = None
    if timetable:
        try:
            y, m = map(int, request.args.get("month", date.today().strftime("%Y-%m")).split("-"))
            cal = build_month(y, m, timetable, data or None, subject_map)
        except ValueError:
            cal = build_month(date.today().year, date.today().month, timetable, data or None, subject_map)
    return render_template("dashboard.html", data=data, safe_days=safe_days,
                           subject_map=subject_map,
                           unmatched=session.get("unmatched", []),
                           timetable_subjects=timetable_subjects(timetable),
                           cal=cal, month_arg=request.args.get("month", ""))

@app.route("/confirm_mapping", methods=["POST"])
def confirm_mapping():
    """Manual overrides for unmatched subjects, then recompute."""
    data = session.get("data", {})
    timetable = session.get("timetable")
    if not data or not timetable:
        return redirect(url_for("login"))
    mapping = session.get("subject_map", {})
    unmatched = []
    for header in request.form:
        if header in ("TOTAL", "PERCENTAGE"):
            continue
        key = request.form[header].strip()
        if key:
            mapping[header] = {"key": key, "method": "manual"}
        elif header in mapping:
            del mapping[header]
    for header in data:
        if header not in ("TOTAL", "PERCENTAGE") and header not in mapping:
            unmatched.append(header)
    session["subject_map"] = mapping
    session["unmatched"] = unmatched
    session["safe_days"] = analyze_safe_days(data, timetable=timetable, subject_map=mapping)
    return redirect(url_for("dashboard"))

@app.route("/simulate_bunking", methods=["POST"])
def simulate_bunking_route():
    data = session.get("data", {})
    if not data:
        return redirect(url_for("login"))
    
    # Get days to simulate from form
    days_to_simulate = request.form.getlist("days")  # e.g., ["Wednesday", "Thursday"]
    
    if not days_to_simulate:
        return redirect(url_for("dashboard"))

    timetable = session.get("timetable")
    subject_map = session.get("subject_map", {})

    # Calculate safe days after simulating those bunks
    simulated_safe_days = analyze_safe_days(data, simulate_days=days_to_simulate,
                                            timetable=timetable, subject_map=subject_map)
    simulated_attendance = simulate_bunking(data, days_to_simulate,
                                            timetable=timetable, subject_map=subject_map)
    
    return render_template("dashboard.html", 
                         data=data, 
                         safe_days=simulated_safe_days,
                         simulated_attendance=simulated_attendance,
                         simulated_days=days_to_simulate,
                         subject_map=subject_map,
                         unmatched=session.get("unmatched", []),
                         timetable_subjects=timetable_subjects(timetable))

@app.route("/calculate_leave/<day>")
def calculate_leave(day):
    data = session.get("data", {})
    if not data:
        return redirect(url_for("login"))
    
    impact = calculate_leave_impact(data, day, timetable=session.get("timetable"),
                                    subject_map=session.get("subject_map", {}))
    safe_days = session.get("safe_days", {})
    
    return render_template("dashboard.html", data=data, safe_days=safe_days, leave_impact=impact, selected_day=day,
                           subject_map=session.get("subject_map", {}),
                           unmatched=session.get("unmatched", []),
                           timetable_subjects=timetable_subjects(session.get("timetable")))

@app.route("/calculate_date/<date_str>")
def calculate_date(date_str):
    data = session.get("data", {})
    timetable = session.get("timetable")
    if not data or not timetable:
        return redirect(url_for("login"))
    try:
        impact = calculate_leave_on_date(data, date_str, timetable, session.get("subject_map", {}))
    except ValueError:
        return redirect(url_for("dashboard"))
    if impact is None:
        return redirect(url_for("dashboard"))
    y, m = map(int, date_str.split("-")[:2])
    cal = build_month(y, m, timetable, data, session.get("subject_map", {}))
    return render_template("dashboard.html", data=data, safe_days=session.get("safe_days", {}),
                           leave_impact=impact, selected_date=date_str,
                           subject_map=session.get("subject_map", {}),
                           unmatched=session.get("unmatched", []),
                           timetable_subjects=timetable_subjects(timetable),
                           cal=cal, month_arg=f"{y:04d}-{m:02d}")

if __name__ == "__main__":
    app.run(debug=True)
