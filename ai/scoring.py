"""Bounded, inspectable scoring; no network or input mutation."""

WEIGHTS = {"grade_importance": 0.35, "skill_gap": 0.30,
           "event_impact": 0.20, "history_fit": 0.15}


def history_score(skill_ids, history, events_by_id):
    """Count overlapping voluntary activities, once per participation record."""
    counts = dict.fromkeys(
        ("completed", "no_show", "dropped", "declined", "in_progress", "overdue"), 0
    )
    for row in history:
        event = events_by_id[row["event_id"]]
        if event["mandatory"]:
            continue
        if skill_ids.intersection(x["skill_id"] for x in event["develops_skills"]):
            counts[row["status"]] += 1
    positive = counts["completed"]
    negative = counts["no_show"] + counts["dropped"] + 2 * counts["declined"]
    value = (positive + 1) / (positive + negative + 2)
    return {"counts": counts, "fit": value,
            "signal": "positive" if value > 0.5 else "negative" if value < 0.5 else "neutral"}


def score_event(effects, all_gaps, critical_skills, history):
    """Critical gaps carry double weight in coverage; all components lie in [0, 1]."""
    def weight(skill_id):
        return 2 if skill_id in critical_skills else 1

    total_gap = sum(weight(sid) * gap for sid, gap in all_gaps.items())
    components = {
        "grade_importance": 1.0 if any(x["critical"] for x in effects) else 0.5,
        "skill_gap": max(x["gap"] / 5 for x in effects),
        "event_impact": sum(weight(x["skill_id"]) * x["gap_closed"] for x in effects) / total_gap,
        "history_fit": history["fit"],
    }
    contributions = {key: WEIGHTS[key] * value for key, value in components.items()}
    return sum(contributions.values()), components, contributions
