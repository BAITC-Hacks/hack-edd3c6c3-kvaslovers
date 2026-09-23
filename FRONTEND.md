# Career Quest frontend

The frontend is a dependency-free HTML/CSS/JavaScript application. It expects the
Career Quest API at `http://127.0.0.1:8000/api` by default.

## Local run

From the repository root, start the API:

```sh
python -m Backend.main
```

In a second terminal, serve the repository root:

```sh
python -m http.server 8080
```

Open `http://127.0.0.1:8080/`.

For a different API address, define `window.CAREER_QUEST_API` before `app.js` is
loaded. The value must include the `/api` prefix.

## Implemented flows

- Select any employee from the API dataset.
- View target grade, trajectory, priority skills and progress.
- Switch between all 1–3 AI recommendations.
- Complete a recommendation and immediately receive updated progress and next steps.
- Open an employee from the HR directory.
- Search and progressively load the employee directory.
- View backend-derived HR gap and participation metrics.
- Use English, Russian or Kazakh labels for the main employee flow.
- Continue in clearly marked demo mode when the API is unavailable.

The current backend does not expose employee activity history. Completions made in
the interface are therefore stored per employee in browser `localStorage` and shown
in Recent activity. Replace this with `GET /api/employees/{id}/history` when that
endpoint becomes available.
