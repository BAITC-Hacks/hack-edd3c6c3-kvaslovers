"""SQLite persistence, transactional completion/import, and HR aggregates."""

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from collections import Counter
from datetime import date, datetime, timezone

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


def _development_status(latest, has_recommendation, has_gap):
    if latest is None:
        return 'Нет активности'
    labels = {'in_progress': 'В процессе', 'dropped': 'Прекращено', 'no_show': 'Неявка',
              'declined': 'Отказ', 'overdue': 'Просрочено'}
    if latest['status'] in labels:
        return labels[latest['status']]
    if has_recommendation:
        return 'Есть следующий шаг'
    return 'Нет подходящей рекомендации' if has_gap else 'Цель достигнута'


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
            CREATE UNIQUE INDEX IF NOT EXISTS users_employee_unique ON users(employee_id) WHERE employee_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS registration_invites (token_hash TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES employees(id), expires_at INTEGER NOT NULL, created_by TEXT NOT NULL, used_at INTEGER);
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

    def create_registration_invite(self, employee_id, created_by, ttl_seconds=7 * 24 * 60 * 60):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = int(time.time())
        expires_at = now + ttl_seconds
        with self.lock:
            self.employee(employee_id)
            with self.db:
                if self.db.execute('SELECT 1 FROM users WHERE employee_id=?', (employee_id,)).fetchone():
                    raise Problem(409, 'У этого профиля уже есть учётная запись')
                self.db.execute(
                    'DELETE FROM registration_invites WHERE employee_id=? AND used_at IS NULL',
                    (employee_id,),
                )
                self.db.execute(
                    'INSERT INTO registration_invites VALUES (?,?,?,?,NULL)',
                    (token_hash, employee_id, expires_at, created_by),
                )
        return {'employee_id': employee_id, 'token': token,
                'expires_at': datetime.fromtimestamp(expires_at, timezone.utc).isoformat()}

    def register(self, username, password, role, employee_invite=None, invite_code=''):
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,50}', username):
            raise Problem(422, 'Логин: от 3 до 50 символов (латиница, цифры, . _ -)')
        if not isinstance(password, str) or not 8 <= len(password) <= 200:
            raise Problem(422, 'Пароль должен содержать от 8 до 200 символов')
        if role not in ('employee', 'hr'):
            raise Problem(422, 'Выберите роль employee или HR')
        if role == 'employee':
            if not isinstance(employee_invite, str) or not employee_invite or len(employee_invite) > 200:
                raise Problem(403, 'Для регистрации сотрудника нужно персональное приглашение HR')
            employee_id = None
        else:
            expected = os.environ.get('CAREER_QUEST_HR_REGISTRATION_CODE', '')
            provided = invite_code if isinstance(invite_code, str) else ''
            if not expected or not secrets.compare_digest(expected.encode(), provided.encode()):
                raise Problem(403, 'Для регистрации HR нужен действующий код приглашения')
            employee_id = None

        salt = secrets.token_hex(16)
        password_digest = password_hash(password, salt)
        now = int(time.time())
        with self.lock:
            try:
                with self.db:
                    if self.db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
                        raise Problem(409, 'Такой логин уже занят')
                    if role == 'employee':
                        token_hash = hashlib.sha256(employee_invite.encode()).hexdigest()
                        invitation = self.db.execute(
                            'SELECT employee_id FROM registration_invites '
                            'WHERE token_hash=? AND used_at IS NULL AND expires_at>?',
                            (token_hash, now),
                        ).fetchone()
                        if not invitation:
                            raise Problem(403, 'Персональное приглашение недействительно или истекло')
                        employee_id = invitation[0]
                    if employee_id and self.db.execute('SELECT 1 FROM users WHERE employee_id=?', (employee_id,)).fetchone():
                        raise Problem(409, 'Этот профиль уже связан с учётной записью')
                    self.db.execute(
                        'INSERT INTO users VALUES (?,?,?,?,?)',
                        (username, salt, password_digest, role, employee_id),
                    )
                    if role == 'employee':
                        consumed = self.db.execute(
                            'UPDATE registration_invites SET used_at=? '
                            'WHERE token_hash=? AND used_at IS NULL AND expires_at>?',
                            (now, token_hash, now),
                        )
                        if consumed.rowcount != 1:
                            raise Problem(403, 'Персональное приглашение уже использовано или истекло')
            except sqlite3.IntegrityError as exc:
                raise Problem(409, 'Логин или профиль уже используется') from exc
        return {'username': username, 'role': role, 'employee_id': employee_id}

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

    def history_payload(self, employee_id):
        with self.lock:
            self.employee(employee_id)
            titles = {event['event_id']: event['title'] for event in self.events}
            return [{**row, 'title': titles[row['event_id']]}
                    for row in reversed(self.history(employee_id))]

    def employee_rows(self, employee_ids=None):
        with self.lock:
            rows = []
            allowed = set(employee_ids) if employee_ids is not None else None
            all_history = self.history()
            linked_ids = {row[0] for row in self.db.execute(
                'SELECT employee_id FROM users WHERE employee_id IS NOT NULL'
            )}
            for employee in self.employees():
                employee_id = employee['employee_id']
                if allowed is not None and employee_id not in allowed:
                    continue
                history = [row for row in all_history if row['employee_id'] == employee_id]
                path = trajectory(employee, self.events, self.skills, history, self.as_of)
                recommendations = recommend(employee, self.events, self.skills, history, as_of_date=self.as_of)
                latest = max(history, key=lambda row: (row['date'], row['record_id']), default=None)
                gap = next((skill for skill in path['skills'] if skill['gap'] > 0), None)
                status = _development_status(latest, bool(recommendations), bool(gap))
                rows.append({
                    'employee_id': employee_id,
                    'full_name': employee['full_name'],
                    'role': employee['role'],
                    'grade': employee['grade'],
                    'department': employee.get('department', ''),
                    'has_recommendation': bool(recommendations),
                    'last_activity': latest['date'] if latest else None,
                    'last_active': latest['date'] if latest else 'Нет активности',
                    'main_skill_gap': gap['name'] if gap else 'Нет разрыва',
                    'focus': gap['name'] if gap else 'Нет разрыва',
                    'activity_status': latest['status'] if latest else None,
                    'development_status': status,
                    'status': status,
                    'progress_pct': path['progress_pct'],
                    'has_account': employee_id in linked_ids,
                })
            return rows

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
            gaps, no_steps, inactive, no_activity, employee_rows = Counter(), [], [], [], []
            progress_values = []
            active_ids = set()
            linked_ids = {row[0] for row in self.db.execute(
                'SELECT employee_id FROM users WHERE employee_id IS NOT NULL'
            )}
            by_employee = {}
            for row in all_history:
                by_employee.setdefault(row['employee_id'], []).append(row)
            voluntary = {e['event_id'] for e in self.events if not e['mandatory']}
            as_of = date.fromisoformat(self.as_of)
            for employee in all_employees:
                eid = employee['employee_id']
                rows = by_employee.get(eid, [])
                path = trajectory(employee, self.events, self.skills, rows, self.as_of)
                progress_values.append(path['progress_pct'])
                gaps.update(s['skill_id'] for s in path['skills'] if s['gap'] > 0)
                brief = {key: employee[key] for key in ('employee_id', 'full_name', 'role', 'grade')}
                recommendations = recommend(employee, self.events, self.skills, rows, as_of_date=self.as_of)
                if not recommendations:
                    no_steps.append(brief)
                skill_gap = next((skill for skill in path['skills'] if skill['gap'] > 0), None)
                latest = max(rows, key=lambda row: (row['date'], row['record_id']), default=None)
                development_status = _development_status(latest, bool(recommendations), bool(skill_gap))
                employee_rows.append({**brief,
                    'department': employee.get('department', ''),
                    'has_recommendation': bool(recommendations),
                    'last_activity': latest['date'] if latest else None,
                    'last_active': latest['date'] if latest else 'Нет активности',
                    'main_skill_gap': skill_gap['name'] if skill_gap else 'Нет разрыва',
                    'focus': skill_gap['name'] if skill_gap else 'Нет разрыва',
                    'activity_status': latest['status'] if latest else None,
                    'development_status': development_status,
                    'status': development_status,
                    'progress_pct': path['progress_pct'],
                    'has_account': eid in linked_ids})
                active_dates = [r['date'] for r in rows if r['event_id'] in voluntary and r['status'] == 'completed' and r['date'] <= self.as_of]
                last = max(active_dates, default=None)
                if not rows:
                    no_activity.append(brief)
                if last and (as_of - date.fromisoformat(last)).days < 90:
                    active_ids.add(eid)
                if last is None or (as_of - date.fromisoformat(last)).days >= 90:
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
            completed_records = sum(row['status'] == 'completed' for row in all_history)
            return {'as_of_date': self.as_of, 'employee_count': len(all_employees),
                    'active_employees': len(active_ids),
                    'active_employees_pct': round(100 * len(active_ids) / max(1, len(all_employees)), 1),
                    'average_progress': round(sum(progress_values) / max(1, len(progress_values)), 1),
                    'completion_rate_pct': round(100 * completed_records / len(all_history), 1) if all_history else 0,
                    'activity_records': len(all_history), 'completed_records': completed_records,
                    'employees_without_recommendation': len(no_steps),
                    'employees_without_activity': no_activity,
                    'employee_rows': employee_rows,
                    'skill_gaps': [{'skill_id': sid, 'name': names[sid], 'employees': n} for sid, n in sorted(gaps.items(), key=lambda x: (-x[1], x[0]))],
                    'no_next_step': no_steps, 'inactive_90_days': inactive, 'participation': participation}

    def import_data(self, payload):
        with self.lock:
            return self._import_data(payload)

    def _import_data(self, payload):
        """Validate and merge compatible dataset files as one SQLite transaction."""
        if not isinstance(payload, dict):
            raise Problem(422, 'Ожидается объект с файлами набора данных')

        def parse_json(value, label):
            if isinstance(value, str):
                try:
                    return json.loads(value.lstrip('\ufeff'))
                except (ValueError, UnicodeError) as exc:
                    raise Problem(422, f'Некорректный JSON: {label}') from exc
            return value

        def checked_meta(document):
            meta = document.get('meta', {})
            if not isinstance(meta, dict):
                raise Problem(422, 'meta должен быть объектом')
            return meta

        employees_data = parse_json(payload.get('employees', []), 'employees.json')
        events_data = parse_json(payload.get('events'), 'events.json')
        skills_data = parse_json(payload.get('skills'), 'skills.json')
        if isinstance(employees_data, dict):
            if checked_meta(employees_data).get('as_of_date', self.as_of) != self.as_of:
                raise Problem(422, 'Дата employees.json должна совпадать с датой среза')
            employees = employees_data.get('employees')
        else:
            employees = employees_data
        if events_data is None:
            candidate_events = self.events
            catalog_events_received = 0
        elif isinstance(events_data, dict):
            if checked_meta(events_data).get('as_of_date', self.as_of) != self.as_of:
                raise Problem(422, 'Дата events.json должна совпадать с датой среза')
            candidate_events = events_data.get('events')
            catalog_events_received = len(candidate_events) if isinstance(candidate_events, list) else 0
        else:
            candidate_events = events_data
            catalog_events_received = len(candidate_events) if isinstance(candidate_events, list) else 0
        if skills_data is None:
            candidate_skills = self.skills
            catalog_skills_received = 0
        elif isinstance(skills_data, dict):
            if checked_meta(skills_data).get('as_of_date', self.as_of) != self.as_of:
                raise Problem(422, 'Дата skills.json должна совпадать с датой среза')
            candidate_skills = skills_data
            if not isinstance(skills_data.get('skills'), list) or not isinstance(skills_data.get('role_profiles'), list):
                raise Problem(422, 'skills и role_profiles должны быть массивами')
            catalog_skills_received = len(skills_data['skills'])
        else:
            raise Problem(422, 'skills.json должен содержать объект каталога')

        raw_history = payload.get('activity_history', payload.get('history', []))
        if isinstance(raw_history, str):
            if len(raw_history.encode('utf-8')) > 3_000_000:
                raise Problem(413, 'CSV слишком большой')
            history = list(csv.DictReader(io.StringIO(raw_history.lstrip('\ufeff'))))
        else:
            history = raw_history
        if not isinstance(employees, list) or not isinstance(history, list) or not isinstance(candidate_events, list):
            raise Problem(422, 'Проверьте формат employees.json, events.json и activity_history.csv')
        if not isinstance(candidate_skills, dict):
            raise Problem(422, 'Проверьте формат skills.json')
        if not candidate_events or not candidate_skills.get('skills') or not candidate_skills.get('role_profiles'):
            raise Problem(422, 'Каталог событий и навыков не может быть пустым')
        if not (employees or history or catalog_events_received or catalog_skills_received):
            raise Problem(422, 'Передайте файлы сотрудников, каталога или истории')
        if len(employees) > 1000 or len(history) > 20000:
            raise Problem(413, 'Лимит: 1000 профилей и 20000 строк за импорт')

        with self.lock:
            current_employees = {e['employee_id']: e for e in self.employees()}
            current_history = {r['record_id']: r for r in self.history()}
            seen = set()
            try:
                for skill in candidate_skills['skills']:
                    if not isinstance(skill, dict) or any(not isinstance(skill.get(k), str) or not skill[k] for k in ('skill_id', 'name', 'type')):
                        raise ValueError('Навык должен содержать skill_id, name и type')
                for event in candidate_events:
                    if not isinstance(event, dict) or any(not isinstance(event.get(k), str) or not event[k] for k in ('event_id', 'title', 'format')):
                        raise ValueError('Событие должно содержать event_id, title и format')
                    if not isinstance(event.get('mandatory'), bool):
                        raise ValueError('mandatory должен быть boolean')
                    for key in ('target_roles', 'target_grades', 'upcoming_sessions', 'develops_skills'):
                        if not isinstance(event.get(key), list):
                            raise ValueError(key + ' должен быть массивом')
                    if not isinstance(event.get('prerequisites'), dict):
                        raise ValueError('prerequisites должен быть объектом')
                    for session in event['upcoming_sessions']:
                        date.fromisoformat(session)
                    duration = event.get('duration_hours')
                    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 <= duration < 100000:
                        raise ValueError('Некорректная duration_hours')
                known_profiles = {(p['role'], p['grade']) for p in candidate_skills['role_profiles']}
                known_skills = {s['skill_id'] for s in candidate_skills['skills']}
                event_ids = {e['event_id'] for e in candidate_events}
                if len(event_ids) != len(candidate_events):
                    raise ValueError('Повторный event_id в events.json')
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
                    goal = employee.get('career_goal')
                    if goal and (goal['target_role'], goal['target_grade']) not in known_profiles:
                        raise ValueError('Неизвестная карьерная цель')
                    if self.db.execute('SELECT 1 FROM completions WHERE employee_id=?', (eid,)).fetchone() and current_employees.get(eid) != employee:
                        raise ValueError('Профиль имеет сохранённый прогресс; импортируйте проверочный профиль с новым ID')
                    current_employees[eid] = employee

                # A new catalog must continue to serve already stored profiles and history.
                for employee in current_employees.values():
                    if (employee['role'], employee['grade']) not in known_profiles:
                        raise ValueError('Новый каталог не содержит роль/грейд сохранённого профиля')
                    if not set(employee['skills']).issubset(known_skills):
                        raise ValueError('Новый каталог не содержит навык сохранённого профиля')
                    goal = employee.get('career_goal')
                    if goal and (goal['target_role'], goal['target_grade']) not in known_profiles:
                        raise ValueError('Новый каталог не содержит цель сохранённого профиля')
                for row in current_history.values():
                    if row['event_id'] not in event_ids:
                        raise ValueError('Новый каталог событий не содержит событие сохранённой истории')

                seen_records = set()
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

                affected = seen | {r['employee_id'] for r in history}
                if catalog_events_received or catalog_skills_received:
                    affected = set(current_employees)
                merged_rows = list(current_history.values())
                for eid in affected:
                    recommend(current_employees[eid], candidate_events, candidate_skills, merged_rows, as_of_date=self.as_of)
                    trajectory(current_employees[eid], candidate_events, candidate_skills, merged_rows, self.as_of)
            except (ValueError, TypeError, KeyError, AttributeError, StopIteration) as exc:
                raise Problem(422, 'Импорт отклонён: ' + str(exc)) from exc

            with self.db:
                for employee in employees:
                    self.db.execute('INSERT INTO employees VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (employee['employee_id'], json.dumps(employee)))
                for row in history:
                    self.db.execute('INSERT OR IGNORE INTO history VALUES (?,?,?)', (row['record_id'], row['employee_id'], json.dumps(row)))
                if catalog_events_received:
                    self.db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data', ('events', json.dumps(candidate_events)))
                if catalog_skills_received:
                    self.db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data', ('skills', json.dumps(candidate_skills)))
            self.events, self.skills = candidate_events, candidate_skills
            return {'employees_received': len(employees), 'history_received': len(history),
                    'events_received': catalog_events_received, 'skills_received': catalog_skills_received,
                    'affected_employees': sorted(affected)}

    def close(self):
        self.db.close()
