// Demo data keeps the experience usable before the backend is available.
// Set window.CAREER_QUEST_API (for example, "http://localhost:8000") to enable live requests.
const API_BASE = window.CAREER_QUEST_API || '';
const EMPLOYEE_ID = 'E0002'; // Present in the AI branch's sample dataset.
const state = {
  progress: 72,
  completed: false,
  skills: [
    { name: 'System Design', level: 2, required: 4 },
    { name: 'Python', level: 3, required: 4 },
    { name: 'Public Speaking', level: 2, required: 3 },
    { name: 'Mentoring', level: 3, required: 4 }
  ],
  history: [
    { title: 'Code Quality Foundations', meta: 'Completed · Sep 12, 2026', icon: '✓', done: true },
    { title: 'Peer Mentoring Session', meta: 'Completed · Aug 28, 2026', icon: '↗', done: true },
    { title: 'System Design Workshop', meta: 'Recommended · 45 min', icon: '✦', done: false }
  ],
  employees: [
    { initials: 'JM', name: 'Jordan Miller', role: 'Product Designer', focus: 'Stakeholder Communication', active: '32 days ago' },
    { initials: 'SN', name: 'Samira Noor', role: 'Data Analyst', focus: 'Data Storytelling', active: '45 days ago' },
    { initials: 'DL', name: 'Daniel Lee', role: 'Software Engineer', focus: 'System Design', active: '38 days ago' },
    { initials: 'AR', name: 'Amina Rakhim', role: 'Business Analyst', focus: 'Leadership', active: '51 days ago' }
  ]
};

const recommendation = {
  event_id: 'EV_012', title: 'System Design Workshop',
  description: 'Build confidence designing resilient, scalable systems with real-world architecture challenges.',
  skill: 'System Design', gain: 1,
  reason: ['System Design is 2/4 for your Senior requirements', 'This workshop directly develops that skill (+1 level)', 'You have not completed this activity yet']
};

async function api(path, options) {
  if (!API_BASE) return null;
  const response = await fetch(`${API_BASE}${path}`, { headers: { 'Content-Type': 'application/json' }, ...options });
  if (!response.ok) throw new Error(`API request failed (${response.status})`);
  return response.status === 204 ? null : response.json();
}
function humanizeSkillId(id) {
  return id.replace(/^SK_/, '').toLowerCase().split('_').map(word => word[0].toUpperCase() + word.slice(1)).join(' ');
}
function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}
function applyProfile(profile) {
  if (!profile) return;
  const name = profile.full_name || profile.name;
  if (name) {
    document.querySelector('h1').firstChild.textContent = `Good morning, ${name.split(' ')[0]} `;
    document.querySelectorAll('.user-mini strong').forEach(el => el.textContent = name);
    document.querySelectorAll('.avatar').forEach(el => el.textContent = name.split(' ').map(part => part[0]).join('').slice(0, 2).toUpperCase());
  }
  if (profile.role) {
    document.querySelector('.user-mini small').textContent = `${profile.role} · ${profile.grade || ''}`.trim();
    document.querySelector('.career-current').textContent = profile.grade || 'Current';
    document.querySelector('.career-next').textContent = profile.career_goal?.target_grade || 'Next level';
  }
  if (profile.employee_id) document.querySelector('.hero-label').dataset.employeeId = profile.employee_id;
}
function applyRecommendations(response) {
  const list = Array.isArray(response) ? response : response?.recommendations;
  if (!Array.isArray(list)) return;
  const item = list[0];
  if (!item) {
    document.querySelector('#activity-title').textContent = 'No next step yet';
    document.querySelector('.activity-desc').textContent = 'There are no eligible activities for this profile right now.';
    document.querySelector('#reason-list').innerHTML = '<li>Check back after your profile or available activities are updated.</li>';
    document.querySelector('#complete-button').disabled = true;
    return;
  }
  Object.assign(recommendation, item);
  recommendation.title = item.title || item.event_title || item.event_id;
  const effects = item.factors?.skills || [];
  const leadEffect = effects.find(effect => effect.effective_gain > 0) || effects[0];
  recommendation.skill = leadEffect?.name || (leadEffect?.skill_id ? humanizeSkillId(leadEffect.skill_id) : 'Skill');
  recommendation.gain = leadEffect?.effective_gain ?? leadEffect?.gain ?? 1;
  document.querySelector('#activity-title').textContent = recommendation.title;
  document.querySelector('.activity-desc').textContent = item.description || (leadEffect ? `${leadEffect.name}: ${leadEffect.current_level} → ${leadEffect.projected_level}, with ${leadEffect.required_level} required for ${item.factors?.target_grade || 'your next level'}.` : 'Recommended based on your career goals, skill gaps, and activity history.');
  document.querySelector('.activity-tag').textContent = leadEffect ? `+${recommendation.gain} ${recommendation.skill}` : 'Recommended for you';
  const reasons = Array.isArray(item.reason) ? item.reason : item.reason ? [item.reason] : effects.slice(0, 3).map(effect => `${effect.name}: ${effect.current_level} → ${effect.projected_level}, ${effect.required_level} required`);
  document.querySelector('#reason-list').innerHTML = reasons.map(reason => `<li>${escapeHTML(reason)}</li>`).join('');
  if (effects.length) {
    state.skills = effects.map(effect => ({
      name: effect.name || humanizeSkillId(effect.skill_id),
      level: effect.current_level ?? 0,
      required: effect.required_level ?? effect.current_level ?? 0
    }));
    renderSkills();
  }
}
async function loadLiveProfile() {
  if (!API_BASE) return;
  try {
    const [profile, results] = await Promise.all([
      api(`/employees/${EMPLOYEE_ID}`),
      api('/recommend', { method: 'POST', body: JSON.stringify({ employee_id: EMPLOYEE_ID }) })
    ]);
    applyProfile(profile);
    applyRecommendations(results);
  } catch (error) {
    toast('Backend unavailable — showing demo data.');
    console.warn('Career Quest API is not available:', error);
  }
}
function renderSkills() {
  document.querySelector('#skills-list').innerHTML = state.skills.map(skill => `<div class="skill-row"><div class="skill-name">${skill.name}<small>${skill.name === 'System Design' ? 'Priority skill' : 'Career skill'}</small></div><div class="skill-track"><i style="width:${Math.min(100, skill.level / skill.required * 100)}%"></i></div><div class="skill-score">${skill.level}<em>/${skill.required}</em></div></div>`).join('');
}
function renderReasons() { document.querySelector('#reason-list').innerHTML = recommendation.reason.map(item => `<li>${item}</li>`).join(''); }
function renderHistory() { document.querySelector('#history-list').innerHTML = state.history.map(item => `<div class="history-row"><span class="history-icon">${item.icon}</span><div class="history-info"><strong>${item.title}</strong><small>${item.meta}</small></div><span class="${item.done ? 'status-done' : 'status-progress'}">${item.done ? 'Completed' : 'Up next'}</span></div>`).join(''); }
function renderHR() {
  const gaps = [{ name: 'System Design', count: 38, width: 87 }, { name: 'Public Speaking', count: 31, width: 71 }, { name: 'Leadership', count: 27, width: 62 }, { name: 'Data Storytelling', count: 19, width: 44 }, { name: 'Mentoring', count: 14, width: 32 }];
  document.querySelector('#gap-list').innerHTML = gaps.map(g => `<div class="gap-row"><div class="skill-name">${g.name}</div><div class="skill-track"><i style="width:${g.width}%"></i></div><div class="gap-count">${g.count}</div></div>`).join('');
  const participation = [{ name: 'Designing for Scale', pct: 72 }, { name: 'Peer Mentoring', pct: 48 }, { name: 'Public Speaking Lab', pct: 36 }, { name: 'Data Storytelling', pct: 64 }];
  document.querySelector('#participation-list').innerHTML = participation.map(p => `<div class="participation-row"><div class="skill-name">${p.name}</div><div class="split-bar"><i style="width:${p.pct}%"></i></div><div class="skill-score">${p.pct}%</div></div>`).join('');
  renderEmployees();
}
function renderEmployees() {
  const query = document.querySelector('#employee-search').value.toLowerCase();
  document.querySelector('#employee-rows').innerHTML = state.employees.filter(e => `${e.name} ${e.role} ${e.focus}`.toLowerCase().includes(query)).map(e => `<tr><td><div class="employee-cell"><span class="table-avatar">${e.initials}</span>${e.name}</div></td><td>${e.role}</td><td><span class="focus-pill">${e.focus}</span></td><td>${e.active}</td><td class="needs-status">Needs a nudge</td></tr>`).join('');
}
function toast(message) { const element = document.querySelector('#toast'); element.textContent = message; element.classList.add('show'); setTimeout(() => element.classList.remove('show'), 2800); }
async function completeActivity() {
  const button = document.querySelector('#complete-button');
  if (state.completed) return;
  button.disabled = true; button.textContent = 'Updating progress…';
  try {
    // Expected integration endpoint; replace when the backend contract is finalized.
    await api(`/employees/${EMPLOYEE_ID}/complete/${recommendation.event_id}`, { method: 'POST' });
    state.completed = true;
    const target = state.skills.find(s => s.name === recommendation.skill);
    if (target) target.level = Math.min(target.required, target.level + recommendation.gain);
    state.progress = Math.min(100, state.progress + 7);
    document.querySelector('#trajectory-percent').textContent = `${state.progress}%`;
    document.querySelector('#trajectory-bar').style.width = `${state.progress}%`;
    state.history = state.history.map(row => row.title === recommendation.title ? { ...row, meta: 'Completed · Just now', icon: '✓', done: true } : row);
    renderSkills(); renderHistory();
    button.textContent = 'Completed ✓'; button.classList.add('complete');
    toast('Nice work — your growth snapshot is updated.');
  } catch (error) {
    button.disabled = false; button.textContent = 'Mark as complete →';
    toast('Could not update progress. Please try again.');
  }
}
document.querySelectorAll('.nav-item').forEach(button => button.addEventListener('click', () => {
  const showHR = button.dataset.view === 'hr';
  document.querySelector('#employee-view').classList.toggle('hidden', showHR);
  document.querySelector('#hr-view').classList.toggle('hidden', !showHR);
  document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item === button));
  document.querySelector('#crumb').textContent = showHR ? 'People insights' : 'My growth';
}));
document.querySelector('#complete-button').addEventListener('click', completeActivity);
document.querySelector('#employee-search').addEventListener('input', renderEmployees);
document.querySelector('#activity-title').textContent = recommendation.title;
document.querySelector('.activity-desc').textContent = recommendation.description;
renderSkills(); renderReasons(); renderHistory(); renderHR();
loadLiveProfile();

