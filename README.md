# Career Quest

Career Quest is a small employee-development app built for the HackAlem AI
Halyk Bank case. Employees can inspect a career target, review skill gaps,
choose an explained next activity, and record completion. HR gets aggregated
skill-gap and participation views.

## Run the whole app

Requires Python 3.9 or newer. The backend uses only the Python standard library;
no package installation is needed.

From the repository root:

```sh
python -m Backend.main
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The same server provides the
web interface and JSON API. By default it loads the supplied synthetic case data
from `case_1/career_quest_dataset/` and stores completed activity records in
`Backend/career_quest.sqlite3`.

On Windows, if `python` is not on PATH, use the installed Python launcher:

```powershell
py -3 -m Backend.main
```

## What works

- Select any employee from the profile picker or open a profile from the HR table.
- View current and target grades, skill levels, progress, and recent activity.
- Get up to three ranked recommendations from the `ai` package. Ranking uses
  target-grade requirements, critical skill gaps, event impact, eligibility,
  prerequisites, schedule, and voluntary participation history. Each result
  includes a deterministic explanation and the underlying scoring factors.
- Complete an eligible recommendation. The backend persists it in SQLite and
  returns the recalculated trajectory and next recommendations.
- Review HR skill gaps, active development, participation, and employees who have
  skill gaps but no eligible next activity. The app does not show a public
  employee leaderboard.

The recommendation engine is rules-and-ranking based and works offline. The AI
package also offers a provider-neutral optional LLM explanation adapter; no
provider credentials are configured by default.

## Use another evaluation dataset

The loader accepts the same four-file schema described in
`case_1/career_quest_dataset/README.ru.md`. Set the data directory before startup
to load jury profiles and their history without editing application code.

PowerShell:

```powershell
$env:CAREER_QUEST_DATA_DIR = "C:\path\to\evaluation-dataset"
py -3 -m Backend.main
```

The directory must contain `employees.json`, `events.json`, `skills.json`, and
`activity_history.csv` with matching snapshot dates. Optional settings are
`CAREER_QUEST_HOST`, `CAREER_QUEST_PORT`, and `CAREER_QUEST_DB`.

## Checks

```sh
python -m unittest ai.test_recommendation -v
```

The tests cover recommendation scoring, eligibility, history, progress replay,
and the bundled AI data. The backend API itself can be inspected at
`/api/health`, `/api/employees`, `/api/hr/summary`, and
`/api/employees/E0002` while the server is running.

All supplied profiles and activity records are synthetic. This hackathon demo
does not implement authentication or production access controls.
