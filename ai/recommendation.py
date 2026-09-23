"""Recommendations for the actual Career Quest v1 dataset schema."""

from datetime import date
from math import isfinite

from .explain import explain
from .scoring import history_score, score_event

GRADES = ("Junior", "Middle", "Senior", "Lead")
STATUSES = {"completed", "in_progress", "dropped", "no_show", "declined", "overdue"}
REPEATABLE_EVENTS = frozenset({"EV_036"})


def _date(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def _level(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not 0 <= value <= 5:
        raise ValueError(f"Invalid skill level/gain: {value!r}; expected a number in [0, 5]")
    return value


def _index(rows, key):
    result = {}
    for row in rows:
        identity = row[key]
        if identity in result:
            raise ValueError(f"Duplicate {key}: {identity}")
        result[identity] = row
    return result


def recommend(employee, events, skills, history, *, as_of_date=None, limit=3):
    """Return 0–3 recommendations, without I/O or mutations.

    employee: one employees.json employee; events: events.json or its events list;
    skills: entire skills.json (including role_profiles); history: CSV row dicts.
    as_of_date defaults to skills.meta.as_of_date, never the machine's clock.
    Explicit career_goal takes precedence, else use the next grade; Lead without
    a goal develops remaining Lead requirements. Missing skills have level zero.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 3:
        raise ValueError("limit must be an integer from 1 to 3")
    today = _date(as_of_date or skills["meta"]["as_of_date"])
    if not isinstance(today, date):
        raise ValueError("as_of_date must be an ISO date or datetime.date")
    review = _date(employee.get("skill_snapshot_date", employee["last_review_date"]))
    if review > today:
        raise ValueError("Skill snapshot is after as_of_date")
    if review < _date(employee["last_review_date"]):
        raise ValueError("Skill snapshot precedes last_review_date")
    catalog = events["events"] if isinstance(events, dict) else events
    events_by_id = _index(catalog, "event_id")
    skill_names = {sid: item["name"] for sid, item in _index(skills["skills"], "skill_id").items()}
    levels = dict(employee["skills"])
    for sid, value in levels.items():
        if sid not in skill_names:
            raise ValueError(f"Unknown skill: {sid}")
        _level(value)
    for event in catalog:
        seen = set()
        for effect in event["develops_skills"]:
            sid = effect["skill_id"]
            if sid not in skill_names or sid in seen:
                raise ValueError(f"Unknown or duplicate developed skill: {sid}")
            seen.add(sid)
            _level(effect["gain"])
            _level(effect["max_level"])
        for sid, required in event["prerequisites"].items():
            if sid not in skill_names:
                raise ValueError(f"Unknown prerequisite skill: {sid}")
            _level(required)

    role, grade = employee["role"], employee["grade"]
    if grade not in GRADES:
        raise ValueError(f"Unknown grade: {grade}")
    goal = employee.get("career_goal")
    target_role = goal["target_role"] if goal else role
    target_grade = goal["target_grade"] if goal else GRADES[min(GRADES.index(grade) + 1, 3)]
    profiles = [p for p in skills["role_profiles"] if (p["role"], p["grade"]) == (target_role, target_grade)]
    if len(profiles) != 1:
        raise ValueError(f"Expected exactly one role profile: {target_role}/{target_grade}")
    profile = profiles[0]
    required = profile["required_skills"]
    for sid, value in required.items():
        if sid not in skill_names:
            raise ValueError(f"Unknown required skill: {sid}")
        _level(value)
    critical = set(profile["critical_skills"])
    if not critical.issubset(required):
        raise ValueError("Critical skills must be present in required_skills")

    relevant = []
    seen_records = set()
    for row in history:
        if row["employee_id"] != employee["employee_id"]:
            continue
        record_id = row["record_id"]
        if record_id in seen_records:
            raise ValueError(f"Duplicate history record: {record_id}")
        seen_records.add(record_id)
        if row["status"] not in STATUSES:
            raise ValueError(f'Unknown history status: {row["status"]}')
        if row["event_id"] not in events_by_id:
            raise ValueError(f'Unknown historical event: {row["event_id"]}')
        if _date(row["date"]) <= today:
            relevant.append(row)
    relevant.sort(key=lambda r: (r["date"], r["record_id"]))
    completed = set()
    latest_status = {}
    adjustments = []
    for row in relevant:
        eid = row["event_id"]
        latest_status[eid] = row["status"]
        if row["status"] != "completed":
            continue
        if eid in completed and eid not in REPEATABLE_EVENTS and not events_by_id[eid]["mandatory"]:
            raise ValueError(f"Non-repeatable event completed twice: {eid}")
        completed.add(eid)
        if _date(row["date"]) <= review:
            continue
        for effect in events_by_id[eid]["develops_skills"]:
            sid = effect["skill_id"]
            before = levels.get(sid, 0)
            gain = max(0, min(effect["gain"], effect["max_level"] - before))
            if gain:
                levels[sid] = before + gain
                adjustments.append({"record_id": row["record_id"], "event_id": eid,
                                    "skill_id": sid, "date": row["date"],
                                    "before": before, "after": before + gain})
    gaps = {sid: need - levels.get(sid, 0) for sid, need in required.items() if need > levels.get(sid, 0)}
    if not gaps:
        return []

    candidates = []
    for event in catalog:
        eid = event["event_id"]
        # Target-role courses are available for an explicit career transition,
        # but a desired grade alone never qualifies the employee for a course.
        if event["mandatory"] or not {role, target_role}.intersection(event["target_roles"]):
            continue
        if grade not in event["target_grades"]:
            continue
        if eid in completed and eid not in REPEATABLE_EVENTS:
            continue
        if eid in REPEATABLE_EVENTS and any(
            row["event_id"] == eid and row["status"] == "completed"
            and _date(row["date"]) == today for row in relevant
        ):
            # One credited club participation per snapshot day; a retry must
            # not offer the same completion again before the next session.
            continue
        if latest_status.get(eid) == "in_progress":
            continue
        if any(levels.get(sid, 0) < need for sid, need in event["prerequisites"].items()):
            continue
        upcoming = sorted(s for s in event["upcoming_sessions"] if _date(s) >= today)
        if event["format"] != "self_paced" and not upcoming:
            continue
        effects = []
        for effect in event["develops_skills"]:
            sid = effect["skill_id"]
            current = levels.get(sid, 0)
            gain = max(0, min(effect["gain"], effect["max_level"] - current))
            if sid not in gaps or gain <= 0:
                continue
            effects.append({"skill_id": sid, "name": skill_names[sid],
                            "reviewed_level": employee.get("reviewed_skills", employee["skills"]).get(sid, 0),
                            "current_level": current, "required_level": required[sid],
                            "gap": gaps[sid], "gain": effect["gain"],
                            "max_level": effect["max_level"], "effective_gain": gain,
                            "gap_closed": min(gain, gaps[sid]), "projected_level": current + gain,
                            "critical": sid in critical})
        if not effects:
            continue
        effects.sort(key=lambda x: (not x["critical"], -x["gap_closed"], x["skill_id"]))
        hist = history_score({x["skill_id"] for x in effects}, relevant, events_by_id)
        score, components, contributions = score_event(effects, gaps, critical, hist)
        factors = {"current_role": role, "current_grade": grade,
                   "target_role": target_role, "target_grade": target_grade,
                   "target_source": "career_goal" if goal else "current_grade" if grade == "Lead" else "next_grade",
                   "as_of_date": today.isoformat(), "skills": effects,
                   "history": hist, "components": components, "contributions": contributions,
                   "skill_adjustments": adjustments}
        candidates.append({"event_id": eid, "title": event["title"], "score": score,
                           "next_session": upcoming[0] if upcoming else None,
                           "duration_hours": event["duration_hours"],
                           "factors": factors, "reason": explain(factors)})
    candidates.sort(key=lambda r: (-r["score"], r["event_id"]))
    return candidates[:limit]
