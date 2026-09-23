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
  $('#login-form').classList.remove('hidden'); $('#register-form').classList.add('hidden');
  $('#register-form').reset(); setRegistrationRole();
  ['#skills-list','#history-list','#recommendation-list','#hr-content','#employee-picker'].forEach(s => $(s).replaceChildren());
  $('#password').value = ''; $('#import-form').reset(); $('#import-status').textContent = ''; $('#llm-status').textContent = '';
}
function setAuthMode(registering) {
  $('#login-form').classList.toggle('hidden', registering);
  $('#register-form').classList.toggle('hidden', !registering);
  $('#register-form').reset(); setRegistrationRole();
  $('#login-error').textContent = ''; $('#register-error').textContent = '';
}
function setRegistrationRole() {
  const isHR = $('#register-role').value === 'hr';
  $('#register-employee-invite-field').classList.toggle('hidden', isHR);
  $('#register-invite-field').classList.toggle('hidden', !isHR);
  $('#register-form [name="employee_invite"]').required = !isHR;
  $('#register-form [name="invite_code"]').required = isHR;
  if (isHR) $('#register-form [name="employee_invite"]').value = '';
  else $('#register-form [name="invite_code"]').value = '';
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
  const metrics = [
    ['♙', 'mint', data.employee_count, 'Сотрудников'],
    ['↗', 'lavender', `${data.active_employees} · ${data.active_employees_pct}%`, 'Активны за 90 дней'],
    ['◎', 'peach', `${data.average_progress}%`, 'Средний прогресс'],
    ['✓', 'yellow', `${data.completion_rate_pct}%`, 'Завершение активностей'],
    ['✦', 'yellow', data.employees_without_recommendation, 'Без рекомендации'],
    ['·', 'peach', data.employees_without_activity.length, 'Без активности'],
    ['◷', 'lavender', data.inactive_90_days.length, 'Без завершений ≥90 дней'],
    ['◇', 'mint', data.skill_gaps.length, 'Навыков с разрывами']
  ].map(([icon, color, value, label]) => `<article class="panel metric-card"><span class="metric-icon ${color}">${icon}</span><div class="metric-value">${value}</div><div class="metric-label">${label}</div></article>`).join('');
  const directory = data.employee_rows.map(e => `<tr><td><button class="text-button open-profile" data-id="${escapeHTML(e.employee_id)}">${escapeHTML(e.full_name)}</button><small>${escapeHTML(e.employee_id)}</small></td><td>${escapeHTML(e.role)} · ${escapeHTML(e.grade)}</td><td>${escapeHTML(e.main_skill_gap)}</td><td>${escapeHTML(e.last_active)}</td><td>${e.has_recommendation ? 'Да' : 'Нет'}</td><td>${escapeHTML(e.development_status)}</td><td>${e.progress_pct}%</td><td>${e.has_account ? 'Аккаунт есть' : `<button class="text-button create-invite" data-id="${escapeHTML(e.employee_id)}">Выдать приглашение</button><small class="invite-output" aria-live="polite"></small>`}</td></tr>`).join('');
  $('#hr-content').innerHTML = `<div class="metric-grid">${metrics}</div>
  <article class="panel hr-panel"><h3>Какие навыки чаще проседают</h3><p class="muted">Число сотрудников с положительным разрывом до целевой роли / грейда. Это не рейтинг людей.</p><div class="gap-scroll">${data.skill_gaps.map(g => `<div class="gap-row"><div class="skill-name">${escapeHTML(g.name)}</div><div class="skill-track"><i style="width:${100*g.employees/data.employee_count}%"></i></div><div class="gap-count">${g.employees}</div></div>`).join('')}</div></article>
  <article class="panel employees-panel"><h3>Сотрудники и статус развития</h3><p class="muted">Последняя активность, главный разрыв, доступность рекомендации и прогресс до карьерной цели.</p><label class="search">Поиск<input id="employee-search" type="search" placeholder="Имя, ID или навык" aria-label="Поиск сотрудников"></label><div class="table-wrap"><table id="employee-directory"><thead><tr><th>Сотрудник</th><th>Роль и грейд</th><th>Главный skill gap</th><th>Последняя активность</th><th>Есть рекомендация</th><th>Статус</th><th>Прогресс</th><th>Доступ</th></tr></thead><tbody>${directory || '<tr><td colspan="8">Нет профилей</td></tr>'}</tbody></table></div></article>
  <article class="panel employees-panel"><h3>Кому не хватает следующего шага</h3><p class="muted">Нужны дополнительные программы или обсуждение цели.</p><div class="table-wrap"><table><thead><tr><th>Сотрудник</th><th>Роль</th><th>Грейд</th></tr></thead><tbody>${employeeRows(data.no_next_step)}</tbody></table></div></article>
  <article class="panel employees-panel"><h3>С кем стоит обсудить развитие</h3><p class="muted">Нет завершённых добровольных активностей за последние 90 дней. Это повод для разговора, не оценка результативности.</p><div class="table-wrap"><table><thead><tr><th>Сотрудник</th><th>Роль</th><th>Грейд</th><th>Последнее завершение</th></tr></thead><tbody>${employeeRows(data.inactive_90_days,true)}</tbody></table></div></article>
  <article class="panel employees-panel"><h3>Участие по активностям</h3><p class="muted">За всю историю до даты среза. Доля завершений считается по записям участия, включая повторы клуба.</p><div class="table-wrap"><table><thead><tr><th>Активность</th><th>Участников</th><th>Записей</th><th>Завершено</th><th>В процессе</th><th>Неявка</th><th>Отказ</th><th>Прекращено</th><th>Просрочено</th><th>Завершение %</th></tr></thead><tbody>${data.participation.map(p => `<tr><td>${escapeHTML(p.title)}<small>${p.mandatory ? 'Обязательная · вне рекомендаций' : 'Добровольная'}</small></td><td>${p.unique_employees}</td><td>${p.total}</td><td>${p.statuses.completed}</td><td>${p.statuses.in_progress}</td><td>${p.statuses.no_show}</td><td>${p.statuses.declined}</td><td>${p.statuses.dropped}</td><td>${p.statuses.overdue}</td><td>${p.completion_pct}%</td></tr>`).join('')}</tbody></table></div></article>`;
}
$('#login-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; $('#login-error').textContent = '';
  try { const session = await api('/api/login', {method:'POST', body:JSON.stringify({username:$('#username').value.trim(),password:$('#password').value})}); $('#password').value = ''; await enter(session); }
  catch (error) { $('#login-error').textContent = error.message; } finally { button.disabled = false; }
});
$('#show-register').addEventListener('click', () => setAuthMode(true));
$('#show-login').addEventListener('click', () => setAuthMode(false));
$('#register-role').addEventListener('change', setRegistrationRole);
$('#register-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; const button = event.submitter;
  const data = new FormData(form); button.disabled = true; $('#register-error').textContent = '';
  try {
    const session = await api('/api/register', {method:'POST', body:JSON.stringify({
      username:data.get('username'), password:data.get('password'), role:data.get('role'),
      employee_invite:data.get('employee_invite'), invite_code:data.get('invite_code')
    })});
    form.reset(); setRegistrationRole(); await enter(session);
  } catch (error) { $('#register-error').textContent = error.message; }
  finally { button.disabled = false; }
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
$('#hr-content').addEventListener('click', async event => {
  const inviteButton = event.target.closest('.create-invite');
  if (inviteButton) {
    inviteButton.disabled = true;
    const output = inviteButton.parentElement.querySelector('.invite-output');
    output.textContent = 'Создаём одноразовый код…';
    try {
      const invite = await api(`/api/employees/${encodeURIComponent(inviteButton.dataset.id)}/registration-invite`, {method:'POST', body:'{}'});
      output.textContent = `Код ${invite.token} · действует до ${invite.expires_at}. Передайте его только этому сотруднику.`;
      inviteButton.textContent = 'Создать новый код';
    } catch (error) { output.textContent = error.message; }
    finally { inviteButton.disabled = false; }
    return;
  }
  const profileButton = event.target.closest('.open-profile');
  if (profileButton) { state.employeeId = profileButton.dataset.id; $('#employee-picker').value = state.employeeId; showView('employee'); }
});
$('#hr-content').addEventListener('input', event => {
  if (event.target.id !== 'employee-search') return;
  const term = event.target.value.trim().toLocaleLowerCase();
  document.querySelectorAll('#employee-directory tbody tr').forEach(row => {
    row.hidden = Boolean(term) && !row.textContent.toLocaleLowerCase().includes(term);
  });
});
$('#import-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; $('#import-status').textContent = 'Проверяем файлы…';
  try {
    const inputs = [
      ['employees', 'employees-file', 'employees-text', true],
      ['events', 'events-file', 'events-text', true],
      ['skills', 'skills-file', 'skills-text', true],
      ['activity_history', 'history-file', 'history-text', false]
    ];
    const payload = {};
    let totalBytes = 0;
    for (const [key, fileId, textId, isJson] of inputs) {
      const file = $(`#${fileId}`).files[0], text = $(`#${textId}`).value.trim();
      if (file && text) throw new Error(`Для ${key} используйте файл или текст, но не оба сразу.`);
      totalBytes += file?.size || new TextEncoder().encode(text).length;
      if (file) {
        const content = await file.text();
        payload[key] = isJson ? JSON.parse(content.replace(/^\uFEFF/,'')) : content;
      } else if (text) payload[key] = isJson ? JSON.parse(text) : text;
    }
    if (!Object.keys(payload).length) throw new Error('Выберите файл или вставьте содержимое хотя бы одного набора данных.');
    if (totalBytes > 3000000) throw new Error('Суммарный размер файлов должен быть до 3 MB.');
    const fullDataset = ['employees','events','skills','activity_history'].every(key => key in payload);
    const result = await api(fullDataset ? '/api/datasets/upload' : '/api/import', {method:'POST',body:JSON.stringify(payload)});
    $('#import-status').textContent = `Импорт завершён: профилей ${result.employees_received}, строк истории ${result.history_received}, событий ${result.events_received}, навыков ${result.skills_received}. Можно выбрать сотрудника во вкладке «Мой рост».`;
    await loadEmployees(result.affected_employees[0] || state.employeeId); await loadHR();
  } catch(error) { $('#import-status').textContent = error.message; } finally { button.disabled = false; }
});
$('#llm-button').addEventListener('click', async () => {
  const eid = state.employeeId, profile = state.profile; $('#llm-button').disabled = true; $('#llm-status').textContent = 'Запрашиваем локальную модель…';
  try { const result = await api(`/api/employees/${encodeURIComponent(eid)}/explanations`,{method:'POST',body:'{}'}); if (state.employeeId === eid && state.profile === profile && state.user) { renderRecommendations(result.recommendations); $('#llm-status').textContent = result.message; } }
  catch(error) { $('#llm-status').textContent = error.message; } finally { $('#llm-button').disabled = false; }
});
setRegistrationRole();
api('/api/me').then(enter).catch(error => { if (state.user) showError(error.message); });
