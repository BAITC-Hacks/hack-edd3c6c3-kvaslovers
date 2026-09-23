import copy
import csv
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from Backend.main import AppServer
from Backend.store import Store, Problem
from ai.local_llm import explain_selected


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / 'state.db')
        cls.server = AppServer(('127.0.0.1', 0), cls.store)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.credentials = json.loads((Path(cls.temp.name)/'credentials.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.store.close(); cls.temp.cleanup()

    def request(self, method, path, payload=None, auth=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        request_headers = {'Content-Type': 'application/json'}
        if auth:
            request_headers.update({'Cookie': auth[0], 'X-CSRF-Token': auth[1]})
        request_headers.update(headers or {})
        connection.request(method, path, json.dumps(payload).encode() if payload is not None else None, request_headers)
        response = connection.getresponse()
        body = response.read()
        result = json.loads(body) if response.getheader('Content-Type','').startswith('application/json') else body
        status, cookie = response.status, response.getheader('Set-Cookie')
        connection.close()
        return status, result, cookie

    def login(self, username):
        status, data, cookie = self.request('POST', '/api/login', {'username': username, 'password': self.credentials[username]})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        return cookie.split(';')[0], data['csrf']

    def fresh_employee(self, suffix):
        employee = copy.deepcopy(self.store.employee('E0004'))
        employee['employee_id'] = 'JURY_' + suffix
        return employee

    def test_access_control_and_static_allowlist(self):
        self.assertEqual(self.request('GET','/api/employees/E0002')[0],401)
        self.assertEqual(self.request('GET','/api/registration/profiles')[0],401)
        employee = self.login('E0002')
        self.assertEqual(self.request('GET','/api/registration/profiles',auth=employee)[0],404)
        self.assertEqual(self.request('GET','/api/employees/E0002',auth=employee)[0],200)
        for path in ['/api/employees/E0004','/employees/E0004/recommendations','/api/hr/summary']:
            self.assertEqual(self.request('GET',path,auth=employee)[0],403)
        self.assertEqual(self.request('POST','/api/import',{},employee)[0],403)
        self.assertEqual(self.request('POST','/api/datasets/upload',{},employee)[0],403)
        self.assertEqual(self.request('POST','/api/employees/E0004/complete',{'event_id':'EV_026'},employee)[0],403)
        status, listed, _ = self.request('GET','/api/employees',auth=employee)
        self.assertEqual([e['employee_id'] for e in listed],['E0002'])
        for path in ['/ai/data/employees.json','/.local/credentials.json','/../.git/config','/ai/data/activity_history.csv']:
            self.assertNotEqual(self.request('GET',path,auth=employee)[0],200)
        hr = self.login('hr')
        self.assertEqual(self.request('GET','/api/employees/UNKNOWN',auth=hr)[0],404)
        self.assertEqual(self.request('GET','/',auth=employee)[0],200)

    def test_login_logout_csrf_and_origin(self):
        self.assertEqual(self.request('POST','/api/login',{'username':'hr','password':'wrong'})[0],401)
        employee = self.login('E0002')
        self.assertEqual(self.request('POST','/api/logout',{},(employee[0], 'wrong'))[0],403)
        self.assertEqual(self.request('POST','/api/logout',{},employee,{'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.request('POST','/api/logout',{},employee)[0],200)
        self.assertEqual(self.request('GET','/api/me',auth=employee)[0],401)
        hr = self.login('hr')
        with self.store.lock:
            self.server.sessions[hr[0].split('=',1)[1]]['expires'] = 0
        self.assertEqual(self.request('GET','/api/me',auth=hr)[0],401)

    def test_completion_http_changes_progress_persists_and_is_idempotent(self):
        hr = self.login('hr')
        employee = self.fresh_employee('COMPLETE')
        self.assertEqual(self.request('POST','/api/import',{'employees':[employee]},hr)[0],200)
        path = '/api/employees/' + employee['employee_id']
        before = self.request('GET',path,auth=hr)[1]
        event_id = before['recommendations'][0]['event_id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.request('POST',path+'/complete',{'event_id':event_id},hr), range(2)))
        self.assertTrue(all(r[0] == 200 for r in results))
        self.assertEqual(sum(not r[1]['already_completed'] for r in results),1)
        after = self.request('GET',path,auth=hr)[1]
        self.assertEqual(after['employee']['last_review_date'], before['employee']['last_review_date'])
        self.assertEqual(after['employee']['skill_snapshot_date'], self.store.as_of)
        self.assertGreater(after['trajectory']['progress_pct'],before['trajectory']['progress_pct'])
        self.assertNotIn(event_id,[r['event_id'] for r in after['recommendations']])
        self.assertEqual(sum(r['event_id']==event_id and r['status']=='completed' for r in after['history']),1)
        reloaded = Store(self.store.path)
        self.assertEqual(reloaded.profile(employee['employee_id']),after)
        reloaded.close()
        self.assertEqual(self.request('POST',path+'/complete',{'event_id':'EV_001'},hr)[0],409)
        self.assertEqual(self.request('POST',path+'/complete',{'event_id':'MISSING'},hr)[0],404)

    def test_repeatable_club_not_reoffered_same_day(self):
        employee = copy.deepcopy(self.store.employee('E0151')); employee['employee_id']='JURY_CLUB'
        employee['skills'] = {sid:0 for sid in employee['skills']}
        self.store.import_data({'employees':[employee]})
        # Use the original employee whose only candidate is the club.
        actual = self.store.profile('E0151')
        self.assertEqual([r['event_id'] for r in actual['recommendations']],['EV_036'])
        result = self.store.complete('E0151','EV_036')
        self.assertNotIn('EV_036',[r['event_id'] for r in result['profile']['recommendations']])
        self.assertTrue(self.store.complete('E0151','EV_036')['already_completed'])

    def test_import_jury_json_csv_atomic_validation_and_deduplication(self):
        hr = self.login('hr')
        employee = self.fresh_employee('IMPORT')
        row = {'record_id':'JURY_RECORD_1','employee_id':employee['employee_id'],'event_id':'EV_036',
               'date':'2026-09-01','due_date':'','status':'no_show','completion_pct':'0','score':'','feedback_rating':'','assigned_by':'self'}
        stream=io.StringIO(); writer=csv.DictWriter(stream,fieldnames=list(row));writer.writeheader();writer.writerow(row)
        payload={'employees':{'meta':{'as_of_date':'2026-10-01'},'employees':[employee]},'history':stream.getvalue()}
        self.assertEqual(self.request('POST','/api/import',payload,hr)[0],200)
        self.assertEqual(self.request('POST','/api/import',payload,hr)[0],200)
        self.assertEqual(len(self.store.history(employee['employee_id'])),1)
        self.assertTrue(self.store.recommendations(employee['employee_id']))
        bad_employee = self.fresh_employee('ROLLBACK'); bad_row={**row,'record_id':'BAD_RECORD','event_id':'NOT_FOUND'}
        status=self.request('POST','/api/import',{'employees':[bad_employee],'history':[bad_row]},hr)[0]
        self.assertEqual(status,422)
        with self.assertRaises(Problem): self.store.employee(bad_employee['employee_id'])
        bad_employee['skills']['SK_PYTHON']=float('nan')
        self.assertEqual(self.request('POST','/api/import',{'employees':[bad_employee]},hr)[0],422)
        self.assertEqual(self.request('POST','/api/import',{'employees':[employee,employee]},hr)[0],422)
        self.assertEqual(self.request('POST','/api/import',{'history':[row,{**row,'record_id':'JURY_RECORD_2','status':'skipped'}]},hr)[0],422)
        self.assertEqual(self.request('POST','/api/import',{'history':[{**row,'status':'completed','completion_pct':'100'}]},hr)[0],422)

    def test_empty_recommendations_and_hr_aggregates(self):
        hr=self.login('hr'); employee=self.fresh_employee('SATISFIED')
        employee['skills']={s['skill_id']:5 for s in self.store.skills['skills']}
        self.store.import_data({'employees':[employee]})
        self.assertEqual(self.request('GET',f'/employees/{employee["employee_id"]}/recommendations',auth=hr)[:2],(200,[]))
        status, summary, _=self.request('GET','/api/hr/summary',auth=hr)
        self.assertEqual(status,200)
        self.assertIn(employee['employee_id'],[e['employee_id'] for e in summary['no_next_step']])
        self.assertEqual(summary['employee_count'],len(self.store.employees()))
        self.assertEqual(sum(p['total'] for p in summary['participation']),len(self.store.history()))
        for activity in summary['participation']:
            self.assertEqual(sum(activity['statuses'].values()),activity['total'])
        independent={}
        from ai.profile import trajectory
        for employee in self.store.employees():
            for skill in trajectory(employee,self.store.events,self.store.skills,self.store.history(employee['employee_id']),self.store.as_of)['skills']:
                if skill['gap']>0: independent[skill['skill_id']]=independent.get(skill['skill_id'],0)+1
        self.assertEqual(independent,{g['skill_id']:g['employees'] for g in summary['skill_gaps']})

    def test_profile_skill_projection_matches_engine_factors(self):
        for employee in self.store.employees():
            profile=self.store.profile(employee['employee_id'])
            levels={s['skill_id']:s['level'] for s in profile['trajectory']['skills']}
            for rec in profile['recommendations']:
                for effect in rec['factors']['skills']:
                    self.assertEqual(levels[effect['skill_id']],effect['current_level'])

    def test_http_latency_budget(self):
        hr=self.login('hr')
        timings=[]
        for path in ['/api/employees/E0002','/api/hr/summary','/employees/E0004/recommendations']:
            start=time.perf_counter();status,_,_=self.request('GET',path,auth=hr)
            elapsed=time.perf_counter()-start;timings.append((path,round(elapsed,4)))
            self.assertEqual(status,200);self.assertLess(elapsed,2)
        print('HTTP latency:',timings)

    def test_llm_disabled_success_invalid_and_timeout(self):
        recommendations=self.store.recommendations('E0004')
        with patch.dict('os.environ',{},clear=True):
            self.assertEqual(explain_selected(recommendations)['mode'],'deterministic')
        original=copy.deepcopy(recommendations)
        def successful(model, context, output):
            self.assertNotIn('employee_id',json.dumps(context))
            output.put({item['event_id']:'Объяснение на основании переданных фактов.' for item in context})
        with patch.dict('os.environ',{'CAREER_QUEST_OLLAMA_MODEL':'test-local'}):
            with patch('ai.local_llm._request',successful):
                result=explain_selected(recommendations)
                self.assertEqual(result['mode'],'local_llm')
                self.assertEqual(result['recommendations'][0]['score'],recommendations[0]['score'])
            with patch('ai.local_llm._request',lambda m,c,o:o.put({'wrong':'bad'})):
                self.assertEqual(explain_selected(recommendations)['mode'],'deterministic')
            def slow(m,c,o): time.sleep(.08);o.put({})
            with patch('ai.local_llm._request',slow):
                start=time.perf_counter();self.assertEqual(explain_selected(recommendations,timeout=.01)['mode'],'deterministic')
                self.assertLess(time.perf_counter()-start,.06)
                time.sleep(.09)
        self.assertEqual(original,recommendations)

    def test_local_llm_http_protocol(self):
        import queue
        from ai.local_llm import _request
        from unittest.mock import MagicMock
        output=queue.Queue()
        connection=MagicMock()
        connection.getresponse.return_value.status=200
        connection.getresponse.return_value.read.return_value=json.dumps({'response':json.dumps({'EV_005':'Объяснение'})}).encode()
        with patch('ai.local_llm.http.client.HTTPConnection',return_value=connection) as factory:
            _request('local-test-model',[{'event_id':'EV_005'}],output)
            factory.assert_called_once_with('127.0.0.1',11434,timeout=6)
            method,path,body,headers=connection.request.call_args.args
            self.assertEqual((method,path),('POST','/api/generate'))
            self.assertFalse(json.loads(body)['stream'])
            self.assertEqual(json.loads(body)['format'],'json')
            self.assertEqual(json.loads(body)['model'],'local-test-model')
            self.assertEqual(output.get_nowait(),{'EV_005':'Объяснение'})
            connection.close.assert_called_once()

    def test_three_jury_fixtures_import_without_engine_changes(self):
        from Backend.store import ROOT
        payload={'employees':json.loads((ROOT/'docs/jury-sample/employees.json').read_text()),
                 'history':(ROOT/'docs/jury-sample/activity_history.csv').read_text()}
        self.store.import_data(payload)
        expected={'JURY_A':['EV_006','EV_005','EV_007'],
                  'JURY_B':['EV_026','EV_021','EV_027'], 'JURY_C':[]}
        for eid, ids in expected.items():
            self.assertEqual([r['event_id'] for r in self.store.recommendations(eid)],ids)

    def test_employee_history_route_and_enriched_directory(self):
        hr = self.login('hr')
        status, history, _ = self.request('GET', '/api/employees/E0002/history', auth=hr)
        self.assertEqual(status, 200)
        self.assertEqual(history, self.store.history_payload('E0002'))
        status, rows, _ = self.request('GET', '/api/employees', auth=hr)
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), len(self.store.employees()))
        required = {'has_recommendation', 'last_activity', 'main_skill_gap',
                    'development_status', 'progress_pct'}
        self.assertTrue(required.issubset(rows[0]))
        employee = self.login('E0002')
        status, own_rows, _ = self.request('GET', '/api/employees', auth=employee)
        self.assertEqual(status, 200)
        self.assertEqual([row['employee_id'] for row in own_rows], ['E0002'])
        self.assertEqual(self.request('GET', '/api/employees/E0004/history', auth=employee)[0], 403)

    def test_registration_requires_personal_hr_invite_and_cannot_claim_profile(self):
        linked = {row[0] for row in self.store.db.execute(
            'SELECT employee_id FROM users WHERE employee_id IS NOT NULL'
        )}
        employee_id = next(employee['employee_id'] for employee in self.store.employees()
                           if employee['employee_id'] not in linked)
        # Knowing or guessing a real, unclaimed employee_id is not enough to take it.
        status, _, _ = self.request('POST', '/api/register', {
            'username': 'signup_attacker', 'password': 'test-password-123',
            'role': 'employee', 'employee_id': employee_id,
        })
        self.assertEqual(status, 403)
        self.assertIsNone(self.store.db.execute(
            'SELECT 1 FROM users WHERE username=?', ('signup_attacker',)
        ).fetchone())
        employee = self.login('E0002')
        self.assertEqual(self.request('POST', f'/api/employees/E0002/registration-invite', {}, employee)[0], 403)
        hr = self.login('hr')
        status, invite, _ = self.request(
            'POST', f'/api/employees/{employee_id}/registration-invite', {}, hr,
        )
        self.assertEqual(status, 201)
        self.assertEqual(invite['employee_id'], employee_id)
        self.assertEqual(len(invite['token']), 43)
        status, session, cookie = self.request('POST', '/api/register', {
            'username': 'signup_employee', 'password': 'test-password-123',
            'role': 'employee', 'employee_invite': invite['token'],
            'employee_id': 'E0002',
        })
        self.assertEqual(status, 201)
        self.assertIn('HttpOnly', cookie)
        self.assertEqual(session['user']['employee_id'], employee_id)
        employee_auth = (cookie.split(';')[0], session['csrf'])
        self.assertEqual(self.request('GET', f'/api/employees/{employee_id}', auth=employee_auth)[0], 200)
        self.assertEqual(self.request('POST', '/api/register', {
            'username': 'second_account', 'password': 'test-password-123',
            'role': 'employee', 'employee_invite': invite['token'],
        })[0], 403)
        self.assertEqual(self.request(
            'POST', f'/api/employees/{employee_id}/registration-invite', {}, hr,
        )[0], 409)
        self.assertEqual(self.request('POST', '/api/register', {
            'username': 'signup_hr_bad', 'password': 'test-password-123',
            'role': 'hr', 'invite_code': 'wrong-code',
        })[0], 403)
        with patch.dict('os.environ', {'CAREER_QUEST_HR_REGISTRATION_CODE': 'test-invite-code'}):
            status, hr_session, hr_cookie = self.request('POST', '/api/register', {
                'username': 'signup_hr_good', 'password': 'test-password-123',
                'role': 'hr', 'invite_code': 'test-invite-code',
            })
        self.assertEqual(status, 201)
        hr_auth = (hr_cookie.split(';')[0], hr_session['csrf'])
        self.assertEqual(self.request('GET', '/api/hr/summary', auth=hr_auth)[0], 200)

    def test_four_file_dataset_upload_is_atomic_and_repeatable(self):
        from Backend.store import ROOT
        hr = self.login('hr')
        employee_file = json.loads((ROOT / 'docs/jury-sample/employees.json').read_text(encoding='utf-8'))
        history_file = (ROOT / 'docs/jury-sample/activity_history.csv').read_text(encoding='utf-8')
        event_file = {'meta': {'as_of_date': self.store.as_of}, 'events': self.store.events}
        payload = {'employees': employee_file, 'events': event_file,
                   'skills': self.store.skills, 'activity_history': history_file}
        status, result, _ = self.request('POST', '/api/datasets/upload', payload, hr)
        self.assertEqual(status, 200)
        self.assertEqual(result['events_received'], len(self.store.events))
        self.assertEqual(result['skills_received'], len(self.store.skills['skills']))
        before = {eid: len(self.store.history(eid)) for eid in ('JURY_A', 'JURY_B', 'JURY_C')}
        self.assertEqual(self.request('POST', '/api/datasets/upload', payload, hr)[0], 200)
        self.assertEqual(before, {eid: len(self.store.history(eid)) for eid in before})
        bad_payload = {**payload, 'events': {'meta': event_file['meta'], 'events': self.store.events + [self.store.events[0]]}}
        self.assertEqual(self.request('POST', '/api/datasets/upload', bad_payload, hr)[0], 422)
        self.assertEqual(before, {eid: len(self.store.history(eid)) for eid in before})

    def test_malformed_catalog_rolls_back_and_keeps_profile_usable(self):
        hr = self.login('hr')
        before = self.store.profile('E0002')
        variants = [
            {'skills': {'skills': None, 'role_profiles': []}},
            {'employees': {'meta': None, 'employees': []}},
        ]
        bad_skills = copy.deepcopy(self.store.skills)
        del bad_skills['skills'][0]['type']
        variants.append({'skills': bad_skills})
        bad_events = copy.deepcopy(self.store.events)
        bad_events[0]['mandatory'] = 'false'
        variants.append({'events': bad_events})
        for payload in variants:
            with self.subTest(payload_type=list(payload)):
                self.assertEqual(self.request('POST', '/api/import', payload, hr)[0], 422)
                self.assertEqual(self.store.profile('E0002'), before)

    def test_expired_and_replaced_employee_invites_are_rejected(self):
        employee = self.fresh_employee('EXPIRED_INVITE')
        self.store.import_data({'employees': [employee]})
        eid = employee['employee_id']
        expired = self.store.create_registration_invite(eid, 'hr', ttl_seconds=-1)
        with self.assertRaises(Problem) as error:
            self.store.register('expired_signup', 'temporary-password', 'employee', expired['token'])
        self.assertEqual(error.exception.status, 403)
        replaced = self.store.create_registration_invite(eid, 'hr')
        current = self.store.create_registration_invite(eid, 'hr')
        with self.assertRaises(Problem):
            self.store.register('replaced_signup', 'temporary-password', 'employee', replaced['token'])
        result = self.store.register('valid_invite_signup', 'temporary-password', 'employee', current['token'])
        self.assertEqual(result['employee_id'], eid)


if __name__=='__main__': unittest.main()
