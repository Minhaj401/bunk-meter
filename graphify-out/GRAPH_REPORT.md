# Graph Report - bunk  (2026-09-12)

## Corpus Check
- Corpus is ~6,266 words - fits in a single context window. You may not need a graph.

## Summary
- 52 nodes · 101 edges · 6 communities
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 2 edges (avg confidence: 0.85)
- Token cost: 15,707 input · 1,514 output

## Community Hubs (Navigation)
- ETLAB Scraping & Parsing
- Bunk Simulation & Projection
- AI Subject Mapping
- Calendar & Leave Impact
- Auth & Dashboard Routes
- Web Surface & UI

## God Nodes (most connected - your core abstractions)
1. `entry_key()` - 9 edges
2. `timetable_subjects()` - 8 edges
3. `analyze_safe_days()` - 8 edges
4. `resolve_map()` - 7 edges
5. `login()` - 7 edges
6. `match_subject()` - 6 edges
7. `build_subject_map()` - 6 edges
8. `calculate_leave_impact()` - 6 edges
9. `simulate_bunking()` - 6 edges
10. `calculate_leave_on_date()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `Dashboard Template` --calls--> `simulate_bunking_route()`  [EXTRACTED]
  templates/dashboard.html → app.py
- `Dashboard Template` --references--> `Bunk Logic API`  [INFERRED]
  templates/dashboard.html → templates/base.html
- `Dashboard Template` --implements--> `Base Template`  [EXTRACTED]
  templates/dashboard.html → templates/base.html
- `Login Template` --implements--> `Base Template`  [EXTRACTED]
  templates/login.html → templates/base.html

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Attendance Sync & Analysis Flow** — templates_login, etlab_scraper, templates_dashboard, bunk_logic_api [INFERRED 0.90]
- **Tactical Planner System** — app_calculate_date_route, app_simulate_bunking_route, templates_dashboard [EXTRACTED 0.85]

## Communities (6 total, 0 thin omitted)

### Community 0 - "ETLAB Scraping & Parsing"
Cohesion: 0.29
Nodes (9): extract_code(), match_subject(), normalize(), parse_subjectwise_attendance(), parse_timetable(), Parse /student/timetable page into {Day: [entries]}. Entry: {course_code?,…, Returns (attendance_data, timetable). Raises ValueError on any failure., Layers 1-2: exact code, then fuzzy name. Returns (key, method) or (None, None). (+1 more)

### Community 1 - "Bunk Simulation & Projection"
Cohesion: 0.29
Nodes (10): analyze_safe_days(), entry_key(), project_forward(), Merge stored map with fresh auto-match for any new headers., Simulate what attendance would be after bunking specified days Returns updated…, Analyze which days are safe to bunk based on current attendance Returns a dict…, Assume every class from start to end (inclusive) attended. Returns updated copy., resolve_map() (+2 more)

### Community 2 - "AI Subject Mapping"
Cohesion: 0.32
Nodes (8): ai_map_misses(), _ai_prompt(), _ai_validate(), gemini_map_misses(), groq_map_misses(), Layer 3: ask Groq to map leftover headers. Returns {header: key}., Layer 3b: Gemini fallback for leftover headers. Returns {header: key}., Layer 3: Groq first, Gemini fallback. Returns {header: (key, method)}.

### Community 3 - "Calendar & Leave Impact"
Cohesion: 0.29
Nodes (8): build_month(), calculate_date(), calculate_leave(), calculate_leave_impact(), calculate_leave_on_date(), Calculate the impact of taking leave on a specific day Returns impact details…, Leave impact on a calendar date. Intervening days assumed fully attended., Month grid for calendar. Each day: link/safety when projectable.

### Community 4 - "Auth & Dashboard Routes"
Cohesion: 0.32
Nodes (8): build_subject_map(), confirm_mapping(), dashboard(), login(), Auto-match all attendance headers. Returns (mapping, unmatched)., Manual overrides for unmatched subjects, then recompute., timetable_subjects(), route

### Community 5 - "Web Surface & UI"
Cohesion: 0.25
Nodes (8): /calculate_date/<iso>, /confirm_mapping, /dashboard, Bunk Logic API, ETLAB Scraper, Base Template, Dashboard Template, Login Template

## Knowledge Gaps
- **5 isolated node(s):** `/dashboard`, `/calculate_date/<iso>`, `/confirm_mapping`, `ETLAB Scraper`, `Bunk Logic API`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 20 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `simulate_bunking_route()` connect `Bunk Simulation & Projection` to `ETLAB Scraping & Parsing`, `Auth & Dashboard Routes`, `Web Surface & UI`?**
  _High betweenness centrality (0.275) - this node is a cross-community bridge._
- **Why does `Dashboard Template` connect `Web Surface & UI` to `Bunk Simulation & Projection`?**
  _High betweenness centrality (0.253) - this node is a cross-community bridge._
- **What connects `/dashboard`, `/calculate_date/<iso>`, `/confirm_mapping` to the rest of the system?**
  _5 weakly-connected nodes found - possible documentation gaps or missing edges._