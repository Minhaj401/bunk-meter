from flask import Flask, render_template, request, redirect, url_for, session
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import math
import re
import traceback

app = Flask(__name__)
app.secret_key = "your_secret_key_here"

# ---------- Timetable Data ----------
TIMETABLE = {
    "Monday": [
        {"course_code": "PBCST404", "course_name": "Computer Organization and Architecture", "type": "Theory", "faculty": "Vineetha K V"},
        {"course_code": "PCCSL408", "course_name": "Database Management Systems lab", "type": "Lab", "faculty": None},
        {"course_code": "PCCSL408", "course_name": "Database Management Systems lab", "type": "Lab", "faculty": None},
        {"course_code": "PCCSL408", "course_name": "Database Management Systems lab", "type": "Lab", "faculty": None},
        {"course_code": "PCCST402", "course_name": "Database Management Systems", "type": "Theory", "faculty": "Ms. Jincy Denny"},
        {"course_code": "GAMAT401", "course_name": "Mathematics for Information Science-4", "type": "Theory", "faculty": "Ms. Neethu K"},
        {"activity": "Sports", "faculty": "SPT24"}
    ],
    "Tuesday": [
        {"course_code": "PBCST404", "course_name": "Computer Organization and Architecture", "type": "Theory", "faculty": "Vineetha K V"},
        {"course_code": "PCCST403", "course_name": "Operating Systems", "type": "Theory", "faculty": "Bhagyasree P V"},
        {"course_code": "GAMAT401", "course_name": "Mathematics for Information Science-4", "type": "Theory", "faculty": "Ms. Neethu K"},
        {"course_code": "PBCST404", "course_name": "Computer Organization and Architecture", "type": "Theory", "faculty": "Vineetha K V"},
        {"course_code": "PECST411", "course_name": "Software Engineering", "type": "Theory", "faculty": "Vaishak C Krishnan"}
    ],
    "Wednesday": [
        {"course_code": "UCHUT347", "course_name": "Engineering Ethics and Sustainable Development", "type": "Theory", "faculty": "Athithya S"},
        {"course_code": "PCCST403", "course_name": "Operating Systems", "type": "Theory", "faculty": "Bhagyasree P V"},
        {"course_code": "PECST411", "course_name": "Software Engineering", "type": "Theory", "faculty": "Vaishak C Krishnan"},
        {"course_code": "PCCST402", "course_name": "Database Management Systems", "type": "Theory", "faculty": "Ms. Jincy Denny"},
        {"course_code": "GAMAT401", "course_name": "Mathematics for Information Science-4", "type": "Theory", "faculty": "Ms. Neethu K"},
        {"course_code": "PCCST402", "course_name": "Database Management Systems", "type": "Theory", "faculty": "Ms. Jincy Denny"},
        {"activity": "Library", "faculty": "LIB24"}
    ],
    "Thursday": [
        {"course_code": "PCCST403", "course_name": "Operating Systems", "type": "Theory", "faculty": "Bhagyasree P V"},
        {"course_code": "PCCSL407", "course_name": "Operating Systems lab", "type": "Lab", "faculty": None},
        {"course_code": "PCCSL407", "course_name": "Operating Systems lab", "type": "Lab", "faculty": None},
        {"course_code": "PCCSL407", "course_name": "Operating Systems lab", "type": "Lab", "faculty": None},
        {"course_code": "PCCST403", "course_name": "Operating Systems ", "type": "Theory", "faculty": "Bhagyasree P V"}
    ],
    "Friday": [
        {"course_code": "PCCST403", "course_name": "Operating Systems", "type": "Theory", "faculty": "Bhagyasree P V"},
        {"course_code": "GAMAT401", "course_name": "Mathematics for Information Science-4", "type": "Theory", "faculty": "Ms. Neethu K"},
        {"course_code": "PCCST402", "course_name": "Database Management Systems", "type": "Theory", "faculty": "Ms. Jincy Denny"},
        {"course_code": "UCHUT347", "course_name": "Engineering Ethics and Sustainable Development", "type": "Theory", "faculty": "Athithya S"},
        {"course_code": "PCCST402", "course_name": "Database Management Systems", "type": "Theory", "faculty": "Ms. Jincy Denny"},
        {"course_code": "PECST411", "course_name": "Software Engineering", "type": "Theory", "faculty": "Vaishak C Krishnan"},
        {"course_code": "PBCST404", "course_name": "Computer Organization and Architecture", "type": "Theory", "faculty": "Vineetha K V"}
    ]
}

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
def calculate_leave_impact(attendance_data, day):
    """
    Calculate the impact of taking leave on a specific day
    Returns impact details including new percentages and allowed status
    """
    if day not in TIMETABLE:
        return None
    
    impact = {
        "day": day,
        "is_allowed": True,
        "subjects_affected": [],
        "subjects_below_75": [],
        "overall_impact": {}
    }
    
    # Get classes for the day
    day_classes = TIMETABLE[day]
    day_subjects = {}
    
    for cls in day_classes:
        if "course_code" in cls:
            course_code = cls["course_code"]
            if course_code not in day_subjects:
                day_subjects[course_code] = 0
            day_subjects[course_code] += 1
    
    # Calculate impact for each subject
    total_current_attended = 0
    total_current_classes = 0
    total_new_attended = 0
    total_new_classes = 0
    
    for course_code, classes_count in day_subjects.items():
        # Find matching subject in attendance data
        for att_subject, att_info in attendance_data.items():
            if att_subject in ["TOTAL", "PERCENTAGE"]:
                continue
            
            if isinstance(att_info, dict) and "raw" in att_info:
                # Check if course code matches
                if course_code in att_subject:
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
def simulate_bunking(attendance_data, days_to_bunk):
    """
    Simulate what attendance would be after bunking specified days
    Returns updated attendance data
    """
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
        if day not in TIMETABLE:
            continue
        
        for cls in TIMETABLE[day]:
            if "course_code" in cls:
                course_code = cls["course_code"]
                if course_code not in classes_missed:
                    classes_missed[course_code] = 0
                classes_missed[course_code] += 1
    
    # Update attendance data
    for course_code, missed_count in classes_missed.items():
        for subject, info in simulated_data.items():
            if subject in ["TOTAL", "PERCENTAGE"]:
                continue
            
            if isinstance(info, dict) and "raw" in info:
                if course_code in subject:
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
def analyze_safe_days(attendance_data, simulate_days=None):
    """
    Analyze which days are safe to bunk based on current attendance
    Returns a dict with day names and their safety status
    Each day is checked to ensure bunking won't drop ANY subject below 75%
    
    Args:
        attendance_data: Current attendance data
        simulate_days: Optional list of days to simulate bunking before checking safety
    """
    # If simulating days, update attendance first
    if simulate_days:
        attendance_data = simulate_bunking(attendance_data, simulate_days)
    
    safe_days = {}
    
    for day, classes in TIMETABLE.items():
        day_info = {
            "is_safe": True,
            "reason": [],
            "classes": [],
            "risky_subjects": []
        }
        
        # Get unique subjects for this day
        day_subjects = {}
        for cls in classes:
            if "course_code" in cls:
                course_code = cls["course_code"]
                course_name = cls["course_name"]
                if course_code not in day_subjects:
                    day_subjects[course_code] = {"name": course_name, "count": 0}
                day_subjects[course_code]["count"] += 1
        
        # Check each subject's attendance - calculate if 75% will be maintained
        for course_code, subject_info in day_subjects.items():
            classes_count = subject_info["count"]
            subject_name = subject_info["name"]
            day_info["classes"].append(f"{subject_name} ({classes_count}x)")
            
            # Find matching subject in attendance data
            for att_subject, att_info in attendance_data.items():
                if att_subject in ["TOTAL", "PERCENTAGE"]:
                    continue
                    
                if isinstance(att_info, dict) and "raw" in att_info:
                    # Check if course code matches
                    if course_code in att_subject:
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

# ---------- Scraper with Selenium ----------
def scrape_attendance(username, password):
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

        html = driver.page_source
        return parse_subjectwise_attendance(html)

    finally:
        driver.quit()

# ---------- Flask Routes ----------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        try:
            data = scrape_attendance(username, password)
            safe_days = analyze_safe_days(data)
            session["data"] = data
            session["safe_days"] = safe_days
            return redirect(url_for("dashboard"))
        except Exception:
            error_details = traceback.format_exc()
            return f"<h1 style='color:red'>❌ Error:</h1><pre>{error_details}</pre>"

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    data = session.get("data", {})
    safe_days = session.get("safe_days", {})
    return render_template("dashboard.html", data=data, safe_days=safe_days)

@app.route("/simulate_bunking", methods=["POST"])
def simulate_bunking_route():
    data = session.get("data", {})
    if not data:
        return redirect(url_for("login"))
    
    # Get days to simulate from form
    days_to_simulate = request.form.getlist("days")  # e.g., ["Wednesday", "Thursday"]
    
    if not days_to_simulate:
        return redirect(url_for("dashboard"))
    
    # Calculate safe days after simulating those bunks
    simulated_safe_days = analyze_safe_days(data, simulate_days=days_to_simulate)
    simulated_attendance = simulate_bunking(data, days_to_simulate)
    
    return render_template("dashboard.html", 
                         data=data, 
                         safe_days=simulated_safe_days,
                         simulated_attendance=simulated_attendance,
                         simulated_days=days_to_simulate)

@app.route("/calculate_leave/<day>")
def calculate_leave(day):
    data = session.get("data", {})
    if not data:
        return redirect(url_for("login"))
    
    impact = calculate_leave_impact(data, day)
    safe_days = session.get("safe_days", {})
    
    return render_template("dashboard.html", data=data, safe_days=safe_days, leave_impact=impact, selected_day=day)

if __name__ == "__main__":
    app.run(debug=True)
