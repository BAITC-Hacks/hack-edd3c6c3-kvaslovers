const API_BASE = (window.CAREER_QUEST_API || 'http://127.0.0.1:8000/api').replace(/\/$/, '');

const translations = {
  en: { career_motion: 'YOUR CAREER, IN MOTION', next_step_close: 'Your next step is closer than you think.', clear_view: "A CLEAR VIEW OF WHAT'S NEXT", growth_snapshot: 'Your growth snapshot', view_full_profile: 'View full profile', skills_focus: 'Skills in focus', next_best_step: 'Your next best step', picked_for_you: 'Picked for where you want to go', why_now: 'WHY THIS, WHY NOW', mark_complete: 'Mark as complete', momentum: 'MOMENTUM MATTERS', recent_activity: 'Recent activity', see_all: 'See all' },
  ru: { career_motion: 'ВАША КАРЬЕРА В ДВИЖЕНИИ', next_step_close: 'Следующий карьерный шаг уже близко.', clear_view: 'ПОНЯТНЫЙ ПУТЬ ВПЕРЁД', growth_snapshot: 'Ваш прогресс', view_full_profile: 'Весь профиль', skills_focus: 'Ключевые навыки', next_best_step: 'Следующий лучший шаг', picked_for_you: 'Подобрано под вашу карьерную цель', why_now: 'ПОЧЕМУ ИМЕННО СЕЙЧАС', mark_complete: 'Отметить выполненным', momentum: 'СОХРАНЯЙТЕ ТЕМП', recent_activity: 'Недавняя активность', see_all: 'Показать всё' },
  kk: { career_motion: 'МАНСАБЫҢЫЗ ДАМУДА', next_step_close: 'Келесі мансаптық қадам жақын.', clear_view: 'АЛДАҒЫ ЖОЛ АНЫҚ', growth_snapshot: 'Сіздің прогресіңіз', view_full_profile: 'Толық профиль', skills_focus: 'Негізгі дағдылар', next_best_step: 'Келесі үздік қадам', picked_for_you: 'Мансаптық мақсатыңызға сай таңдалды', why_now: 'НЕГЕ ДӘЛ ҚАЗІР', mark_complete: 'Орындалды деп белгілеу', momentum: 'ҚАРҚЫНДЫ САҚТАҢЫЗ', recent_activity: 'Соңғы белсенділік', see_all: 'Барлығын көрсету' }
};

const demo = {
  employee: { employee_id: 'E0002', full_name: 'Arman Zhaksylykov', role: 'Backend Engineer', grade: 'Middle', tenure_months: 41, career_goal: { target_grade: 'Senior' } },
  trajectory: { target_grade: 'Senior', completion_pct: 62, skills: [
    { skill_id: 'SK_SYSTEM_DESIGN', name: 'System Design', current_level: 2, required_level: 4, gap: 2, critical: true },
    { skill_id: 'SK_API_DESIGN', name: 'API Design', current_level: 2, required_level: 4, gap: 2, critical: true },
    { skill_id: 'SK_OBSERVABILITY', name: 'Observability', current_level: 2, required_level: 3, gap: 1, critical: false }
  ] },
  recommendations: [{ event_id: 'EV_005', title: 'Designing High-Load Systems', duration_hours: 2, score: .91, factors: { target_grade: 'Senior', skills: [{ name: 'System Design', current_level: 2, projected_level: 3, required_level: 4, effective_gain: 1, critical: true }] }, reason: 'System Design is critical for the Senior grade; this activity closes one level of the current gap.' }]
};

const state = {
  online: false,
  language: localStorage.getItem('careerQuestLanguage') || 'en',
  currentEmployeeId: localStorage.getItem('careerQuestEmployee') || 'E0002',
  employees: [], recommendations: [], selectedRecommendation: 0,
  profile: null, skills: [], progress: 0, showAllSkills: false,
  history: [], showAllHistory: false, employeeLimit: 12
};

function escapeHTML(value) { return String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]); }

async function api(path, options = {}) {
  const headers = options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' };
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  let payload = null;
  try { payload = await response.json(); } catch (_) { /* Empty/non-JSON response. */ }
  if (!response.ok) throw new Error(payload?.error || `API request failed (${response.status})`);
  return payload;
}

function setLoading(active, message = 'Loading employee data…') {
  const overlay = document.querySelector('#loading-overlay');
  overlay.querySelector('span:last-child').textContent = message;
  overlay.classList.toggle('hidden', !active);
}

function setConnection(online) {
  state.online = online;
  const indicator = document.querySelector('#connection-status');
  indicator.textContent = online ? 'Live data' : 'Demo mode';
  indicator.classList.toggle('offline', !online);
}

function toast(message) {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove('show'), 3000);
}

function initials(name) { return String(name).trim().split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase(); }
function humanizeSkillId(id) { return String(id || '').replace(/^SK_/, '').toLowerCase().split('_').map(word => word ? word[0].toUpperCase() + word.slice(1) : '').join(' '); }

function translate() {
  const dictionary = translations[state.language] || translations.en;
  document.documentElement.lang = state.language;
  document.querySelectorAll('[data-i18n]').forEach(element => { if (dictionary[element.dataset.i18n]) element.textContent = dictionary[element.dataset.i18n]; });
  document.querySelector('#language-picker').value = state.language;
  if (state.profile) applyProfile(state.profile);
  renderSkills(); renderHistory(); renderRecommendation();
}

function greeting(firstName) {
  if (state.language === 'ru') return `Доброе утро, ${firstName} `;
  if (state.language === 'kk') return `Қайырлы таң, ${firstName} `;
  return `Good morning, ${firstName} `;
}

function applyProfile(payload) {
  if (!payload) return;
  state.profile = payload;
  const profile = payload.employee || payload;
  const trajectory = payload.trajectory || {};
  const name = profile.full_name || profile.name || profile.employee_id;
  const heading = document.querySelector('#employee-view h1');
  heading.firstChild.textContent = greeting(String(name).split(' ')[0]);
  document.querySelectorAll('.user-mini strong').forEach(element => { element.textContent = name; });
  document.querySelectorAll('.avatar').forEach(element => { element.textContent = initials(name); });
  document.querySelector('.user-mini small').textContent = `${profile.role || ''} · ${profile.grade || ''}`;
  document.querySelector('.career-current').textContent = profile.grade || 'Current';
  document.querySelector('.career-next').textContent = trajectory.target_grade || profile.career_goal?.target_grade || 'Next level';
  document.querySelector('.hero-label').dataset.employeeId = profile.employee_id;
  document.querySelector('#skills-subtitle').textContent = `Progress toward ${trajectory.target_grade || 'next-level'} requirements`;
  state.progress = Math.round(trajectory.completion_pct ?? 0);
  document.querySelector('#trajectory-percent').textContent = `${state.progress}%`;
  document.querySelector('#trajectory-bar').style.width = `${state.progress}%`;
  document.querySelector('.line-track i').style.width = `${state.progress}%`;
  document.querySelector('.line-track b').style.left = `${Math.max(2, state.progress - 2)}%`;
  state.skills = trajectory.skills || [];
  renderSkills();
}

function renderSkills() {
  const visible = state.showAllSkills ? state.skills : state.skills.slice(0, 4);
  const container = document.querySelector('#skills-list');
  if (!visible.length) {
    container.innerHTML = '<div class="empty-state">No skill requirements available.</div>';
    document.querySelector('#skills-note').classList.add('hidden');
    return;
  }
  document.querySelector('#skills-note').classList.remove('hidden');
  container.innerHTML = visible.map(skill => {
    const name = skill.name || humanizeSkillId(skill.skill_id);
    const current = skill.current_level ?? skill.level ?? 0;
    const required = skill.required_level ?? skill.required ?? 0;
    const percent = required ? Math.min(100, current / required * 100) : 100;
    return `<div class="skill-row"><div class="skill-name">${escapeHTML(name)}<small>${skill.critical ? 'Priority skill' : 'Career skill'}</small></div><div class="skill-track"><i style="width:${percent}%"></i></div><div class="skill-score">${current}<em>/${required}</em></div></div>`;
  }).join('');
  const priority = [...state.skills].sort((a, b) => (Number(b.critical) - Number(a.critical)) || ((b.gap || 0) - (a.gap || 0)))[0];
  document.querySelector('#skills-note span:last-child').innerHTML = `<b>${escapeHTML(priority?.name || humanizeSkillId(priority?.skill_id))}</b> is the highest-priority growth opportunity.`;
  document.querySelector('#skills-count').textContent = `${visible.length} OF ${state.skills.length}`;
  document.querySelector('#profile-toggle').firstElementChild.textContent = state.showAllSkills ? 'Show key skills' : (translations[state.language]?.view_full_profile || translations.en.view_full_profile);
}

function applyRecommendations(response) {
  const list = Array.isArray(response) ? response : response?.recommendations;
  state.recommendations = Array.isArray(list) ? list : [];
  state.selectedRecommendation = 0;
  renderRecommendationTabs(); renderRecommendation();
}

function renderRecommendationTabs() {
  const container = document.querySelector('#recommendation-tabs');
  container.innerHTML = state.recommendations.map((item, index) => `<button class="recommendation-tab ${index === state.selectedRecommendation ? 'active' : ''}" data-recommendation-index="${index}">${index + 1}<span>${Math.round((item.score || 0) * 100)}%</span></button>`).join('');
  container.classList.toggle('hidden', state.recommendations.length < 2);
}

function renderRecommendation() {
  const item = state.recommendations[state.selectedRecommendation];
  const button = document.querySelector('#complete-button');
  button.classList.remove('complete');
  if (!item) {
    document.querySelector('#activity-title').textContent = 'No next step available';
    document.querySelector('.activity-desc').textContent = 'This employee currently has no eligible activity with a positive contribution to the target role.';
    document.querySelector('#reason-list').innerHTML = '<li>Update the profile or event catalogue to generate a new recommendation.</li>';
    document.querySelector('.activity-tag').textContent = 'No eligible activity';
    document.querySelector('#activity-type').textContent = '✓ PROFILE REVIEWED';
    button.disabled = true; button.textContent = 'Nothing to complete';
    return;
  }
  const effects = item.factors?.skills || [];
  const lead = effects.find(effect => (effect.effective_gain || 0) > 0) || effects[0];
  const gain = lead?.effective_gain ?? lead?.gain ?? 0;
  document.querySelector('#activity-title').textContent = item.title || item.event_id;
  document.querySelector('.activity-desc').textContent = item.description || (lead ? `${lead.name}: ${lead.current_level} → ${lead.projected_level}, with ${lead.required_level} required for ${item.factors?.target_grade || 'the next level'}.` : 'Recommended from the employee profile, target grade, skill gaps and participation history.');
  const hours = Number(item.duration_hours || 0);
  const session = item.next_session ? ` · ${item.next_session}` : '';
  document.querySelector('#activity-type').innerHTML = `<span>↗</span> LEARNING${hours ? ` · ${hours} H` : ''}${escapeHTML(session)}`;
  document.querySelector('.activity-tag').textContent = lead ? `+${gain} ${lead.name || humanizeSkillId(lead.skill_id)}` : `Score ${Math.round((item.score || 0) * 100)}%`;
  const reasons = Array.isArray(item.reason) ? item.reason : item.reason ? [item.reason] : effects.slice(0, 3).map(effect => `${effect.name}: ${effect.current_level} → ${effect.projected_level}, ${effect.required_level} required`);
  document.querySelector('#reason-list').innerHTML = reasons.map(reason => `<li>${escapeHTML(reason)}</li>`).join('');
  button.disabled = !state.online;
  button.innerHTML = `<span>${state.online ? (translations[state.language]?.mark_complete || translations.en.mark_complete) : 'Available with backend'}</span> <span>→</span>`;
}

function loadStoredHistory(employeeId) {
  try { state.history = JSON.parse(localStorage.getItem(`careerQuestHistory:${employeeId}`) || '[]'); }
  catch (_) { state.history = []; }
}
function saveHistory() { const id = state.profile?.employee?.employee_id || state.currentEmployeeId; localStorage.setItem(`careerQuestHistory:${id}`, JSON.stringify(state.history.slice(0, 30))); }

function renderHistory() {
  const container = document.querySelector('#history-list');
  const items = state.showAllHistory ? state.history : state.history.slice(0, 3);
  document.querySelector('#history-toggle').classList.toggle('hidden', state.history.length <= 3);
  if (!items.length) {
    container.innerHTML = '<div class="empty-state"><strong>No activity completed in this browser yet.</strong><span>Completed recommendations will appear here immediately.</span></div>';
    return;
  }
  container.innerHTML = items.map(item => `<div class="history-row"><span class="history-icon">✓</span><div class="history-info"><strong>${escapeHTML(item.title)}</strong><small>${escapeHTML(item.meta)}</small></div><span class="status-done">Completed</span></div>`).join('');
  document.querySelector('#history-toggle').firstElementChild.textContent = state.showAllHistory ? 'Show recent' : (translations[state.language]?.see_all || translations.en.see_all);
}

async function loadEmployee(employeeId, { announce = false } = {}) {
  state.currentEmployeeId = employeeId;
  localStorage.setItem('careerQuestEmployee', employeeId);
  document.querySelector('#employee-picker').value = employeeId;
  loadStoredHistory(employeeId); renderHistory(); setLoading(true);
  try {
    const payload = await api(`/employees/${encodeURIComponent(employeeId)}`);
    setConnection(true); applyProfile(payload); applyRecommendations(payload.recommendations || []);
    if (announce) toast(`Opened ${payload.employee?.full_name || employeeId}`);
  } catch (error) {
    setConnection(false);
    if (!state.profile) { applyProfile(demo); applyRecommendations(demo.recommendations); }
    toast(`Could not load employee: ${error.message}`);
  } finally { setLoading(false); }
}

function populateEmployeePicker() {
  const picker = document.querySelector('#employee-picker');
  picker.innerHTML = state.employees.map(person => `<option value="${escapeHTML(person.employee_id)}">${escapeHTML(person.full_name || person.employee_id)} · ${escapeHTML(person.role || '')}</option>`).join('');
  if (state.employees.some(person => person.employee_id === state.currentEmployeeId)) picker.value = state.currentEmployeeId;
}

async function loadEmployees() {
  try {
    const listing = await api('/employees');
    state.employees = listing?.employees || [];
    populateEmployeePicker(); renderEmployees(); return true;
  } catch (error) {
    state.employees = [demo.employee];
    populateEmployeePicker(); renderEmployees(); return false;
  }
}

function renderEmployees() {
  const query = document.querySelector('#employee-search').value.trim().toLowerCase();
  const matches = state.employees.filter(person => `${person.employee_id} ${person.full_name} ${person.role} ${person.department} ${person.grade}`.toLowerCase().includes(query));
  const visible = matches.slice(0, state.employeeLimit);
  document.querySelector('#employee-rows').innerHTML = visible.map(person => {
    const name = person.full_name || person.employee_id;
    const goal = person.career_goal?.target_grade ? `${person.career_goal.target_role || person.role} · ${person.career_goal.target_grade}` : `${person.role} · next grade`;
    return `<tr data-employee-id="${escapeHTML(person.employee_id)}"><td><div class="employee-cell"><span class="table-avatar">${initials(name)}</span>${escapeHTML(name)}</div></td><td>${escapeHTML(person.role)}</td><td><span class="focus-pill">${escapeHTML(goal)}</span></td><td>${Number(person.tenure_months || 0)} months</td><td><button class="row-action" data-open-employee="${escapeHTML(person.employee_id)}">Open profile →</button></td></tr>`;
  }).join('') || '<tr><td colspan="5"><div class="empty-state">No employees match this search.</div></td></tr>';
  const loadMore = document.querySelector('#load-more-employees');
  loadMore.classList.toggle('hidden', visible.length >= matches.length);
  loadMore.textContent = `Show more employees (${matches.length - visible.length}) ↓`;
}

async function loadHR() {
  try {
    const summary = await api('/hr/summary');
    const total = summary.employees || state.employees.length;
    const without = summary.employees_without_recommendation || 0;
    const gaps = (summary.skills_with_gaps || []).reduce((sum, skill) => sum + Number(skill.employees || 0), 0);
    const departments = new Set(state.employees.map(person => person.department).filter(Boolean)).size;
    document.querySelector('#metric-employees').textContent = total;
    document.querySelector('#metric-with-step').textContent = total - without;
    document.querySelector('#metric-without-step').textContent = without;
    document.querySelector('#metric-gaps').textContent = gaps;
    document.querySelector('#metric-departments').textContent = `${departments} departments represented`;
    const largest = Math.max(1, ...(summary.skills_with_gaps || []).map(skill => skill.employees));
    document.querySelector('#gap-list').innerHTML = (summary.skills_with_gaps || []).slice(0, 5).map(skill => `<div class="gap-row"><div class="skill-name">${escapeHTML(skill.name)}</div><div class="skill-track"><i style="width:${Math.round(skill.employees / largest * 100)}%"></i></div><div class="gap-count">${skill.employees}</div></div>`).join('');
    const participation = [...(summary.participation || [])].sort((a, b) => b.participants - a.participants).slice(0, 5);
    document.querySelector('#participation-list').innerHTML = participation.map(item => {
      const pct = Math.min(100, Math.round(Number(item.completed_records || 0) / Math.max(1, Number(item.participants || 0)) * 100));
      return `<div class="participation-row"><div class="skill-name">${escapeHTML(item.title)}</div><div class="split-bar"><i style="width:${pct}%"></i></div><div class="skill-score">${pct}%</div></div>`;
    }).join('');
  } catch (error) {
    document.querySelector('#metric-employees').textContent = state.employees.length || '—';
    console.warn('HR summary unavailable:', error);
  }
}

async function completeActivity() {
  const item = state.recommendations[state.selectedRecommendation];
  if (!item || !state.online) return;
  const button = document.querySelector('#complete-button');
  button.disabled = true; button.textContent = 'Updating progress…';
  try {
    const result = await api(`/employees/${encodeURIComponent(state.currentEmployeeId)}/activities/${encodeURIComponent(item.event_id)}/complete`, { method: 'POST', body: '{}' });
    state.history.unshift({ title: item.title || item.event_id, meta: `Completed · ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}` });
    saveHistory(); renderHistory(); applyProfile(result.profile); applyRecommendations(result.recommendations || []);
    await loadHR();
    toast('Activity completed — progress and recommendations updated.');
  } catch (error) {
    renderRecommendation(); toast(`Could not complete activity: ${error.message}`);
  }
}

function showView(name) {
  const showHR = name === 'hr';
  document.querySelector('#employee-view').classList.toggle('hidden', showHR);
  document.querySelector('#hr-view').classList.toggle('hidden', !showHR);
  document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === name));
  document.querySelector('#crumb').textContent = showHR ? 'People insights' : 'My growth';
}

async function initialize() {
  document.querySelector('#language-picker').value = state.language;
  setLoading(true, 'Connecting to Career Quest…');
  const employeeAPI = await loadEmployees();
  const validId = state.employees.some(person => person.employee_id === state.currentEmployeeId) ? state.currentEmployeeId : (state.employees[0]?.employee_id || 'E0002');
  await loadEmployee(validId);
  if (!employeeAPI) setConnection(false);
  await loadHR(); translate(); setLoading(false);
}

document.querySelectorAll('.nav-item').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
document.querySelector('#employee-picker').addEventListener('change', event => loadEmployee(event.target.value));
document.querySelector('#language-picker').addEventListener('change', event => { state.language = event.target.value; localStorage.setItem('careerQuestLanguage', state.language); translate(); });
document.querySelector('#refresh-button').addEventListener('click', async () => { await Promise.all([loadEmployee(state.currentEmployeeId), loadHR()]); toast('Data refreshed.'); });
document.querySelector('#complete-button').addEventListener('click', completeActivity);
document.querySelector('#recommendation-tabs').addEventListener('click', event => { const button = event.target.closest('[data-recommendation-index]'); if (!button) return; state.selectedRecommendation = Number(button.dataset.recommendationIndex); renderRecommendationTabs(); renderRecommendation(); });
document.querySelector('#profile-toggle').addEventListener('click', () => { state.showAllSkills = !state.showAllSkills; renderSkills(); });
document.querySelector('#history-toggle').addEventListener('click', () => { state.showAllHistory = !state.showAllHistory; renderHistory(); });
document.querySelector('#employee-search').addEventListener('input', () => { state.employeeLimit = 12; renderEmployees(); });
document.querySelector('#load-more-employees').addEventListener('click', () => { state.employeeLimit += 20; renderEmployees(); });
document.querySelector('#employee-rows').addEventListener('click', async event => { const button = event.target.closest('[data-open-employee]'); if (!button) return; await loadEmployee(button.dataset.openEmployee, { announce: true }); showView('employee'); window.scrollTo({ top: 0, behavior: 'smooth' }); });

renderHistory();
initialize();
