"""SQLite persistence, transactional completion/import, and HR aggregates."""

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from collections import Counter
from datetime import date

from ai.data import load_dataset
from ai.profile import effective_skills, trajectory
from ai.recommendation import recommend, GRADES, STATUSES, REPEATABLE_EVENTS

ROOT = Path(__file__).resolve().parents[1]


class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 200_000).hex()


class Store:
    def __init__(self, path, dataset=ROOT / 'ai/data'):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        os.chmod(self.path, 0o600)
        self.db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS employees (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS history (id TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES employees(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, salt TEXT NOT NULL, hash TEXT NOT NULL, role TEXT NOT NULL, employee_id TEXT REFERENCES employees(id));
            CREATE TABLE IF NOT EXISTS completions (employee_id TEXT NOT NULL, event_id TEXT NOT NULL, day TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(employee_id,event_id,day));
        ''')
        if not self.db.execute('SELECT 1 FROM metadata LIMIT 1').fetchone():
            employees, events, skills, history = load_dataset(dataset)
            with self.db:
                for key, value in [('events', events['events']), ('skills', skills), ('as_of', skills['meta']['as_of_date'])]:
                    self.db.execute('INSERT INTO metadata VALUES (?,?)', (key, json.dumps(value)))
                self.db.executemany('INSERT INTO employees VALUES (?,?)', [(e['employee_id'], json.dumps(e)) for e in employees['employees']])
                self.db.executemany('INSERT INTO history VALUES (?,?,?)', [(r['record_id'], r['employee_id'], json.dumps(r)) for r in history])
                credentials = {'hr': self._create_user('hr', 'hr', None)}
                for employee_id in ['E0002', 'E0004', 'E0151']:
                    if any(e['employee_id'] == employee_id for e in employees['employees']):
                        credentials[employee_id] = self._create_user(employee_id, 'employee', employee_id)
            cred_path = self.path.parent / 'credentials.json'
            descriptor = os.open(cred_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(credentials, stream, ensure_ascii=False, indent=2)
        self.events = self.meta('events')
        self.skills = self.meta('skills')
        self.as_of = self.meta('as_of')

    def meta(self, key):
        return json.loads(self.db.execute('SELECT data FROM metadata WHERE key=?', (key,)).fetchone()[0])

    def _create_user(self, username, role, employee_id):
        password, salt = secrets.token_urlsafe(15), secrets.token_hex(16)
        self.db.execute('INSERT INTO users VALUES (?,?,?,?,?)', (username, salt, password_hash(password, salt), role, employee_id))
        return password

    def authenticate(self, username, password):
        with self.lock:
            row = self.db.execute('SELECT salt,hash,role,employee_id FROM users WHERE username=?', (username,)).fetchone()
        salt = row[0] if row else '00' * 16
        actual = password_hash(password, salt)
        if not row or not secrets.compare_digest(actual, row[1]):
            raise Problem(401, 'Неверный логин или пароль')
        return {'username': username, 'role': row[2], 'employee_id': row[3]}

    def employee(self, employee_id):
        row = self.db.execute('SELECT data FROM employees WHERE id=?', (employee_id,)).fetchone()
        if not row:
            raise Problem(404, 'Сотрудник не найден')
        return json.loads(row[0])

    def employees(self):
        return [json.loads(row[0]) for row in self.db.execute('SELECT data FROM employees ORDER BY id')]

    def history(self, employee_id=None):
        if employee_id is None:
            rows = self.db.execute('SELECT data FROM history')
        else:
            rows = self.db.execute('SELECT data FROM history WHERE employee_id=?', (employee_id,))
        return sorted([json.loads(row[0]) for row in rows], key=lambda r: (r['date'], r['record_id']))

    def recommendations(self, employee_id):
        with self.lock:
            employee = self.employee(employee_id)
            history = self.history(employee_id)
            return recommend(employee, self.events, self.skills, history, as_of_date=self.as_of)

    def profile(self, employee_id):
        with self.lock:
            employee = self.employee(employee_id)
            history = self.history(employee_id)
            titles = {e['event_id']: e['title'] for e in self.events}
            return {'employee': employee, 'as_of_date': self.as_of,
                    'trajectory': trajectory(employee, self.events, self.skills, history, self.as_of),
                    'history': [{**r, 'title': titles[r['event_id']]} for r in reversed(history)],
                    'recommendations': self.recommendations(employee_id)}

    def complete(self, employee_id, event_id):
        with self.lock, self.db:
            # The primary key plus transaction makes retries/concurrent clicks idempotent.
            prior = self.db.execute('SELECT data FROM completions WHERE employee_id=? AND event_id=? AND day=?', (employee_id, event_id, self.as_of)).fetchone()
            if prior:
                return {**json.loads(prior[0]), 'already_completed': True, 'profile': self.profile(employee_id)}
            employee = self.employee(employee_id)
            history = self.history(employee_id)
            event = next((e for e in self.events if e['event_id'] == event_id), None)
            if event is None:
                raise Problem(404, 'Активность не найдена')
            # Completion is offered for the currently recommended, eligible next steps.
            if event_id not in {r['event_id'] for r in self.recommendations(employee_id)}:
                raise Problem(409, 'Активность уже завершена или недоступна. Обновите рекомендации.')
            before = trajectory(employee, self.events, self.skills, history, self.as_of)
            levels = effective_skills(employee, self.events, history, self.as_of)
            changes = []
            for effect in event['develops_skills']:
                sid = effect['skill_id']
                old = levels.get(sid, 0)
                new = old + max(0, min(effect['gain'], effect['max_level'] - old))
                levels[sid] = new
                if new != old:
                    changes.append({'skill_id': sid, 'before': old, 'after': new})
            # Materialize the full skill snapshot, preserving the real review date.
            # Existing historical gains are not applied twice on subsequent requests.
            employee.setdefault('reviewed_skills', dict(employee['skills']))
            employee['skills'] = levels
            employee['skill_snapshot_date'] = self.as_of
            self.db.execute('UPDATE employees SET data=? WHERE id=?', (json.dumps(employee), employee_id))
            record = {'record_id': 'APP_' + secrets.token_hex(12), 'employee_id': employee_id,
                      'event_id': event_id, 'date': self.as_of, 'due_date': '', 'status': 'completed',
                      'completion_pct': '100', 'score': '', 'feedback_rating': '', 'assigned_by': 'self'}
            self.db.execute('INSERT INTO history VALUES (?,?,?)', (record['record_id'], employee_id, json.dumps(record)))
            result = {'already_completed': False, 'changes': changes,
                      'progress_before': before['progress_pct'],
                      'progress_after': trajectory(employee, self.events, self.skills, self.history(employee_id), self.as_of)['progress_pct']}
            self.db.execute('INSERT INTO completions VALUES (?,?,?,?)', (employee_id, event_id, self.as_of, json.dumps(result)))
            return {**result, 'profile': self.profile(employee_id)}

    def hr_summary(self):
        with self.lock:
            all_employees, all_history = self.employees(), self.history()
            gaps, no_steps, inactive = Counter(), [], []
            by_employee = {}
            for row in all_history:
                by_employee.setdefault(row['employee_id'], []).append(row)
            for employee in all_employees:
                eid = employee['employee_id']
                rows = by_employee.get(eid, [])
                path = trajectory(employee, self.events, self.skills, rows, self.as_of)
                gaps.update(s['skill_id'] for s in path['skills'] if s['gap'] > 0)
                brief = {key: employee[key] for key in ('employee_id', 'full_name', 'role', 'grade')}
                if not recommend(employee, self.events, self.skills, rows, as_of_date=self.as_of):
                    no_steps.append(brief)
                voluntary = {e['event_id'] for e in self.events if not e['mandatory']}
                active_dates = [r['date'] for r in rows if r['event_id'] in voluntary and r['status'] == 'completed' and r['date'] <= self.as_of]
                last = max(active_dates, default=None)
                if last is None or (date.fromisoformat(self.as_of) - date.fromisoformat(last)).days >= 90:
                    inactive.append({**brief, 'last_completed': last})
            names = {s['skill_id']: s['name'] for s in self.skills['skills']}
            participation = []
            for event in self.events:
                rows = [r for r in all_history if r['event_id'] == event['event_id'] and r['date'] <= self.as_of]
                counts = Counter(r['status'] for r in rows)
                participation.append({'event_id': event['event_id'], 'title': event['title'],
                                      'mandatory': event['mandatory'], 'total': len(rows),
                                      'unique_employees': len({r['employee_id'] for r in rows}),
                                      'statuses': {s: counts[s] for s in sorted(STATUSES)},
                                      'completion_pct': round(100 * counts['completed'] / len(rows), 1) if rows else 0})
            return {'as_of_date': self.as_of, 'employee_count': len(all_employees),
                    'skill_gaps': [{'skill_id': sid, 'name': names[sid], 'employees': n} for sid, n in sorted(gaps.items(), key=lambda x: (-x[1], x[0]))],
                    'no_next_step': no_steps, 'inactive_90_days': inactive, 'participation': participation}

    def import_data(self, payload):
        """Merge profiles and participation records atomically, never truncate history."""
        employees = payload.get('employees', [])
        if isinstance(employees, dict):
            meta = employees.get('meta', {})
            if meta.get('as_of_date', self.as_of) != self.as_of:
                raise Problem(422, 'Дата импортируемого набора должна совпадать с датой среза')
            employees = employees.get('employees')
        raw_history = payload.get('history', [])
        if isinstance(raw_history, str):
            if len(raw_history) > 3_000_000:
                raise Problem(413, 'CSV слишком большой')
            history = list(csv.DictReader(io.StringIO(raw_history.lstrip('\ufeff'))))
        else:
            history = raw_history
        if not isinstance(employees, list) or not isinstance(history, list) or not (employees or history):
            raise Problem(422, 'Передайте employees JSON и/или history CSV')
        if len(employees) > 1000 or len(history) > 20000:
            raise Problem(413, 'Лимит: 1000 профилей и 20000 строк за импорт')
        with self.lock:
            current_employees = {e['employee_id']: e for e in self.employees()}
            current_history = {r['record_id']: r for r in self.history()}
            seen = set()
            try:
                known_profiles = {(p['role'], p['grade']) for p in self.skills['role_profiles']}
                known_skills = {s['skill_id'] for s in self.skills['skills']}
                for employee in employees:
                    eid = employee['employee_id']
                    if not isinstance(eid, str) or not eid or len(eid) > 100 or eid in seen:
                        raise ValueError('Некорректный или повторный employee_id')
                    seen.add(eid)
                    for key in ['full_name', 'role', 'grade', 'department', 'last_review_date']:
                        if not isinstance(employee[key], str) or not employee[key] or len(employee[key]) > 250:
                            raise ValueError('Некорректное поле ' + key)
                    if (employee['role'], employee['grade']) not in known_profiles:
                        raise ValueError('Неизвестная роль или грейд')
                    review = date.fromisoformat(employee['last_review_date'])
                    if review > date.fromisoformat(self.as_of):
                        raise ValueError('Оценка навыков позже даты среза')
                    if not isinstance(employee['skills'], dict) or not set(employee['skills']).issubset(known_skills):
                        raise ValueError('Неизвестные навыки')
                    if employee.get('career_goal') and (employee['career_goal']['target_role'], employee['career_goal']['target_grade']) not in known_profiles:
                        raise ValueError('Неизвестная карьерная цель')
                    if self.db.execute('SELECT 1 FROM completions WHERE employee_id=?', (eid,)).fetchone() and current_employees.get(eid) != employee:
                        raise ValueError('Профиль имеет сохранённый прогресс; импортируйте проверочный профиль с новым ID')
                    current_employees[eid] = employee
                seen_records = set()
                event_ids = {e['event_id'] for e in self.events}
                for row in history:
                    rid = row['record_id']
                    if not isinstance(rid, str) or not rid or len(rid) > 100 or rid in seen_records:
                        raise ValueError('Некорректный или повторный record_id')
                    seen_records.add(rid)
                    if row['employee_id'] not in current_employees or row['event_id'] not in event_ids:
                        raise ValueError('История ссылается на неизвестного сотрудника или событие')
                    if row['status'] not in STATUSES or date.fromisoformat(row['date']) > date.fromisoformat(self.as_of):
                        raise ValueError('Некорректная дата или статус истории')
                    pct = int(row['completion_pct'])
                    if not 0 <= pct <= 100 or (row['status'] == 'completed' and pct != 100):
                        raise ValueError('Некорректный completion_pct')
                    if rid in current_history and current_history[rid] != row:
                        raise ValueError('record_id уже существует с другим содержимым')
                    current_history[rid] = row
                # Validate the engine against each affected profile before any writes.
                affected = seen | {r['employee_id'] for r in history}
                merged_rows = list(current_history.values())
                for eid in affected:
                    recommend(current_employees[eid], self.events, self.skills, merged_rows, as_of_date=self.as_of)
            except (ValueError, TypeError, KeyError, StopIteration) as exc:
                raise Problem(422, 'Импорт отклонён: ' + str(exc)) from exc
            with self.db:
                for employee in employees:
                    self.db.execute('INSERT INTO employees VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (employee['employee_id'], json.dumps(employee)))
                for row in history:
                    self.db.execute('INSERT OR IGNORE INTO history VALUES (?,?,?)', (row['record_id'], row['employee_id'], json.dumps(row)))
            return {'employees_received': len(employees), 'history_received': len(history), 'affected_employees': sorted(affected)}

    def close(self):
        self.db.close()
