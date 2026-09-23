'use strict';
const $ = selector => document.querySelector(selector);
const state = {user: null, csrf: null, employeeId: null, profile: null, request: 0, view: 'employee'};
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const statusNames = {completed:'Завершено', in_progress:'В процессе', dropped:'Прекращено', no_show:'Неявка', declined:'Отказ', overdue:'Просрочено'};
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.remove('show'), 6000); }
function showError(message) { $('#global-error').textContent = message; $('#global-error').classList.toggle('hidden', !message); }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type':'application/json', 'X-CSRF-Token':state.csrf || '', ...options.headers}});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') resetSession();
    throw new Error(data.error || `Ошибка запроса ${response.status}`);
  }
  return data;
}
function resetSession() {
  state.user = null; state.csrf = null; state.profile = null; state.employeeId = null; state.request++;
  $('#workspace').classList.add('hidden'); $('#login-view').classList.remove('hidden');
  ['#skills-list','#history-list','#recommendation-list','#hr-content','#employee-picker'].forEach(s => $(s).replaceChildren());
  $('#password').value = ''; $('#import-form').reset(); $('#import-status').textContent = ''; $('#llm-status').textContent = '';
}
async function loadEmployees(preferred) {
  const employees = await api('/api/employees');
  $('#employee-picker').innerHTML = employees.map(e => `<option value="${escapeHTML(e.employee_id)}">${escapeHTML(e.employee_id)} · ${escapeHTML(e.full_name)} · ${escapeHTML(e.role)}</option>`).join('');
  state.employeeId = employees.some(e => e.employee_id === preferred) ? preferred : employees[0]?.employee_id;
  $('#employee-picker').value = state.employeeId || '';
}
async function enter(session) {
  state.user = session.user; state.csrf = session.csrf;
  const isHR = state.user.role === 'hr';
  $('#hr-nav').classList.toggle('hidden', !isHR); $('#employee-picker-label').classList.toggle('hidden', !isHR);
  $('#session-name').textContent = state.user.username; $('#session-role').textContent = isHR ? 'HR · обзор команды' : 'Личный профиль';
  $('#login-view').classList.add('hidden'); $('#workspace').classList.remove('hidden'); showError('');
  await loadEmployees(state.user.employee_id || 'E0002');
  await showView('employee');
}
async function showView(view) {
  if (view === 'hr' && state.user?.role !== 'hr') return;
  state.view = view;
  $('#employee-view').classList.toggle('hidden', view !== 'employee'); $('#hr-view').classList.toggle('hidden', view !== 'hr');
  document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  $('#crumb').textContent = view === 'hr' ? 'Обзор команды' : 'Мой рост'; showError('');
  try { if (view === 'hr') await loadHR(); else await loadProfile(); } catch (error) { showError(error.message); }
}
async function loadProfile() {
  const request = ++state.request, employeeId = state.employeeId;
  $('#profile-content').classList.add('hidden'); $('#employee-name').textContent = 'Загружаем профиль…';
  const profile = await api(`/api/employees/${encodeURIComponent(employeeId)}`);
  if (request !== state.request || !state.user) return;
  renderProfile(profile); $('#profile-content').classList.remove('hidden');
}
function renderProfile(profile) {
  state.profile = profile;
  const {employee, trajectory, history, as_of_date} = profile;
  $('#employee-name').textContent = employee.full_name;
  $('#employee-role').textContent = `${employee.role} · ${employee.grade} · ${employee.department}`;
  $('#snapshot-date').textContent = `Срез ${as_of_date}`; $('#simulation-date').textContent = as_of_date;
  $('#current-grade').textContent = employee.grade; $('#target-grade').textContent = trajectory.target_grade;
  $('#target-role').textContent = `Цель: ${trajectory.target_role}`;
  $('#trajectory-percent').textContent = `${trajectory.progress_pct}%`; $('#trajectory-bar').style.width = `${trajectory.progress_pct}%`;
  $('#progress-formula').textContent = trajectory.formula;
  $('#skill-caption').textContent = `Критических разрывов: ${trajectory.critical_gaps}. Показаны все навыки профиля и цели.`;
  $('#skills-list').innerHTML = trajectory.skills.map(s => `<div class="skill-row"><div class="skill-name">${escapeHTML(s.name)}<small>${s.critical ? 'Критический для цели' : s.required ? 'Нужен для цели' : 'Вне требований цели'}</small></div><div class="skill-track"><i style="width:${Math.min(100, s.level / (s.required || 5) * 100)}%"></i></div><div class="skill-score">${s.level}<em>/${s.required || '—'}</em></div></div>`).join('');
  renderRecommendations(profile.recommendations);
  $('#llm-status').textContent = ''; $('#llm-button').disabled = false; $('#llm-button').classList.toggle('hidden', !profile.recommendations.length);
  $('#history-count').textContent = `${history.length} записей`;
  $('#history-list').innerHTML = history.length ? history.map(r => `<div class="history-row"><span class="history-icon">${r.status === 'completed' ? '✓' : '·'}</span><div class="history-info"><strong>${escapeHTML(r.title)}</strong><small>${escapeHTML(r.date)} · ${escapeHTML(r.event_id)} · ${escapeHTML(r.completion_pct)}%</small></div><span class="${r.status === 'completed' ? 'status-done' : 'status-progress'}">${escapeHTML(statusNames[r.status] || r.status)}</span></div>`).join('') : '<p class="empty-state">Истории участия пока нет.</p>';
}
function renderRecommendations(items) {
  $('#recommendation-list').innerHTML = items.length ? items.map((r,i) => `<article class="panel next-panel"><div class="panel-head"><div><h3>Следующий шаг ${i+1}</h3><p>Оценка ${r.score.toFixed(4)} · ${escapeHTML(r.event_id)}</p></div><span class="sparkle">✦</span></div><div class="activity-type">${r.duration_hours} ЧАСОВ · ${r.next_session ? escapeHTML(r.next_session) : 'В СВОЁМ ТЕМПЕ'}</div><h4>${escapeHTML(r.title)}</h4><div class="reason-box"><div class="reason-title">ПОЧЕМУ ЭТА АКТИВНОСТЬ</div><p>${escapeHTML(r.reason)}</p></div><div class="effect-list">${r.factors.skills.map(s => `<div><span>${escapeHTML(s.name)}</span><strong>${s.current_level} → ${s.projected_level}<small> / требуется ${s.required_level}</small></strong></div>`).join('')}</div><details><summary>Как рассчитана оценка</summary><p>Критичность: ${r.factors.contributions.grade_importance.toFixed(4)} · Разрыв: ${r.factors.contributions.skill_gap.toFixed(4)} · Эффект: ${r.factors.contributions.event_impact.toFixed(4)} · История: ${r.factors.contributions.history_fit.toFixed(4)}.</p><p>Баллы помогают сравнить активности и не являются вероятностью повышения.</p></details>${r.llm_reason ? `<div class="llm-note"><strong>Дополнительное объяснение LLM</strong><p>${escapeHTML(r.llm_reason)}</p><small>Сверяйте с рассчитанными фактами выше.</small></div>` : ''}<p class="muted">Учебная симуляция: выполнение сохраняется на дату среза ${escapeHTML(state.profile.as_of_date)}.</p><div class="activity-footer"><span class="activity-tag">Выбор за вами</span><button class="primary-button complete-button" data-event="${escapeHTML(r.event_id)}">Отметить выполненной →</button></div></article>`).join('') : '<article class="panel empty-state"><h3>Подходящих шагов сейчас нет</h3><p>В доступном каталоге нет новых активностей, которые закрывают ваши разрывы. Возможно, требования уже выполнены или нужны другие программы. Обсудите следующий шаг с HR.</p></article>';
}
async function loadHR() {
  $('#hr-content').innerHTML = '<p class="empty-state">Собираем обзор команды…</p>';
  const data = await api('/api/hr/summary');
  if (!state.user || state.view !== 'hr') return;
  $('#snapshot-date').textContent = `Срез ${data.as_of_date}`;
  const employeeRows = (rows, inactive=false) => rows.length ? rows.map(e => `<tr><td><button class="text-button open-profile" data-id="${escapeHTML(e.employee_id)}">${escapeHTML(e.full_name)}</button><small>${escapeHTML(e.employee_id)}</small></td><td>${escapeHTML(e.role)}</td><td>${escapeHTML(e.grade)}</td>${inactive ? `<td>${escapeHTML(e.last_completed || 'Нет завершений')}</td>` : ''}</tr>`).join('') : `<tr><td colspan="4">Нет сотрудников в этой группе</td></tr>`;
  $('#hr-content').innerHTML = `<div class="metric-grid"><article class="panel metric-card"><span class="metric-icon mint">♙</span><div class="metric-value">${data.employee_count}</div><div class="metric-label">Сотрудников</div></article><article class="panel metric-card"><span class="metric-icon peach">◎</span><div class="metric-value">${data.no_next_step.length}</div><div class="metric-label">Без рекомендованного шага</div></article><article class="panel metric-card"><span class="metric-icon lavender">↗</span><div class="metric-value">${data.inactive_90_days.length}</div><div class="metric-label">Без добровольных завершений ≥90 дней</div></article><article class="panel metric-card"><span class="metric-icon yellow">✦</span><div class="metric-value">${data.skill_gaps.length}</div><div class="metric-label">Навыков с разрывами</div></article></div>
  <article class="panel hr-panel"><h3>Какие навыки чаще проседают</h3><p class="muted">Число сотрудников с положительным разрывом до целевой роли / грейда. Это не рейтинг людей.</p><div class="gap-scroll">${data.skill_gaps.map(g => `<div class="gap-row"><div class="skill-name">${escapeHTML(g.name)}</div><div class="skill-track"><i style="width:${100*g.employees/data.employee_count}%"></i></div><div class="gap-count">${g.employees}</div></div>`).join('')}</div></article>
  <article class="panel employees-panel"><h3>Кому не хватает следующего шага</h3><p class="muted">Нужны дополнительные программы или обсуждение цели.</p><div class="table-wrap"><table><thead><tr><th>Сотрудник</th><th>Роль</th><th>Грейд</th></tr></thead><tbody>${employeeRows(data.no_next_step)}</tbody></table></div></article>
  <article class="panel employees-panel"><h3>С кем стоит обсудить развитие</h3><p class="muted">Нет завершённых добровольных активностей за последние 90 дней. Это повод для разговора, не оценка результативности.</p><div class="table-wrap"><table><thead><tr><th>Сотрудник</th><th>Роль</th><th>Грейд</th><th>Последнее завершение</th></tr></thead><tbody>${employeeRows(data.inactive_90_days,true)}</tbody></table></div></article>
  <article class="panel employees-panel"><h3>Участие по активностям</h3><p class="muted">За всю историю до даты среза. Доля завершений считается по записям участия, включая повторы клуба.</p><div class="table-wrap"><table><thead><tr><th>Активность</th><th>Участников</th><th>Записей</th><th>Завершено</th><th>В процессе</th><th>Неявка</th><th>Отказ</th><th>Прекращено</th><th>Просрочено</th><th>Завершение %</th></tr></thead><tbody>${data.participation.map(p => `<tr><td>${escapeHTML(p.title)}<small>${p.mandatory ? 'Обязательная · вне рекомендаций' : 'Добровольная'}</small></td><td>${p.unique_employees}</td><td>${p.total}</td><td>${p.statuses.completed}</td><td>${p.statuses.in_progress}</td><td>${p.statuses.no_show}</td><td>${p.statuses.declined}</td><td>${p.statuses.dropped}</td><td>${p.statuses.overdue}</td><td>${p.completion_pct}%</td></tr>`).join('')}</tbody></table></div></article>`;
}
$('#login-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; $('#login-error').textContent = '';
  try { const session = await api('/api/login', {method:'POST', body:JSON.stringify({username:$('#username').value.trim(),password:$('#password').value})}); $('#password').value = ''; await enter(session); }
  catch (error) { $('#login-error').textContent = error.message; } finally { button.disabled = false; }
});
async function logout() { try { await api('/api/logout',{method:'POST',body:'{}'}); resetSession(); } catch(error) { showError(error.message); } }
$('#logout').addEventListener('click', logout); $('#mobile-logout').addEventListener('click', logout);
document.querySelectorAll('.nav-item').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
$('#employee-picker').addEventListener('change', async event => { state.employeeId = event.target.value; showError(''); try { await loadProfile(); } catch(error) { showError(error.message); } });
$('#recommendation-list').addEventListener('click', async event => {
  const button = event.target.closest('.complete-button'); if (!button) return;
  const eid = state.employeeId;
  document.querySelectorAll('.complete-button').forEach(b => b.disabled = true); button.textContent = 'Сохраняем…'; showError('');
  try { const result = await api(`/api/employees/${encodeURIComponent(eid)}/complete`, {method:'POST',body:JSON.stringify({event_id:button.dataset.event})}); if (state.employeeId === eid && state.user) renderProfile(result.profile); toast(result.already_completed ? 'Выполнение уже сохранено' : `Прогресс: ${result.progress_before}% → ${result.progress_after}%. Навыки обновлены.`); }
  catch(error) { showError(error.message); if (state.profile) renderRecommendations(state.profile.recommendations); }
});
$('#refresh-hr').addEventListener('click', () => loadHR().catch(e => showError(e.message)));
$('#hr-content').addEventListener('click', event => { const button = event.target.closest('.open-profile'); if(button) { state.employeeId = button.dataset.id; $('#employee-picker').value = state.employeeId; showView('employee'); } });
$('#import-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; $('#import-status').textContent = 'Проверяем файлы…';
  try {
    const employeeFile = $('#employees-file').files[0], historyFile = $('#history-file').files[0];
    const employeeText = $('#employees-text').value.trim(), historyText = $('#history-text').value.trim();
    if (!employeeFile && !historyFile && !employeeText && !historyText) throw new Error('Выберите файлы или вставьте JSON профилей и/или CSV истории.');
    if ((employeeFile && employeeText) || (historyFile && historyText)) throw new Error('Для каждого типа данных используйте файл или текст, а не оба сразу.');
    if ((employeeFile?.size || 0) + (historyFile?.size || 0) > 3000000) throw new Error('Суммарный размер файлов должен быть до 3 MB.');
    const payload = {};
    if (employeeFile) payload.employees = JSON.parse((await employeeFile.text()).replace(/^\uFEFF/,''));
    if (historyFile) payload.history = await historyFile.text();
    if (employeeText) payload.employees = JSON.parse(employeeText);
    if (historyText) payload.history = historyText;
    const result = await api('/api/import', {method:'POST',body:JSON.stringify(payload)});
    $('#import-status').textContent = `Импорт завершён: профилей ${result.employees_received}, строк истории ${result.history_received}. Можно выбрать сотрудника во вкладке «Мой рост».`;
    await loadEmployees(result.affected_employees[0] || state.employeeId); await loadHR();
  } catch(error) { $('#import-status').textContent = error.message; } finally { button.disabled = false; }
});
$('#llm-button').addEventListener('click', async () => {
  const eid = state.employeeId, profile = state.profile; $('#llm-button').disabled = true; $('#llm-status').textContent = 'Запрашиваем локальную модель…';
  try { const result = await api(`/api/employees/${encodeURIComponent(eid)}/explanations`,{method:'POST',body:'{}'}); if (state.employeeId === eid && state.profile === profile && state.user) { renderRecommendations(result.recommendations); $('#llm-status').textContent = result.message; } }
  catch(error) { $('#llm-status').textContent = error.message; } finally { $('#llm-button').disabled = false; }
});
api('/api/me').then(enter).catch(error => { if (state.user) showError(error.message); });
