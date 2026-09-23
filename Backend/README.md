# Career Quest backend

Backend is a dependency-free HTTP server. It serves the frontend files, calls
the recommendation function in `ai/` directly, and stores completed activities
in a local SQLite database.

From the repository root, start it with:

```sh
python -m Backend.main
```

Open `http://127.0.0.1:8000` after startup. The default dataset is
`case_1/career_quest_dataset/`. Set
`CAREER_QUEST_DATA_DIR` to use another directory with the same four files.
Optional settings: `CAREER_QUEST_HOST` (defaults to `127.0.0.1`),
`CAREER_QUEST_PORT` (defaults to `8000`) and `CAREER_QUEST_DB` (defaults to
`Backend/career_quest.sqlite3`).

## API

- `GET /api/health` — server and dataset location.
- `GET /api/employees?q=...` — employee search.
- `GET /api/employees/{employee_id}` — employee profile, target-grade trajectory,
  recent activity, and recommendations.
- `GET /api/employees/{employee_id}/recommendations` — 1–3 AI recommendations.
- `POST /api/employees/{employee_id}/activities/{event_id}/complete` — record a
  currently recommended activity; returns updated trajectory and recommendations.
- `GET /api/hr/summary` — aggregate skill gaps, employees without a next step, and
  voluntary activity participation plus HR employee rows.

The recommendation engine remains the source of truth for eligibility, scoring,
and explanations. The backend passes its structured results through unchanged.
Completed activities are appended to history in SQLite and affect subsequent
recommendations and skill trajectories. This local demo API is intended to run
on the developer machine; it does not implement employee authentication or
production authorization.
