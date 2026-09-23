"""Run from repository root: python3 -m Backend.main"""

import argparse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit, unquote

from .store import ROOT, Problem, Store

LOG = logging.getLogger('career_quest')


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store):
        super().__init__(address, Handler)
        self.store = store
        self.sessions = {}
        self.attempts = {}


class Handler(BaseHTTPRequestHandler):
    server_version = 'CareerQuest'

    def log_message(self, format, *args):
        # Do not log request bodies, credentials, cookie values, or profile contents.
        LOG.info('%s %s', self.command, self.path.split('?')[0])

    def send(self, status, data, cookie=None, content_type='application/json; charset=utf-8'):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if content_type.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        if self.headers.get_content_type() != 'application/json':
            raise Problem(415, 'Требуется application/json')
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise Problem(400, 'Некорректный Content-Length')
        if size < 0 or size > 4_000_000:
            raise Problem(413, 'Размер запроса превышает 4 MB')
        try:
            payload = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError):
            raise Problem(400, 'Некорректный JSON')
        if not isinstance(payload, dict):
            raise Problem(400, 'JSON должен быть объектом')
        return payload

    def session(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            token = cookie['cq_session'].value if 'cq_session' in cookie else ''
        except Exception:
            token = ''
        with self.server.store.lock:
            session = self.server.sessions.get(token)
            if not session or session['expires'] <= time.time():
                self.server.sessions.pop(token, None)
                raise Problem(401, 'Войдите в систему')
        return token, session

    def permission(self, session, employee_id=None, hr=False):
        user = session['user']
        if hr and user['role'] != 'hr':
            raise Problem(403, 'Доступ только для HR')
        if employee_id and user['role'] != 'hr' and user['employee_id'] != employee_id:
            raise Problem(403, 'Можно просматривать только свой профиль')

    def route(self, method):
        path = unquote(urlsplit(self.path).path)
        if method == 'GET' and path in ('/', '/index.html', '/styles.css', '/app.js'):
            file = ROOT / ('index.html' if path == '/' else path[1:])
            mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            return self.send(200, file.read_bytes(), content_type=mime + '; charset=utf-8')
        if method == 'GET' and path == '/health':
            return self.send(200, {'status': 'ok'})
        if method == 'POST':
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers.get('Host', ''):
                raise Problem(403, 'Запрос с другого сайта запрещён')
        if method == 'POST' and path == '/api/login':
            payload = self.body()
            username, password = payload.get('username'), payload.get('password')
            if not isinstance(username, str) or not isinstance(password, str) or len(username) > 100 or len(password) > 200:
                raise Problem(400, 'Введите логин и пароль')
            key = self.client_address[0]
            now = time.time()
            with self.server.store.lock:
                attempts = [t for t in self.server.attempts.get(key, []) if t > now - 60]
                if len(attempts) >= 10:
                    raise Problem(429, 'Слишком много попыток. Подождите минуту.')
                self.server.attempts[key] = attempts + [now]
            user = self.server.store.authenticate(username, password)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            with self.server.store.lock:
                self.server.attempts.pop(key, None)
                self.server.sessions = {k: v for k, v in self.server.sessions.items() if v['expires'] > now}
                self.server.sessions[token] = {'user': user, 'csrf': csrf, 'expires': now + 8 * 3600}
            return self.send(200, {'user': user, 'csrf': csrf}, 'cq_session=' + token + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')
        if method == 'POST' and path == '/api/register':
            payload = self.body()
            user = self.server.store.register(
                payload.get('username'), payload.get('password'), payload.get('role'),
                payload.get('employee_invite'), payload.get('invite_code', ''),
            )
            now = time.time()
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            with self.server.store.lock:
                self.server.sessions = {k: v for k, v in self.server.sessions.items() if v['expires'] > now}
                self.server.sessions[token] = {'user': user, 'csrf': csrf, 'expires': now + 8 * 3600}
            return self.send(201, {'user': user, 'csrf': csrf}, 'cq_session=' + token + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')
        token, session = self.session()
        if method == 'POST' and not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), session['csrf']):
            raise Problem(403, 'Недействительный CSRF-токен')
        if method == 'GET' and path == '/api/me':
            return self.send(200, {'user': session['user'], 'csrf': session['csrf'], 'as_of_date': self.server.store.as_of})
        if method == 'POST' and path == '/api/logout':
            with self.server.store.lock:
                self.server.sessions.pop(token, None)
            return self.send(200, {'ok': True}, 'cq_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
        if method == 'GET' and path in ('/employees', '/api/employees'):
            employee_ids = None if session['user']['role'] == 'hr' else [session['user']['employee_id']]
            return self.send(200, self.server.store.employee_rows(employee_ids))
        if method == 'GET' and path == '/api/hr/summary':
            self.permission(session, hr=True)
            return self.send(200, self.server.store.hr_summary())
        if method == 'POST' and path in ('/api/import', '/api/datasets/upload'):
            self.permission(session, hr=True)
            payload = self.body()
            if path == '/api/datasets/upload':
                missing = {'employees', 'events', 'skills', 'activity_history'} - set(payload)
                if missing:
                    raise Problem(422, 'Для загрузки набора нужны файлы: employees.json, events.json, skills.json и activity_history.csv')
            return self.send(200, self.server.store.import_data(payload))
        match = re.fullmatch(r'/(?:api/)?employees/([^/]+)(?:/(history|recommendations|complete|explanations|registration-invite))?', path)
        if match:
            employee_id, action = match.groups()
            self.permission(session, employee_id)
            if method == 'GET' and action is None:
                return self.send(200, self.server.store.profile(employee_id))
            if method == 'GET' and action == 'recommendations':
                return self.send(200, self.server.store.recommendations(employee_id))
            if method == 'GET' and action == 'history':
                return self.send(200, self.server.store.history_payload(employee_id))
            if method == 'POST' and action == 'registration-invite':
                self.permission(session, hr=True)
                return self.send(201, self.server.store.create_registration_invite(
                    employee_id, session['user']['username']))
            if method == 'POST' and action == 'complete':
                payload = self.body()
                if not isinstance(payload.get('event_id'), str):
                    raise Problem(400, 'Укажите event_id')
                return self.send(200, self.server.store.complete(employee_id, payload['event_id']))
            if method == 'POST' and action == 'explanations':
                from ai.local_llm import explain_selected
                return self.send(200, explain_selected(self.server.store.recommendations(employee_id)))
        raise Problem(404, 'Маршрут не найден')

    def handle_request(self, method):
        started = time.perf_counter()
        try:
            self.route(method)
        except Problem as error:
            self.send(error.status, {'error': error.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            LOG.exception('Unhandled server error')
            self.send(500, {'error': 'Ошибка сервера. Изменение не выполнено.'})
        finally:
            LOG.debug('Request %.3fs', time.perf_counter() - started)

    def do_GET(self):
        self.handle_request('GET')

    def do_POST(self):
        self.handle_request('POST')


def main():
    parser = argparse.ArgumentParser(description='Career Quest: локальное приложение хакатона')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--db', type=Path, default=ROOT / '.local/career_quest.sqlite3')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    store = Store(args.db)
    server = AppServer(('127.0.0.1', args.port), store)
    print(f'Career Quest: http://127.0.0.1:{server.server_port}', flush=True)
    print(f'Логины и пароли: {args.db.parent / "credentials.json"}', flush=True)
    print('Дата учебного среза: ' + store.as_of + '. Ctrl+C — остановить.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == '__main__':
    main()
