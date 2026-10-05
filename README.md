# MY-STUDYPLANNER
A Streamlit app that builds a personalized study timetable based on your exam scores, confidence, and exam dates, using a diminishing-returns optimization model.
# 📚 Study Timetable Optimizer

A web app that builds a personalized, day-by-day study schedule for exam prep — based on how you're actually performing in each subject, not just an even split of your time.

## What it does

Enter your subjects, your last few exam scores, your confidence level, and your exam dates. The app allocates your daily study hours using a constrained optimization model: weaker subjects and closer exams get more time, while a rotating "maintenance slot" makes sure no subject goes untouched for too long.

**Key features:**
- Weighted scoring from your last 3 exam results, blended with a self-rated confidence score
- A diminishing-returns model (based on Lagrange multipliers) that fairly splits limited study hours across subjects
- No study time scheduled for a subject on its own exam day
- Content deadlines and practice test dates, with a cap of 3 practice tests per day (and automatic alternative-day suggestions if you go over)
- An auto-updating calendar that greys out days as they pass — no need to regenerate manually
- Downloadable as CSV or as a standalone, shareable HTML calendar
- Click any day for a full breakdown in a popup

## Why

Built as a STEM project exploring how constrained optimization (the same math behind resource allocation problems in economics and operations research) can be applied to something every student deals with: how to actually divide limited study time.

## Tech stack

Python, Streamlit, pandas

## Running it locally

```bash
pip install -r requirements.txt
streamlit run study_planner_app.py
```

## Status

Actively in development — feedback welcome via the in-app feedback form.
