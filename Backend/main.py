"""Small standard-library HTTP API that delegates recommendations to ``ai``.

Run from the repository root with ``python -m Backend.main``.
"""

import json
import os
import sqlite3
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ai.data import load_dataset
from ai.recommendation import recommend


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("CAREER_QUEST_DATA_DIR", ROOT / "case_1" / "career_quest_dataset"))
DB_PATH = Path(os.environ.get("CAREER_QUEST_DB", Path(__file__).with_name("career_quest.sqlite3")))
GRADES = ("Junior", "Middle", "Senior", "Lead")


def _db():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _initialize_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS activity_history (
                record_id TEXT PRIMARY KEY,
                employee_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                date TEXT NOT NULL,
                due_date TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                completion_pct TEXT NOT NULL,
                score TEXT NOT NULL DEFAULT '',
                feedback_rating TEXT NOT NULL DEFAULT '',
                assigned_by TEXT NOT NULL
            )"""
        )


def _dataset():
    employees, events, skills, base_history = load_dataset(DATA_DIR)
    with _db() as connection:
        live_history = [dict(row) for row in connection.execute(
            "SELECT * FROM activity_history ORDER BY date, record_id"
        )]
    return employees, events, skills, base_history + live_history


def _employee(employee_id, employees):
    matches = [row for row in employees["employees"] if row["employee_id"] == employee_id]
    if not matches:
        raise KeyError(employee_id)
    return matches[0]


def _target(employee, skills):
    goal = employee.get("career_goal")
    role = goal["target_role"] if goal else employee["role"]
    grade = goal["target_grade"] if goal else GRADES[min(GRADES.index(employee["grade"]) + 1, 3)]
    profiles = [p for p in skills["role_profiles"] if (p["role"], p["grade"]) == (role, grade)]
    if len(profiles) != 1:
        raise ValueError(f"No unique role profile for {role}/{grade}")
    return role, grade, profiles[0]


def _current_levels(employee, events, skills, history):
    levels = dict(employee.get("skills", {}))
    events_by_id = {event["event_id"]: event for event in events["events"]}
    cutoff = date.fromisoformat(employee["last_review_date"])
    snapshot = date.fromisoformat(skills["meta"]["as_of_date"])
    rows = sorted(
        (row for row in history if row["employee_id"] == employee["employee_id"]
         and row["status"] == "completed"
         and cutoff < date.fromisoformat(row["date"]) <= snapshot),
        key=lambda row: (row["date"], row["record_id"]),
    )
    for row in rows:
        event = events_by_id[row["event_id"]]
        for effect in event["develops_skills"]:
            sid = effect["skill_id"]
            before = levels.get(sid, 0)
            levels[sid] = min(effect["max_level"], before + effect["gain"])
    return levels


def employee_payload(employee, events, skills, history):
    role, grade, profile = _target(employee, skills)
    levels = _current_levels(employee, events, skills, history)
    names = {skill["skill_id"]: skill["name"] for skill in skills["skills"]}
    critical = set(profile["critical_skills"])
    trajectory = []
    for sid, required in profile["required_skills"].items():
        current = levels.get(sid, 0)
        trajectory.append({
            "skill_id": sid,
            "name": names[sid],
            "current_level": current,
            "required_level": required,
            "gap": max(0, required - current),
            "critical": sid in critical,
        })
    trajectory.sort(key=lambda item: (not item["critical"], -item["gap"], item["skill_id"]))
    return {
        "employee": employee,
        "trajectory": {
            "target_role": role,
            "target_grade": grade,
            "skills": trajectory,
            "completion_pct": round(
                100 * sum(min(x["current_level"], x["required_level"]) for x in trajectory)
                / sum(x["required_level"] for x in trajectory), 1
            ) if trajectory and sum(x["required_level"] for x in trajectory) else 100.0,
        },
    }


def _recommendations(employee, events, skills, history):
    return recommend(employee, events, skills, history)


def hr_summary(employees, events, skills, history):
    names = {skill["skill_id"]: skill["name"] for skill in skills["skills"]}
    deficits = {sid: 0 for sid in names}
    employees_without_steps = 0
    for employee in employees["employees"]:
        _, _, profile = _target(employee, skills)
        levels = _current_levels(employee, events, skills, history)
        gaps = [sid for sid, required in profile["required_skills"].items()
                if levels.get(sid, 0) < required]
        for sid in gaps:
            deficits[sid] += 1
        if gaps and not _recommendations(employee, events, skills, history):
            employees_without_steps += 1
    participation = {}
    events_by_id = {event["event_id"]: event for event in events["events"]}
    for row in history:
        event = events_by_id[row["event_id"]]
        if event["mandatory"]:
            continue
        item = participation.setdefault(row["event_id"], {"event_id": row["event_id"],
            "title": event["title"], "participants": set(), "completed": 0})
        item["participants"].add(row["employee_id"])
        item["completed"] += row["status"] == "completed"
    return {
        "employees": len(employees["employees"]),
        "skills_with_gaps": [
            {"skill_id": sid, "name": names[sid], "employees": count}
            for sid, count in sorted(deficits.items(), key=lambda pair: (-pair[1], pair[0])) if count
        ][:10],
        "employees_without_recommendation": employees_without_steps,
        "participation": [
            {"event_id": item["event_id"], "title": item["title"],
             "participants": len(item["participants"]), "completed_records": item["completed"]}
            for item in sorted(participation.values(), key=lambda item: item["event_id"])
        ],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "CareerQuestBackend/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path == "/api/health":
                return self._send(200, {"status": "ok", "data_dir": str(DATA_DIR)})
            employees, events, skills, history = _dataset()
            if path == "/api/employees":
                term = query.get("q", [""])[0].casefold()
                rows = [row for row in employees["employees"] if not term or term in " ".join(
                    str(row.get(key, "")) for key in ("employee_id", "full_name", "role", "department")
                ).casefold()]
                return self._send(200, {"employees": rows, "total": len(rows)})
            if path == "/api/hr/summary":
                return self._send(200, hr_summary(employees, events, skills, history))
            parts = path.strip("/").split("/")
            if len(parts) in (3, 4) and parts[:2] == ["api", "employees"]:
                person = _employee(parts[2], employees)
                if len(parts) == 3:
                    payload = employee_payload(person, events, skills, history)
                    payload["recommendations"] = _recommendations(person, events, skills, history)
                    return self._send(200, payload)
                if parts[3] == "recommendations":
                    return self._send(200, {"recommendations": _recommendations(person, events, skills, history)})
            return self._send(404, {"error": "Route not found"})
        except KeyError as exc:
            return self._send(404, {"error": f"Unknown employee: {exc.args[0]}"})
        except (ValueError, OSError) as exc:
            return self._send(500, {"error": str(exc)})

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        parts = path.strip("/").split("/")
        if len(parts) != 6 or parts[:2] != ["api", "employees"] or parts[3] != "activities" or parts[5] != "complete":
            return self._send(404, {"error": "Route not found"})
        employee_id, event_id = parts[2], parts[4]
        try:
            employees, events, skills, history = _dataset()
            person = _employee(employee_id, employees)
            options = _recommendations(person, events, skills, history)
            event = next((item for item in events["events"] if item["event_id"] == event_id), None)
            if event is None:
                return self._send(404, {"error": f"Unknown event: {event_id}"})
            if not any(item["event_id"] == event_id for item in options):
                return self._send(409, {"error": "Activity is not currently recommended for this employee"})
            body_size = int(self.headers.get("Content-Length", "0"))
            if body_size > 65536:
                return self._send(413, {"error": "Request body too large"})
            body = json.loads(self.rfile.read(body_size) or b"{}")
            if not isinstance(body, dict):
                return self._send(400, {"error": "Expected a JSON object"})
            as_of = date.fromisoformat(skills["meta"]["as_of_date"]).isoformat()
            record_id = "LIVE_" + os.urandom(12).hex()
            with _db() as connection:
                connection.execute(
                    """INSERT INTO activity_history
                       (record_id, employee_id, event_id, date, due_date, status,
                        completion_pct, score, feedback_rating, assigned_by)
                       VALUES (?, ?, ?, ?, '', 'completed', '100', '', '', 'self')""",
                    (record_id, employee_id, event_id, as_of),
                )
            updated = _dataset()
            return self._send(200, {
                "completed": True,
                "record_id": record_id,
                "profile": employee_payload(person, updated[1], updated[2], updated[3]),
                "recommendations": _recommendations(person, updated[1], updated[2], updated[3]),
            })
        except KeyError as exc:
            return self._send(404, {"error": f"Unknown employee: {exc.args[0]}"})
        except (ValueError, json.JSONDecodeError) as exc:
            return self._send(400, {"error": str(exc)})
        except sqlite3.IntegrityError:
            return self._send(409, {"error": "Could not save this completion"})
        except (OSError, sqlite3.Error) as exc:
            return self._send(500, {"error": str(exc)})


def main():
    _initialize_db()
    # Load on startup so a wrong/missing dataset fails before the server accepts requests.
    _dataset()
    host = os.environ.get("CAREER_QUEST_HOST", "127.0.0.1")
    port = int(os.environ.get("CAREER_QUEST_PORT", "8000"))
    print(f"Career Quest API listening at http://{host}:{port}")
    print(f"Dataset: {DATA_DIR}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
