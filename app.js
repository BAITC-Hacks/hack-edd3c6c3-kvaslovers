const DEFAULT_API_BASE = window.location.protocol === 'file:'
  ? 'http://127.0.0.1:8000/api'
  : `${window.location.origin}/api`;
const API_BASE = (window.CAREER_QUEST_API || DEFAULT_API_BASE).replace(/\/$/, '');
const DEFAULT_EMPLOYEE_ID = new URLSearchParams(window.location.search).get('employee') || 'E0002';
const state = {
  employeeId: DEFAULT_EMPLOYEE_ID,
  employees: [],
  skills: [],
  history: [],
  recommendations: [],
  selectedRecommendation: null,
  showAllSkills: false,
  showAllHistory: false,
  employeeLimit: 20,
  profileRequest: 0
};

function setLoading(active) {
  document.querySelector('#loading-overlay').classList.toggle('hidden', !active);
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  });
  const payload = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(payload?.error || `API request failed (${response.status})`);
  return payload;
}

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
}

function humanizeSkillId(id) {
  return String(id || '').replace(/^SK_/, '').toLowerCase()
    .split('_').map(word => word ? word[0].toUpperCase() + word.slice(1) : '').join(' ');
}

function toast(message) {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.classList.add('show');
  window.setTimeout(() => element.classList.remove('show'), 3200);
}

function applyProfile(payload) {
  if (!payload) return;
  const profile = payload.employee || payload;
  const firstName = (profile.full_name || profile.name || 'there').split(/\s+/)[0];
  document.querySelector('h1').firstChild.textContent = `Good morning, ${firstName} `;
  document.querySelectorAll('.user-mini strong').forEach(el => { el.textContent = profile.full_name || profile.name || 'Employee'; });
  document.querySelectorAll('.avatar').forEach(el => {
    el.textContent = (profile.full_name || profile.name || 'CQ').split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase();
  });
  document.querySelector('.user-mini small').textContent = `${profile.role || ''} · ${profile.grade || ''}`.trim();
  document.querySelector('.career-current').textContent = profile.grade || 'Current';
  const trajectory = payload.trajectory;
  if (trajectory) {
    document.querySelector('.career-next').textContent = trajectory.target_role === profile.role
      ? trajectory.target_grade : `${trajectory.target_role} · ${trajectory.target_grade}`;
    document.querySelector('#target-grade-label').textContent = `${trajectory.target_grade}`;
    const progress = Math.max(0, Math.min(100, Math.round(trajectory.completion_pct || 0)));
    document.querySelector('#trajectory-percent').textContent = `${progress}%`;
    document.querySelector('#trajectory-bar').style.width = `${progress}%`;
    document.querySelector('.line-track i').style.width = `${progress}%`;
    document.querySelector('.line-track b').style.left = `${Math.max(2, progress - 2)}%`;
    state.skills = trajectory.skills || [];
    renderSkills();
    const focus = state.skills.find(skill => skill.gap > 0);
    document.querySelector('#skill-opportunity').innerHTML = focus
      ? `<b>${escapeHTML(focus.name || humanizeSkillId(focus.skill_id))}</b> is your largest current gap (${focus.current_level}/${focus.required_level}).`
      : 'You meet the listed requirements for this career target.';
  }
  if (Array.isArray(payload.recent_activity)) {
    state.history = payload.recent_activity;
    renderHistory();
  }
}

function renderSkills() {
  const list = document.querySelector('#skills-list');
  if (!state.skills.length) {
    list.innerHTML = '<p class="empty-state">No skill requirements are available for this career target.</p>';
    return;
  }
  const visible = state.showAllSkills ? state.skills : state.skills.slice(0, 5);
  list.innerHTML = visible.map(skill => {
    const level = Number(skill.current_level || 0);
    const required = Number(skill.required_level || 0);
    const width = required > 0 ? Math.max(0, Math.min(100, level / required * 100)) : 0;
    const name = skill.name || humanizeSkillId(skill.skill_id);
    return `<div class="skill-row"><div class="skill-name">${escapeHTML(name)}<small>${skill.critical ? 'Promotion-critical' : 'Career skill'}</small></div><div class="skill-track"><i style="width:${width}%"></i></div><div class="skill-score">${level}<em>/${required}</em></div></div>`;
  }).join('');
  document.querySelector('#profile-toggle').innerHTML = state.showAllSkills
    ? 'Show priority skills <span>↑</span>' : `View full profile (${state.skills.length}) <span>→</span>`;
}

function renderHistory() {
  const list = document.querySelector('#history-list');
  const visible = state.showAllHistory ? state.history : state.history.slice(0, 3);
  list.innerHTML = visible.length ? visible.map(item =>
    `<div class="history-row"><span class="history-icon">${escapeHTML(item.icon || '↗')}</span><div class="history-info"><strong>${escapeHTML(item.title)}</strong><small>${escapeHTML(item.meta)}</small></div><span class="${item.done ? 'status-done' : 'status-progress'}">${escapeHTML(item.label || (item.done ? 'Completed' : 'Up next'))}</span></div>`
  ).join('') : '<p class="empty-state">No activity records yet. Your next step will appear here.</p>';
  const toggle = document.querySelector('#history-toggle');
  toggle.classList.toggle('hidden', state.history.length <= 3);
  toggle.innerHTML = state.showAllHistory ? 'Show recent <span>↑</span>' : 'See all <span>→</span>';
}

function chooseRecommendation(item) {
  state.selectedRecommendation = item;
  const effects = item.factors?.skills || [];
  const leadEffect = effects.find(effect => effect.effective_gain > 0) || effects[0];
  const title = item.title || item.event_title || item.event_id;
  document.querySelector('#activity-title').textContent = title;
  document.querySelector('.activity-desc').textContent = item.description || (leadEffect
    ? `${leadEffect.name}: ${leadEffect.current_level} → ${leadEffect.projected_level}; ${leadEffect.required_level} required for ${item.factors?.target_grade || 'your career target'}.`
    : 'Selected from your career goals, skill gaps, and activity history.');
  document.querySelector('.activity-tag').textContent = leadEffect
    ? `+${leadEffect.effective_gain} ${leadEffect.name}` : `Match score ${Math.round(item.score * 100)}%`;
  const duration = Number(item.duration_hours || 0);
  document.querySelector('#activity-type').innerHTML = `<span>↗</span> DEVELOPMENT · ${duration ? `${duration} HOUR${duration === 1 ? '' : 'S'}` : 'SELF-PACED'}`;
  const reasons = Array.isArray(item.reason) ? item.reason : item.reason ? [item.reason] : [];
  document.querySelector('#reason-list').innerHTML = reasons.length
    ? reasons.map(reason => `<li>${escapeHTML(reason)}</li>`).join('')
    : '<li>Selected based on your target grade, skill gaps, and activity history.</li>';
  document.querySelectorAll('.recommendation-option').forEach(button => {
    button.classList.toggle('selected', button.dataset.eventId === item.event_id);
  });
  const completeButton = document.querySelector('#complete-button');
  completeButton.disabled = false;
  completeButton.classList.remove('complete');
  completeButton.innerHTML = 'Mark as complete <span>→</span>';
}

function applyRecommendations(response) {
  const list = Array.isArray(response) ? response : response?.recommendations;
  if (!Array.isArray(list)) return;
  state.recommendations = list;
  const options = document.querySelector('#recommendation-options');
  const button = document.querySelector('#complete-button');
  if (!list.length) {
    state.selectedRecommendation = null;
    options.innerHTML = '';
    document.querySelector('#activity-title').textContent = 'No next step yet';
    document.querySelector('.activity-desc').textContent = 'There are no eligible activities for this profile right now.';
    document.querySelector('#activity-type').innerHTML = '<span>✦</span> CAREER QUEST';
    document.querySelector('#reason-list').innerHTML = '<li>You have no current skill gaps with an available activity, or the catalogue has no eligible next step.</li>';
    document.querySelector('.activity-tag').textContent = 'No activity available';
    button.disabled = true;
    return;
  }
  options.innerHTML = list.map((item, index) => {
    const effect = item.factors?.skills?.[0];
    const title = item.title || item.event_title || item.event_id;
    const skill = effect?.name || humanizeSkillId(effect?.skill_id);
    return `<button class="recommendation-option${index === 0 ? ' selected' : ''}" type="button" data-event-id="${escapeHTML(item.event_id)}"><span><strong>${escapeHTML(title)}</strong><small>${escapeHTML(skill)} · ${Math.round(item.score * 100)}% match</small></span><span class="option-arrow">→</span></button>`;
  }).join('');
  options.querySelectorAll('.recommendation-option').forEach(button => button.addEventListener('click', () => {
    const selected = list.find(item => item.event_id === button.dataset.eventId);
    if (selected) chooseRecommendation(selected);
  }));
  chooseRecommendation(list[0]);
}

function setView(view) {
  const showHR = view === 'hr';
  document.querySelector('#employee-view').classList.toggle('hidden', showHR);
  document.querySelector('#hr-view').classList.toggle('hidden', !showHR);
  document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === view));
  document.querySelector('#crumb').textContent = showHR ? 'People insights' : 'My growth';
}

function renderEmployeePicker() {
  const picker = document.querySelector('#employee-picker');
  picker.innerHTML = state.employees.map(employee =>
    `<option value="${escapeHTML(employee.employee_id)}">${escapeHTML(employee.full_name)} · ${escapeHTML(employee.role)} · ${escapeHTML(employee.grade)}</option>`
  ).join('');
  if (!state.employees.some(employee => employee.employee_id === state.employeeId)) {
    state.employeeId = state.employees[0]?.employee_id || DEFAULT_EMPLOYEE_ID;
  }
  picker.value = state.employeeId;
}

async function loadEmployee(employeeId, { updateUrl = true } = {}) {
  if (!employeeId) return;
  const request = ++state.profileRequest;
  setLoading(true);
  state.employeeId = employeeId;
  try {
    const payload = await api(`/employees/${encodeURIComponent(employeeId)}`);
    if (request !== state.profileRequest) return;
    applyProfile(payload);
    applyRecommendations(payload.recommendations || []);
    const picker = document.querySelector('#employee-picker');
    if (picker && [...picker.options].some(option => option.value === employeeId)) picker.value = employeeId;
    if (updateUrl) {
      const url = new URL(window.location.href);
      url.searchParams.set('employee', employeeId);
      window.history.replaceState({}, '', url);
    }
  } finally {
    if (request === state.profileRequest) setLoading(false);
  }
}

function renderHRRows() {
  const query = document.querySelector('#employee-search').value.toLowerCase().trim();
  const matches = state.employees.filter(employee =>
    `${employee.full_name} ${employee.role} ${employee.focus} ${employee.department}`.toLowerCase().includes(query)
  );
  const visible = matches.slice(0, state.employeeLimit);
  document.querySelector('#employee-rows').innerHTML = visible.map(employee => {
    const initials = employee.full_name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase();
    const statusClass = employee.has_recommendation ? 'status-done' : employee.status === 'On track' ? 'status-progress' : 'needs-status';
    return `<tr data-employee-id="${escapeHTML(employee.employee_id)}" tabindex="0" aria-label="Open ${escapeHTML(employee.full_name)} profile"><td><div class="employee-cell"><span class="table-avatar">${escapeHTML(initials)}</span>${escapeHTML(employee.full_name)}</div></td><td>${escapeHTML(employee.role)} · ${escapeHTML(employee.grade)}</td><td><span class="focus-pill">${escapeHTML(employee.focus)}</span></td><td>${escapeHTML(employee.last_active)}</td><td class="${statusClass}">${escapeHTML(employee.status)}</td></tr>`;
  }).join('');
  const loadMore = document.querySelector('#load-more-employees');
  loadMore.classList.toggle('hidden', visible.length >= matches.length);
  loadMore.innerHTML = `Show more employees (${matches.length - visible.length}) <span>↓</span>`;
}

async function loadHR() {
  applyHR(await api('/hr/summary'));
}

async function completeActivity() {
  const recommendation = state.selectedRecommendation;
  const button = document.querySelector('#complete-button');
  if (!recommendation) return;
  button.disabled = true;
  button.textContent = 'Updating progress…';
  try {
    const result = await api(`/employees/${encodeURIComponent(state.employeeId)}/activities/${encodeURIComponent(recommendation.event_id)}/complete`, {
      method: 'POST', body: JSON.stringify({})
    });
    applyProfile(result.profile);
    applyRecommendations(result.recommendations || []);
    toast('Progress updated. Your next step is ready.');
    loadHR().catch(error => console.warn('HR data refresh failed:', error));
  } catch (error) {
    button.disabled = false;
    button.innerHTML = 'Mark as complete <span>→</span>';
    toast(error.message || 'Could not update progress. Please try again.');
  }
}

document.querySelectorAll('.nav-item').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
document.querySelector('#complete-button').addEventListener('click', completeActivity);
document.querySelector('#employee-search').addEventListener('input', () => { state.employeeLimit = 20; renderHRRows(); });
document.querySelector('#load-more-employees').addEventListener('click', () => { state.employeeLimit += 20; renderHRRows(); });
document.querySelector('#profile-toggle').addEventListener('click', () => { state.showAllSkills = !state.showAllSkills; renderSkills(); });
document.querySelector('#history-toggle').addEventListener('click', () => { state.showAllHistory = !state.showAllHistory; renderHistory(); });
document.querySelector('#employee-picker').addEventListener('change', event => {
  loadEmployee(event.target.value).catch(error => toast(error.message));
});
document.querySelector('#employee-rows').addEventListener('click', event => {
  const row = event.target.closest('tr[data-employee-id]');
  if (!row) return;
  loadEmployee(row.dataset.employeeId).then(() => setView('employee')).catch(error => toast(error.message));
});
document.querySelector('#employee-rows').addEventListener('keydown', event => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const row = event.target.closest('tr[data-employee-id]');
  if (!row) return;
  event.preventDefault();
  loadEmployee(row.dataset.employeeId).then(() => setView('employee')).catch(error => toast(error.message));
});

async function start() {
  try {
    const summary = await api('/hr/summary');
    applyHR(summary);
    await loadEmployee(state.employeeId, { updateUrl: false });
  } catch (error) {
    toast(`Backend unavailable: ${error.message}`);
    document.querySelector('#activity-title').textContent = 'Start the Career Quest backend';
    document.querySelector('.activity-desc').textContent = 'Run “python -m Backend.main” from the project folder, then refresh this page.';
    document.querySelector('#employee-picker').innerHTML = '<option>Backend unavailable</option>';
    document.querySelector('#complete-button').disabled = true;
    console.error('Career Quest failed to load:', error);
  }
}

function applyHR(summary) {
  const metricValues = document.querySelectorAll('.metric-value');
  metricValues[0].textContent = summary.employees;
  metricValues[1].textContent = `${summary.active_employees_pct}%`;
  metricValues[2].textContent = summary.employees_without_recommendation;
  metricValues[3].innerHTML = `${summary.average_skill_level}<span class="metric-denom"> / 5</span>`;
  document.querySelector('#gap-list').innerHTML = summary.skills_with_gaps.slice(0, 5).map(skill => {
    const width = Math.max(8, Math.round(skill.employees / Math.max(1, summary.employees) * 100));
    return `<div class="gap-row"><div class="skill-name">${escapeHTML(skill.name)}</div><div class="skill-track"><i style="width:${width}%"></i></div><div class="gap-count">${skill.employees}</div></div>`;
  }).join('') || '<p class="empty-state">No skill gaps found.</p>';
  document.querySelector('#participation-list').innerHTML = summary.participation
    .slice().sort((a, b) => b.completion_pct - a.completion_pct).slice(0, 4).map(item => {
      const pct = Math.max(0, Math.min(100, item.completion_pct));
      return `<div class="participation-row"><div class="skill-name">${escapeHTML(item.title)}</div><div class="split-bar"><i style="width:${pct}%"></i></div><div class="skill-score">${pct}%</div></div>`;
    }).join('') || '<p class="empty-state">No voluntary activity records yet.</p>';
  state.employees = summary.employee_rows || state.employees;
  renderEmployeePicker();
  renderHRRows();
}

start();
