"""Shared skill projection and career trajectory for API and recommendation engine."""

GRADES = ("Junior", "Middle", "Senior", "Lead")


def effective_skills(employee, events, history, as_of):
    levels = dict(employee['skills'])
    by_event = {event['event_id']: event for event in events}
    for row in sorted(history, key=lambda r: (r['date'], r['record_id'])):
        if (row['employee_id'] == employee['employee_id'] and row['status'] == 'completed'
                and employee.get('skill_snapshot_date', employee['last_review_date']) < row['date'] <= as_of):
            for effect in by_event[row['event_id']]['develops_skills']:
                sid = effect['skill_id']
                current = levels.get(sid, 0)
                levels[sid] = current + max(0, min(effect['gain'], effect['max_level'] - current))
    return levels


def target_profile(employee, skills):
    goal = employee.get('career_goal')
    role = goal['target_role'] if goal else employee['role']
    grade = goal['target_grade'] if goal else GRADES[min(GRADES.index(employee['grade']) + 1, 3)]
    return next(p for p in skills['role_profiles'] if p['role'] == role and p['grade'] == grade)


def trajectory(employee, events, skills, history, as_of):
    levels = effective_skills(employee, events, history, as_of)
    target = target_profile(employee, skills)
    names = {s['skill_id']: s for s in skills['skills']}
    rows = []
    for sid in sorted(set(levels) | set(target['required_skills'])):
        required = target['required_skills'].get(sid, 0)
        current = levels.get(sid, 0)
        rows.append({'skill_id': sid, 'name': names[sid]['name'], 'type': names[sid]['type'],
                     'level': current, 'required': required, 'gap': max(0, required - current),
                     'critical': sid in target['critical_skills']})
    rows.sort(key=lambda r: (not r['critical'], -r['gap'], r['name']))
    denominator = sum(target['required_skills'].values())
    achieved = sum(min(levels.get(sid, 0), need) for sid, need in target['required_skills'].items())
    return {'target_role': target['role'], 'target_grade': target['grade'], 'skills': rows,
            'progress_pct': round(100 * achieved / denominator, 1) if denominator else 100,
            'critical_gaps': sum(r['critical'] and r['gap'] > 0 for r in rows),
            'formula': 'Сумма min(текущий, требуемый) / сумма требований × 100. Это покрытие требований, не вероятность повышения.'}
