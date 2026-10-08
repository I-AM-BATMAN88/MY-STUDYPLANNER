"""
Study Timetable Optimizer - Streamlit App
==========================================

Run this with:
    streamlit run study_planner_app.py

Requires: pip install streamlit pandas
"""

import json
import os
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import List, Optional
from urllib.parse import quote

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

PLANS_DIR = "plans"  # one JSON file per visitor, named by their private ID

# Paste your deployed app's link here (no ?u=... part), e.g.
# APP_URL = "https://yourname-studyplanner.streamlit.app"
# This is what the "Share on WhatsApp" buttons send. If left empty, the app
# tries to work the link out itself, which only works on newer Streamlit.
APP_URL = ""


def get_share_url():
    """The clean app link to share (never includes a personal ?u=... ID)."""
    if APP_URL.strip():
        return APP_URL.strip().split("?")[0]
    try:
        url = str(st.context.url).split("?")[0]  # newer Streamlit only
        if url.startswith("http"):
            return url
    except Exception:
        pass
    return None


def whatsapp_share_link(url):
    """A wa.me link that opens WhatsApp with the message already typed."""
    text = (
        "Check out this study timetable planner. It builds a day-by-day study "
        "schedule from your exam scores and exam dates: " + url
    )
    return "https://wa.me/?text=" + quote(text)


def copy_button_html(url):
    """A small 'Copy link' button. Streamlit has no built-in copy-to-clipboard
    button, so this is a tiny HTML/JS snippet shown inside the page."""
    return f"""
<button id="copybtn" style="
    width:100%; height:38px; cursor:pointer; font-size:15px;
    font-family:'Source Sans Pro',sans-serif; color:#31333F; background:#FFFFFF;
    border:1px solid rgba(49,51,63,0.25); border-radius:8px;">🔗 Copy link</button>
<script>
const url = {json.dumps(url)};
const btn = document.getElementById("copybtn");
function fallbackCopy() {{
  const t = document.createElement("textarea");
  t.value = url; t.style.position = "fixed"; t.style.opacity = "0";
  document.body.appendChild(t); t.focus(); t.select();
  let ok = false;
  try {{ ok = document.execCommand("copy"); }} catch (e) {{}}
  document.body.removeChild(t);
  return ok;
}}
btn.addEventListener("click", async () => {{
  let ok = false;
  try {{ await navigator.clipboard.writeText(url); ok = true; }}
  catch (e) {{ ok = fallbackCopy(); }}
  btn.textContent = ok ? "✅ Link copied!" : "⚠️ Couldn't copy - use the box below";
  setTimeout(() => {{ btn.textContent = "🔗 Copy link"; }}, 2500);
}});
</script>
"""


def render_share_buttons(url, key_prefix, side_by_side=True):
    """Two buttons: share on WhatsApp, and copy the link."""
    wa_label = "💬 Share on WhatsApp"
    if side_by_side:
        c1, c2 = st.columns(2)
        with c1:
            st.link_button(wa_label, whatsapp_share_link(url),
                           use_container_width=True, key=f"{key_prefix}_wa")
        with c2:
            components.html(copy_button_html(url), height=44)
    else:
        st.link_button(wa_label, whatsapp_share_link(url),
                       use_container_width=True, key=f"{key_prefix}_wa")
        components.html(copy_button_html(url), height=44)
    with st.expander("Copy didn't work? Copy the link from here"):
        st.code(url, language=None)
CONFIDENCE_WEIGHT = 0.3  # fixed: confidence always counts for 30% of effective score

SUBJECT_COLORS = ["#4C6EF5", "#F76707", "#2F9E44", "#AE3EC9", "#1098AD", "#E8590C", "#5C940D"]

# Pastel background per weekday (Mon=0 .. Sun=6), and a little fruit sticker per day
WEEKDAY_COLORS = ["#D7EDE5", "#F5F0C4", "#F5D9DC", "#D9DCF5", "#E8E8E8", "#FBE0C8", "#D3E6F5"]
WEEKDAY_STICKERS = ["🥑", "🍋", "🍓", "🍇", "🫐", "🍑", "🍊"]


# ---------- Core model ----------

def weighted_average(scores: List[Optional[float]]) -> float:
    """Average up to 3 exam scores, weighting the most recent one highest."""
    valid = [s for s in scores if s is not None]
    if not valid:
        return 70.0
    n = len(valid)
    weights = list(range(1, n + 1))
    return sum(s * w for s, w in zip(valid, weights)) / sum(weights)


@dataclass
class Subject:
    name: str
    scores: List[Optional[float]]
    exam_day: int
    confidence: float = 5.0
    confidence_weight: float = CONFIDENCE_WEIGHT
    material_deadline_day: Optional[int] = None
    practice_test_days: List[int] = field(default_factory=list)

    def score_estimate(self) -> float:
        return weighted_average(self.scores)

    def effective_score(self) -> float:
        return (1 - self.confidence_weight) * self.score_estimate() \
            + self.confidence_weight * (self.confidence * 10)

    def weakness_weight(self) -> float:
        return (100 - self.effective_score()) ** 2


def days_until(subject: Subject, current_day: int) -> int:
    return max(subject.exam_day - current_day, 1)


def to_minutes(hours: float) -> int:
    """Convert hours (decimal) to whole minutes for display."""
    return round(hours * 60)


def build_timetable(subjects, total_days, hours_per_day, max_subjects_per_day,
                    start_date=None, keep_warm=True):
    timetable = []
    last_studied = {}

    for day in range(1, total_days + 1):
        # A subject is studied on days before its exam, but not on the exam
        # day itself -- that day is for sitting the exam, not studying for it.
        remaining = [s for s in subjects if s.exam_day > day]

        if not remaining:
            note = "Exam day — no study time scheduled." if any(s.exam_day == day for s in subjects) \
                else "All exams done."
            timetable.append({"day": day, "allocations": [], "note": note})
            continue

        scored = []
        for s in remaining:
            urgency_weight = s.weakness_weight() / days_until(s, day)
            scored.append((s, urgency_weight))
        scored.sort(key=lambda pair: pair[1], reverse=True)

        warm_subject = None
        if keep_warm and max_subjects_per_day > 1 and len(scored) > max_subjects_per_day:
            core = scored[:max_subjects_per_day - 1]
            leftovers = scored[max_subjects_per_day - 1:]
            warm_pair = min(leftovers, key=lambda pair: last_studied.get(pair[0].name, -1))
            warm_subject = warm_pair[0]
            chosen = core
        else:
            chosen = scored[:max_subjects_per_day]

        warm_hours = round(hours_per_day * 0.2, 1) if warm_subject else 0.0
        core_hours = hours_per_day - warm_hours

        total_weight = sum(s.weakness_weight() for s, _ in chosen)
        allocations = []
        for s, _ in chosen:
            share = s.weakness_weight() / total_weight if total_weight > 0 else 1 / len(chosen)
            hours = round(core_hours * share, 1)
            allocations.append((s.name, hours))
            last_studied[s.name] = day

        if warm_subject:
            allocations.append((warm_subject.name, warm_hours))
            last_studied[warm_subject.name] = day

        entry = {"day": day, "allocations": allocations}
        if start_date:
            entry["date"] = start_date + timedelta(days=day - 1)
        timetable.append(entry)

    return timetable


def enforce_practice_test_limit(subjects, max_per_day=2):
    """
    Keep at most `max_per_day` subjects' practice tests on any single day.
    If more are scheduled for the same day, the earliest-entered subjects
    keep their slot and the rest are dropped. For each one dropped, the
    nearest free day *before that subject's own exam* is suggested (search
    radiates outward from the original day, checking the day before first,
    then the day after, and so on). Returns a list of dicts:
    {"day": original day, "name": subject name, "suggestion": day or None,
    "exam_day": that subject's exam day} -- one per dropped practice test.
    """
    by_name = {s.name: s for s in subjects}

    day_map = {}
    for s in subjects:
        for d in s.practice_test_days:
            day_map.setdefault(d, []).append(s.name)

    allowed = {}
    overflow = {}
    for d, names in day_map.items():
        allowed[d] = set(names[:max_per_day])
        if len(names) > max_per_day:
            overflow[d] = names[max_per_day:]

    # Tracks how many slots are tentatively used per day as we hand out
    # suggestions, so two dropped subjects aren't both pointed at a day
    # that can only fit one more.
    usage = {d: len(names) for d, names in allowed.items()}

    def find_alternative(original_day, exam_day):
        offset = 1
        while (original_day - offset >= 1) or (original_day + offset < exam_day):
            for candidate in (original_day - offset, original_day + offset):
                if candidate < 1 or candidate >= exam_day:
                    continue
                if usage.get(candidate, 0) < max_per_day:
                    return candidate
            offset += 1
        return None

    results = []
    for d, dropped in overflow.items():
        for name in dropped:
            subject = by_name[name]
            suggestion = find_alternative(d, subject.exam_day)
            if suggestion is not None:
                usage[suggestion] = usage.get(suggestion, 0) + 1
            results.append({
                "day": d, "name": name,
                "suggestion": suggestion, "exam_day": subject.exam_day,
            })

    for s in subjects:
        s.practice_test_days = [d for d in s.practice_test_days if s.name in allowed.get(d, set())]

    return results


def collect_events(subjects):
    events = {}
    for s in subjects:
        events.setdefault(s.exam_day, []).append((s.name, "🎓 Exam"))
        if s.material_deadline_day:
            events.setdefault(s.material_deadline_day, []).append((s.name, "📌 Material deadline"))
        for d in s.practice_test_days:
            events.setdefault(d, []).append((s.name, "📝 Practice test"))
    return events


def timetable_to_dataframe(timetable):
    all_subjects = sorted({name for entry in timetable for name, _ in entry["allocations"]})
    columns = [f"{name} (min)" for name in all_subjects]
    rows = []
    for entry in timetable:
        row = {"Day": entry["day"]}
        if "date" in entry:
            row["Date"] = entry["date"].isoformat()
        for col in columns:
            row[col] = 0
        for name, hours in entry["allocations"]:
            row[f"{name} (min)"] = to_minutes(hours)
        if not entry["allocations"]:
            row["Note"] = entry.get("note", "")
        rows.append(row)
    return pd.DataFrame(rows)


def render_calendar_html(timetable, events, start_date, color_map, today=None):
    """Build a standalone, printable HTML page showing the calendar visually."""
    today = today or date.today()
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    entries = []
    for e in timetable:
        day = e["day"]
        d = start_date + timedelta(days=day - 1) if start_date else None
        weekday = d.weekday() if d else (day - 1) % 7
        entries.append((day, d, weekday, e))

    first_weekday = entries[0][2]
    weeks = []
    current_week = [None] * first_weekday
    for day, d, weekday, e in entries:
        current_week.append((day, d, weekday, e))
        if len(current_week) == 7:
            weeks.append(current_week)
            current_week = []
    if current_week:
        current_week += [None] * (7 - len(current_week))
        weeks.append(current_week)

    rows_html = ""
    for week in weeks:
        rows_html += "<tr>"
        for cell in week:
            if cell is None:
                rows_html += "<td style='border:none;'></td>"
                continue
            day, d, weekday, e = cell
            is_past = bool(d and d < today)
            is_today = bool(d and d == today)
            label = d.strftime("%b %d") if d else f"Day {day}"
            bg = WEEKDAY_COLORS[weekday]
            sticker = WEEKDAY_STICKERS[weekday]
            day_events = events.get(day, [])
            exam_flag = " 🎓" if any(etype == "🎓 Exam" for _, etype in day_events) else ""
            status = " ✅" if is_past else " 🔵" if is_today else ""

            if not e["allocations"]:
                lines = [e.get("note", "Free day")]
            else:
                lines = [f"{name}: {to_minutes(hours)} min" for name, hours in e["allocations"]]
            for name, etype in day_events:
                lines.append(f"{etype}: {name}")

            border = "border:3px solid #4C6EF5;" if is_today else "border:2px solid #CED4DA;"
            opacity = "opacity:0.6;" if is_past else ""

            rows_html += (
                f"<td style='background:{bg}; {border} {opacity} border-radius:10px; "
                f"padding:8px; vertical-align:top; min-width:120px;'>"
                f"<b>{sticker} {label}{status}{exam_flag}</b><br>"
                f"<span style='font-size:0.85em;'>{'<br>'.join(lines)}</span>"
                f"</td>"
            )
        rows_html += "</tr>"

    header_html = "".join(f"<th style='padding:6px; color:#E8929A;'>{name}</th>" for name in day_names)

    html = f"""
    <html>
    <head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: 'Comic Sans MS', 'Chalkboard SE', cursive; color:#000000; background:#FFFFFF; }}
        table {{ border-collapse: separate; border-spacing: 6px; width: 100%; }}
        h1 {{ color: #E8929A; }}
    </style>
    </head>
    <body>
        <h1>📚 Your Study Timetable</h1>
        <table><tr>{header_html}</tr>{rows_html}</table>
    </body>
    </html>
    """
    return html


def subject_color_map(subjects):
    return {s.name: SUBJECT_COLORS[i % len(SUBJECT_COLORS)] for i, s in enumerate(subjects)}


# ---------- Styling ----------

def inject_css():
    import textwrap
    st.markdown(textwrap.dedent("""
        <style>
        .cal-header {
            text-align: center;
            font-weight: 700;
            padding: 10px 0;
            color: #FFFFFF;
            background: #E8929A;
            border-radius: 10px;
            letter-spacing: 0.02em;
            font-family: 'Comic Sans MS', 'Chalkboard SE', 'Brush Script MT', cursive;
            font-size: 1.3em;
            margin-bottom: 4px;
        }
        .cal-cell {
            border-radius: 14px;
            padding: 10px 8px;
            margin-bottom: 4px;
            min-height: 150px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.08);
            text-align: center;
            font-family: 'Comic Sans MS', 'Chalkboard SE', 'Brush Script MT', cursive;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: flex-start;
            color: #000000;
        }
        .cal-cell * {
            text-align: center;
            color: #000000 !important;
        }
        .cal-today {
            border: 3px solid #4C6EF5;
        }
        .cal-past {
            opacity: 0.45;
        }
        .cal-empty {
            border-radius: 12px;
            margin-bottom: 4px;
            min-height: 150px;
            background: transparent;
            border: none;
        }
        .cal-list-row {
            border-radius: 14px;
            padding: 12px 16px;
            margin-bottom: 6px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.08);
            font-family: 'Comic Sans MS', 'Chalkboard SE', 'Brush Script MT', cursive;
            color: #000000;
            text-align: left;
        }
        .cal-list-row * {
            color: #000000 !important;
        }
        div[data-testid="column"] div[data-testid="stButton"] button {
            width: 100%;
            border-radius: 8px;
        }
        .subject-card {
            border-radius: 12px;
            padding: 14px 16px;
            margin-bottom: 12px;
            background: #FFFFFF;
            border: 1px solid #E9ECEF;
            border-left: 6px solid var(--accent, #4C6EF5);
            box-shadow: 0 1px 4px rgba(0,0,0,0.06);
        }
        .subject-card h4 {
            margin: 0 0 6px 0;
            color: #000000 !important;
        }
        .subject-card .meta {
            font-size: 0.85em;
            color: #000000 !important;
            line-height: 1.6;
        }
        .stButton button {
            border-radius: 8px;
        }
        div[data-testid="stExpander"] {
            border-radius: 10px;
        }
        </style>
    """), unsafe_allow_html=True)

    st.markdown(textwrap.dedent("""
        <style>
        @media (max-width: 640px) {
            div[data-testid="stHorizontalBlock"] {
                flex-wrap: wrap !important;
            }
            div[data-testid="column"] {
                width: 100% !important;
                flex: 1 1 100% !important;
                min-width: 100% !important;
            }
            .cal-header {
                font-size: 1em;
                padding: 4px 0;
            }
            .cal-cell {
                min-height: auto;
                padding: 12px 14px;
                margin-bottom: 10px;
                font-size: 1.05em;
            }
            .subject-card {
                padding: 12px 14px;
            }
            .cal-list-row {
                padding: 14px 16px;
                font-size: 1.05em;
            }
        }
        </style>
    """), unsafe_allow_html=True)


def render_subject_cards(subjects, color_map, start_date=None):
    """One bordered card per subject, summarizing all its info at a glance."""
    cols = st.columns(2)
    for i, s in enumerate(subjects):
        color = color_map.get(s.name, "#4C6EF5")
        exam_label = f"Day {s.exam_day}"
        if start_date:
            exam_label = (start_date + timedelta(days=s.exam_day - 1)).strftime("%b %d")

        deadline_line = ""
        if s.material_deadline_day:
            dl_label = f"Day {s.material_deadline_day}"
            if start_date:
                dl_label = (start_date + timedelta(days=s.material_deadline_day - 1)).strftime("%b %d")
            deadline_line = f"📌 Finish content by: {dl_label}<br>"

        practice_line = ""
        if s.practice_test_days:
            practice_line = f"📝 Practice tests: day(s) {', '.join(str(d) for d in s.practice_test_days)}<br>"

        scores_str = ", ".join(str(sc) for sc in s.scores)

        card_html = (
            f'<div class="subject-card" style="--accent:{color}">'
            f'<h4>{s.name}</h4>'
            f'<div class="meta">'
            f'📊 Recent scores: {scores_str}<br>'
            f'🧠 Confidence: {s.confidence}/10<br>'
            f'🎯 Exam: {exam_label}<br>'
            f'{deadline_line}{practice_line}'
            f'<b>Effective score: {s.effective_score():.1f}%</b>'
            f'</div>'
            f'</div>'
        )

        with cols[i % 2]:
            st.markdown(card_html, unsafe_allow_html=True)


@st.dialog("Day details")
def show_day_dialog(label, allocations, day_events, note, practice_test_link):
    st.markdown(f"### {label}")

    if note:
        st.info(note)
    elif not allocations:
        st.caption("Nothing scheduled.")
    else:
        st.markdown("**Study plan:**")
        for name, hours in allocations:
            st.write(f"- {name}: {to_minutes(hours)} min")

    if day_events:
        st.markdown("**Events:**")
        for name, etype in day_events:
            st.write(f"- {etype}: {name}")
            if "Practice test" in etype and practice_test_link:
                st.link_button(f"📊 Open tracker for {name}", practice_test_link)


def render_calendar(timetable, events, start_date, practice_test_link, color_map, today=None, key_prefix="cal"):
    """
    Render the timetable as a week-by-week calendar grid.
    Days before `today` are shown greyed-out and marked done, since that
    study time has already passed. `today` defaults to the real current
    date, so re-opening the app on a later day automatically shows earlier
    days as complete without any action needed.
    """
    today = today or date.today()
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    entries = []
    for e in timetable:
        day = e["day"]
        d = start_date + timedelta(days=day - 1) if start_date else None
        weekday = d.weekday() if d else (day - 1) % 7
        entries.append((day, d, weekday, e))

    first_weekday = entries[0][2]
    weeks = []
    current_week = [None] * first_weekday
    for day, d, weekday, e in entries:
        current_week.append((day, d, e))
        if len(current_week) == 7:
            weeks.append(current_week)
            current_week = []
    if current_week:
        current_week += [None] * (7 - len(current_week))
        weeks.append(current_week)

    header_cols = st.columns(7)
    for col, name in zip(header_cols, day_names):
        col.markdown(f"<div class='cal-header'>{name}</div>", unsafe_allow_html=True)

    for week in weeks:
        cols = st.columns(7)
        for col, cell in zip(cols, week):
            with col:
                if cell is None:
                    st.markdown("<div class='cal-empty'></div>", unsafe_allow_html=True)
                    continue
                day, d, e = cell
                is_past = bool(d and d < today)
                is_today = bool(d and d == today)
                label = d.strftime("%b %d") if d else f"Day {day}"
                weekday_idx = d.weekday() if d else (day - 1) % 7
                bg_color = WEEKDAY_COLORS[weekday_idx]
                sticker = WEEKDAY_STICKERS[weekday_idx]
                day_events = events.get(day, [])
                exam_flag = " 🎓" if any(etype == "🎓 Exam" for _, etype in day_events) else ""

                css_class = "cal-cell"
                if is_past:
                    css_class += " cal-past"
                elif is_today:
                    css_class += " cal-today"

                if is_past:
                    body = "<div>Done</div>"
                elif not e["allocations"]:
                    body = f"<div style='font-size:0.8em'>{e.get('note', 'Free day')}</div>"
                else:
                    total_min = sum(to_minutes(h) for _, h in e["allocations"])
                    body = f"<div style='font-size:0.8em'>{len(e['allocations'])} subject(s)<br>{total_min} min</div>"

                st.markdown(
                    f"<div class='{css_class}' style='background:{bg_color};'>"
                    f"<div><b>{sticker} {label}</b>{' ✅' if is_past else ' 🔵' if is_today else ''}{exam_flag}</div>"
                    f"{body}"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                if st.button("🔍 Details", key=f"details_{key_prefix}_{day}", use_container_width=True):
                    show_day_dialog(
                        label, e["allocations"], day_events,
                        e.get("note"), practice_test_link,
                    )


def render_calendar_list(timetable, events, start_date, practice_test_link, color_map, today=None, key_prefix="cal"):
    """
    Render the timetable as a single vertical list, one full-width card per
    day. Unlike the 7-column grid, this never gets squeezed on a narrow
    phone screen -- every card is always full width, so this is the better
    choice on mobile.
    """
    today = today or date.today()

    for e in timetable:
        day = e["day"]
        d = start_date + timedelta(days=day - 1) if start_date else None
        is_past = bool(d and d < today)
        is_today = bool(d and d == today)
        label = d.strftime("%A, %b %d") if d else f"Day {day}"
        weekday_idx = d.weekday() if d else (day - 1) % 7
        bg_color = WEEKDAY_COLORS[weekday_idx]
        sticker = WEEKDAY_STICKERS[weekday_idx]
        day_events = events.get(day, [])
        exam_flag = " 🎓" if any(etype == "🎓 Exam" for _, etype in day_events) else ""

        css_class = "cal-list-row"
        if is_past:
            css_class += " cal-past"
        elif is_today:
            css_class += " cal-today"

        if is_past:
            body = "Done"
        elif not e["allocations"]:
            body = e.get("note", "Free day")
        else:
            parts = [f"{name}: {to_minutes(hours)} min" for name, hours in e["allocations"]]
            body = " · ".join(parts)

        event_line = ""
        if day_events and not is_past:
            event_line = "<br>" + " · ".join(f"{etype}: {name}" for name, etype in day_events)

        st.markdown(
            f"<div class='{css_class}' style='background:{bg_color};'>"
            f"<b>{sticker} {label}</b>{' ✅' if is_past else ' 🔵' if is_today else ''}{exam_flag}"
            f"<br><span style='font-size:0.85em'>{body}</span>"
            f"<span style='font-size:0.8em'>{event_line}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

        if st.button("🔍 Details", key=f"details_list_{key_prefix}_{day}", use_container_width=True):
            show_day_dialog(label, e["allocations"], day_events, e.get("note"), practice_test_link)


# ---------- Persistence (so the calendar auto-updates day to day) ----------

def valid_uid(uid):
    """Visitor IDs come from the URL, so only allow plain letters/digits
    (stops anyone sneaking a file path like ../ into the filename)."""
    return isinstance(uid, str) and re.fullmatch(r"[A-Za-z0-9]{8,32}", uid) is not None


def plan_path(uid):
    if not valid_uid(uid):
        raise ValueError("Invalid visitor ID")
    os.makedirs(PLANS_DIR, exist_ok=True)
    return os.path.join(PLANS_DIR, f"{uid}.json")


def save_plan(uid, subjects, total_days, hours_per_day, max_subjects_per_day, keep_warm,
            practice_test_link, anchor_date, user_name=""):
    data = {
        "anchor_date": anchor_date.isoformat(),
        "total_days": total_days,
        "hours_per_day": hours_per_day,
        "max_subjects_per_day": max_subjects_per_day,
        "keep_warm": keep_warm,
        "practice_test_link": practice_test_link,
        "subjects": [asdict(s) for s in subjects],
        "user_name": user_name,
    }
    with open(plan_path(uid), "w") as f:
        json.dump(data, f)


def load_plan(uid):
    path = plan_path(uid)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    data["anchor_date"] = date.fromisoformat(data["anchor_date"])
    data["subjects"] = [Subject(**s) for s in data["subjects"]]
    data.setdefault("user_name", "")  # older saved plans won't have this key
    return data


# ---------- Streamlit UI ----------

st.set_page_config(page_title="Study Timetable Optimizer", page_icon="📚", layout="wide")
inject_css()

# --- Private visitor ID: keeps every person's plan separate ---
# A new visitor gets a random ID that is written into their link (?u=...).
# Their plan is saved under that ID, so people who open the plain shared
# link start fresh instead of seeing someone else's data.
if "uid" not in st.session_state:
    _from_url = st.query_params.get("u")
    st.session_state["uid"] = _from_url if valid_uid(_from_url) else uuid.uuid4().hex[:12]
uid = st.session_state["uid"]
if st.query_params.get("u") != uid:
    st.query_params["u"] = uid

# A widget's session_state value can't be changed after that widget has
# already been created in the same run. So the "insert suggested day"
# button (further down) stages its change under a "pending_pt_*" key and
# reruns -- this block applies the staged change here, at the very top,
# before the pt_i text_input widgets are created below.
for _key in list(st.session_state.keys()):
    if _key.startswith("pending_pt_"):
        _idx = _key.removeprefix("pending_pt_")
        st.session_state[f"pt_{_idx}"] = st.session_state.pop(_key)

# --- Intro / welcome screen (shown every time the app is opened) ---
if "intro_done" not in st.session_state:
    st.session_state["intro_done"] = False
    _existing_plan = load_plan(uid)
    if _existing_plan is not None:
        st.session_state["user_name"] = _existing_plan.get("user_name", "")

if not st.session_state["intro_done"]:
    _returning = load_plan(uid) is not None
    _name = st.session_state.get("user_name", "")

    if _returning:
        st.title(f"📚 Welcome back{', ' + _name if _name else ''}!")
        new_name = st.text_input(
            "What's your name? (so your calendar can say hi)",
            value=_name, key="returning_name_input",
        )
        if st.button("Continue →", type="primary"):
            st.session_state["user_name"] = new_name.strip()
            st.session_state["intro_done"] = True
            st.rerun()
        st.stop()

    st.title("📚 Welcome to the Study Timetable Optimizer")
    st.write(
        "Hi! This tool builds you a day-by-day study schedule for your exams, "
        "based on how you're actually doing in each subject — not just an even "
        "split of your time."
    )
    st.markdown("**Here's how it works:**")
    st.markdown(
        "- You enter your subjects, your last few exam scores, and how "
        "confident you feel in each one\n"
        "- You tell it your exam dates, how many hours a day you can study, "
        "and how many subjects you want to juggle per day\n"
        "- It works out a schedule that gives weaker subjects and closer "
        "exams more of your time, while still keeping every subject "
        "ticking over so nothing gets forgotten\n"
        "- You get a colorful calendar you can click into for details, "
        "download, or print — and it automatically updates as days pass"
    )
    st.markdown("**About the Past Paper Tracker:**")
    st.write(
        "If you schedule a practice test for a subject, the calendar will show "
        "a button linking to the Past Paper Tracker — a free Chrome extension "
        "for keeping track of your practice test scores over time. You'll need "
        "to download and install it yourself from the Chrome Web Store. "
        "Note that it only works on a PC/laptop, since it's a browser "
        "extension — it won't work on mobile."
    )

    st.write("Have your recent scores and exam dates ready, then hit the button below to get started.")

    user_name_input = st.text_input("What's your name? (so your calendar can say hi)", key="name_input")

    if st.button("Get started →", type="primary"):
        st.session_state["user_name"] = user_name_input.strip()
        st.session_state["intro_done"] = True
        st.rerun()

    st.stop()

user_name = st.session_state.get("user_name", "")
title_name = f"{user_name}'s " if user_name else ""
st.title(f"📚 {title_name}Study Timetable Optimizer")
st.write(
    "Enter your subjects, recent scores, and exam days. The tool allocates "
    "your daily study hours using a diminishing-returns model — weaker "
    "subjects and closer exams get more time, while a rotating slot keeps "
    "every other subject from going cold. Confidence is factored in at a "
    "fixed 30% weight alongside your recent scores."
)

# --- Auto-updating view of a saved plan, if one exists ---
saved = load_plan(uid)
if saved:
    saved_name = saved.get("user_name", "")
    saved_prefix = f"{saved_name}'s " if saved_name else "Your "

    st.divider()
    st.subheader(f"📅 {saved_prefix}current plan")
    st.caption(
        "This updates automatically each day — days that have already "
        "passed are greyed out and marked done."
    )
    timetable = build_timetable(
        subjects=saved["subjects"],
        total_days=saved["total_days"],
        hours_per_day=saved["hours_per_day"],
        max_subjects_per_day=saved["max_subjects_per_day"],
        start_date=saved["anchor_date"],
        keep_warm=saved["keep_warm"],
    )
    events = collect_events(saved["subjects"])
    colors = subject_color_map(saved["subjects"])

    st.markdown(f"**{saved_prefix}subjects, at a glance**")
    render_subject_cards(saved["subjects"], colors, saved["anchor_date"])

    view_mode_saved = st.radio(
        "Calendar view", ["📅 Grid (best on PC)", "📋 List (best on phone)"],
        index=1, horizontal=True, key="view_mode_saved", label_visibility="collapsed",
    )
    if view_mode_saved.startswith("📋"):
        render_calendar_list(timetable, events, saved["anchor_date"], saved["practice_test_link"], colors, key_prefix="saved")
    else:
        render_calendar(timetable, events, saved["anchor_date"], saved["practice_test_link"], colors, key_prefix="saved")

    dl1, dl2 = st.columns(2)
    with dl1:
        df = timetable_to_dataframe(timetable)
        st.download_button(
            "📄 Download as CSV (numbers)", data=df.to_csv(index=False).encode("utf-8"),
            file_name="study_timetable.csv", mime="text/csv",
            use_container_width=True, key="csv_top",
        )
    with dl2:
        html_page = render_calendar_html(timetable, events, saved["anchor_date"], colors)
        st.download_button(
            "📅 Download as calendar (HTML)", data=html_page.encode("utf-8"),
            file_name="study_timetable.html", mime="text/html",
            use_container_width=True, key="html_top",
        )

    if st.button("🔄 Start a new plan"):
        os.remove(plan_path(uid))
        st.rerun()

st.divider()

# --- Sidebar: global settings ---
with st.sidebar:
    st.header("Settings")
    start_date = st.date_input("When do you want to start?", value=date.today())
    total_days = st.number_input("Total days to plan", min_value=1, max_value=120, value=30)
    hours_per_day = st.number_input("Study hours per day", min_value=0.5, max_value=16.0, value=5.0, step=0.5)
    max_subjects_per_day = st.number_input("Max subjects per day", min_value=1, max_value=10, value=3)
    keep_warm = st.checkbox("Keep every subject warm (rotating maintenance slot)", value=True)
    st.caption("Confidence is fixed at 30% weight alongside your recent scores.")

with st.sidebar:
    st.divider()
    st.markdown("**📤 Share this app**")
    _share_url = get_share_url()
    if _share_url:
        render_share_buttons(_share_url, "share_sidebar", side_by_side=False)
        st.caption("Sends a clean link, so friends get their own fresh plan.")
    else:
        st.caption(
            "To share the app, copy the link from your address bar and delete "
            "everything from the `?` onwards. If you send the full link with "
            "`?u=...`, friends will open YOUR plan."
        )

# Past Paper Tracker link -- kept in code only, not shown as an editable field.
# See the Welcome page for the user-facing explanation of what this is.
practice_test_link = "https://chromewebstore.google.com/detail/past-paper-tracker/cmadpkpdpbcklhhijkmbajjolfmopgko"

# --- Subject inputs ---
st.subheader("Your subjects")
st.caption("Enter your last 3 full exam scores if you have them — leave later ones equal to your "
        "most recent if you don't have 3 yet.")

num_subjects = st.number_input("How many subjects/papers?", min_value=1, max_value=15, value=6)

subjects = []
for i in range(int(num_subjects)):
    with st.expander(f"Subject {i + 1}", expanded=True):
        name = st.text_input("Name", value=f"Subject {i + 1}", key=f"name_{i}")

        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            s1 = st.number_input("Score (oldest)", 0, 100, 70, key=f"s1_{i}")
        with sc2:
            s2 = st.number_input("Score (middle)", 0, 100, 70, key=f"s2_{i}")
        with sc3:
            s3 = st.number_input("Score (most recent)", 0, 100, 70, key=f"s3_{i}")

        confidence = st.slider("Confidence (1-10)", 1, 10, 5, key=f"conf_{i}")
        exam_day = st.number_input(
            "Exam day (days from your start date)", min_value=1, max_value=int(total_days),
            value=min(int(total_days), 14), key=f"day_{i}",
        )

        has_deadline = st.checkbox("Set a 'finish content by' deadline?", key=f"hasdl_{i}")
        material_deadline_day = None
        if has_deadline:
            material_deadline_day = st.number_input(
                "Finish content by day", min_value=1, max_value=int(exam_day),
                value=min(int(exam_day), 7), key=f"dl_{i}",
            )

        practice_days_str = st.text_input(
            "Practice test days (comma-separated, optional, e.g. 5,10,18)",
            key=f"pt_{i}",
        )
        practice_test_days = [
            int(tok.strip()) for tok in practice_days_str.split(",")
            if tok.strip().isdigit()
        ]

        subjects.append(
            Subject(
                name=name,
                scores=[s1, s2, s3],
                exam_day=exam_day,
                confidence=confidence,
                confidence_weight=CONFIDENCE_WEIGHT,
                material_deadline_day=material_deadline_day,
                practice_test_days=practice_test_days,
            )
        )

st.divider()

if st.button("Generate / update my study timetable", type="primary"):
    st.session_state["plan_generated"] = True

if st.session_state.get("plan_generated"):
    name_to_index = {s.name: i for i, s in enumerate(subjects)}
    practice_test_results = enforce_practice_test_limit(subjects, max_per_day=3)

    for r in practice_test_results:
        if r["suggestion"] is not None:
            st.warning(
                f"Day {r['day']}: only 3 practice tests are allowed per day — "
                f"skipped {r['name']}."
            )
            idx = name_to_index.get(r["name"])
            if idx is not None:
                insert_key = f"insert_{r['name']}_{r['day']}_{r['suggestion']}"
                if st.button(
                    f"➕ Insert day {r['suggestion']} for {r['name']} (before its exam on "
                    f"day {r['exam_day']}) and regenerate",
                    key=insert_key,
                ):
                    pt_key = f"pt_{idx}"
                    current = st.session_state.get(pt_key, "")
                    parts = [
                        p.strip() for p in current.split(",")
                        if p.strip() and p.strip() != str(r["day"])
                    ]
                    if str(r["suggestion"]) not in parts:
                        parts.append(str(r["suggestion"]))
                    # Can't write to st.session_state[pt_key] directly here --
                    # that widget already exists this run. Stage it instead;
                    # the block at the top of the script applies it next run.
                    st.session_state[f"pending_pt_{idx}"] = ", ".join(parts)
                    st.rerun()
        else:
            st.warning(
                f"Day {r['day']}: only 3 practice tests are allowed per day — "
                f"skipped {r['name']}. No free day before its exam on day "
                f"{r['exam_day']} was found."
            )

    timetable = build_timetable(
        subjects=subjects,
        total_days=int(total_days),
        hours_per_day=hours_per_day,
        max_subjects_per_day=int(max_subjects_per_day),
        start_date=start_date,
        keep_warm=keep_warm,
    )
    events = collect_events(subjects)
    colors = subject_color_map(subjects)

    save_plan(uid, subjects, int(total_days), hours_per_day, int(max_subjects_per_day),
            keep_warm, practice_test_link, start_date, user_name=user_name)

    name_prefix = f"{user_name}'s " if user_name else "Your "
    st.subheader(f"{name_prefix}subjects, at a glance")
    render_subject_cards(subjects, colors, start_date)

    st.subheader("Your calendar")
    view_mode_new = st.radio(
        "Calendar view", ["📅 Grid (best on PC)", "📋 List (best on phone)"],
        index=1, horizontal=True, key="view_mode_new", label_visibility="collapsed",
    )
    if view_mode_new.startswith("📋"):
        render_calendar_list(timetable, events, start_date, practice_test_link, colors, key_prefix="new")
    else:
        render_calendar(timetable, events, start_date, practice_test_link, colors, key_prefix="new")

    st.caption(
        "🔖 Saved. Bookmark this page (or add it to your home screen) — the link in "
        "your address bar is your personal link, and it's how you get back to your plan."
    )
    st.caption(
        "📤 Sharing the app with friends? Send them the link **without** the "
        "`?u=...` part, so they start with their own fresh plan."
    )

    dl1, dl2 = st.columns(2)
    with dl1:
        df = timetable_to_dataframe(timetable)
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📄 Download as CSV (numbers)", data=csv,
            file_name="study_timetable.csv", mime="text/csv",
            use_container_width=True, key="csv_new",
        )
    with dl2:
        html_page = render_calendar_html(timetable, events, start_date, colors)
        st.download_button(
            "📅 Download as calendar (HTML)", data=html_page.encode("utf-8"),
            file_name="study_timetable.html", mime="text/html",
            use_container_width=True, key="html_new",
        )
    st.caption("The HTML version looks like the calendar above — open it in any browser or print it.")

    with st.expander("How this was calculated"):
        st.write(
            "Each subject gets an *effective score*: a weighted average of "
            "your last up to 3 exam scores (most recent weighted highest), "
            "blended with your confidence rating at a fixed 30% weight "
            "(confidence x 10 = a percentage). Each day, the tool ranks "
            "remaining subjects by `(100 - effective_score)^2 / days_until_exam` "
            "and gives the top ones (limited by 'Max subjects per day') a share "
            "of the hours proportional to `(100 - effective_score)^2`. This "
            "comes from solving a constrained optimization problem: maximize "
            "total expected improvement given a fixed number of study hours, "
            "assuming diminishing returns (√hours) per subject. If 'keep every "
            "subject warm' is on, one slot a day (20% of your hours) goes to "
            "whichever leftover subject has gone longest without study."
        )


# ---------- Share ----------

_share_url_main = get_share_url()
if _share_url_main:
    st.divider()
    st.subheader("📤 Share with friends")
    render_share_buttons(_share_url_main, "share_main", side_by_side=True)


# ---------- Feedback ----------

st.divider()
st.subheader("💬 Was this helpful?")

rating = st.slider("Rate this tool (1-5)", 1, 5, 5, key="feedback_rating")
comment = st.text_area("Any feedback or suggestions?", key="feedback_comment")

if st.button("Submit feedback"):
    feedback_entry = pd.DataFrame([{
        "timestamp": pd.Timestamp.now().isoformat(),
        "rating": rating,
        "comment": comment,
    }])
    try:
        existing = pd.read_csv("feedback.csv")
        updated = pd.concat([existing, feedback_entry], ignore_index=True)
    except FileNotFoundError:
        updated = feedback_entry
    updated.to_csv("feedback.csv", index=False)
    st.success("Thanks for your feedback!")


# ---------- Admin: view/download collected feedback ----------
# Simple passcode gate so casual visitors don't see everyone else's
# comments. Change ADMIN_PASSCODE to whatever you like -- this is a
# student-project-level protection, not real security (anyone reading
# the source code can see it), but it keeps it off the main page.
ADMIN_PASSCODE = "studyplanner2026"

with st.expander("📊 View/download collected feedback (admin only)"):
    entered = st.text_input("Passcode", type="password", key="admin_passcode")
    if entered == ADMIN_PASSCODE:
        try:
            feedback_df = pd.read_csv("feedback.csv")
            st.write(f"{len(feedback_df)} response(s) so far.")
            st.dataframe(feedback_df, use_container_width=True)
            st.download_button(
                "Download feedback.csv",
                data=feedback_df.to_csv(index=False).encode("utf-8"),
                file_name="feedback.csv", mime="text/csv",
            )
        except FileNotFoundError:
            st.info("No feedback submitted yet.")
    elif entered:
        st.error("Incorrect passcode.")
