/* Hader — pages & navigation. */
'use strict';

const STATUS = {
  present: ['حاضر', 'Present', 'ok'], late: ['متأخر', 'Late', 'warn'], early: ['خروج مبكر', 'Early leave', 'warn'],
  late_early: ['متأخر + مبكر', 'Late & early', 'warn'], absent: ['غائب', 'Absent', 'bad'], partial_absent: ['غياب جزئي', 'Partial absence', 'bad'], leave: ['إجازة', 'Leave', 'purple'],
  holiday: ['عطلة رسمية', 'Holiday', 'info'], off: ['راحة', 'Day off', ''], incomplete: ['بصمة ناقصة', 'Missed punch', 'warn'],
  unscheduled: ['بدون جدول', 'Unscheduled', ''], pending: ['لم يحضر بعد', 'Not yet', ''],
};
const statusBadge = (s) => s ? `<span class="badge ${STATUS[s]?.[2] || ''}">${esc(T(...(STATUS[s] || [s, s])))}</span>` : '';
const APPROVAL = { approved: ['معتمد', 'Approved', 'ok'], pending: ['بانتظار الموافقة', 'Pending', 'warn'], rejected: ['مرفوض', 'Rejected', 'bad'] };
const approvalBadge = (s) => `<span class="badge ${APPROVAL[s]?.[2] || ''}">${esc(T(...(APPROVAL[s] || [s, s])))}</span>`;
const STATES = () => [[0, T('دخول', 'Check-In')], [1, T('خروج', 'Check-Out')], [2, T('خروج استراحة', 'Break-Out')], [3, T('عودة من استراحة', 'Break-In')], [4, T('دخول إضافي', 'OT-In')], [5, T('خروج إضافي', 'OT-Out')]];
const stateName = (v) => (STATES().find(s => s[0] === +v) || [v, v === 255 ? '-' : v])[1];
const VERIFY = { password: ['كلمة مرور', 'Password'], fingerprint: ['بصمة إصبع', 'Fingerprint'], card: ['بطاقة', 'Card'], face: ['وجه', 'Face'], palm: ['كف', 'Palm'], finger_vein: ['وريد', 'Finger vein'], other: ['أخرى', 'Other'] };
const verifyName = (v) => T(...(VERIFY[v] || [v.replace(/_/g, ' + '), v.replace(/_/g, ' + ')]));
const WEEKDAYS = () => App.lang === 'ar' ? ['الإثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد'] : ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const BIO_NAMES = { 1: ['بصمة إصبع', 'Fingerprint'], 2: ['وجه (IR)', 'Face (IR)'], 7: ['وريد الإصبع', 'Finger vein'], 8: ['كف اليد', 'Palm'], 6: ['راحة اليد', 'Palmprint'], 9: ['وجه مرئي', 'Visible face'] };
// a record's name in the page language (English name when one is stored)
const nm = (r) => (App.lang === 'en' && r.name_en) ? r.name_en : (r.name ?? '');
const NAME_EN = { key: 'name_en', label: 'Name (English)', placeholder: 'e.g. Head Office' };
const opts = (list, key = 'id', label = 'name') => (list || []).map(x => ({ value: x[key], label: x[label] }));

// ============================================================== navigation
function modules() {
  return [
    { key: 'dashboard', label: T('الرئيسية', 'Dashboard'), icon: 'dashboard', items: [
      { r: 'dashboard', label: T('لوحة التحكم', 'Dashboard'), icon: 'dashboard', page: pageDashboard },
      { r: 'monitor', label: T('المراقبة الحية', 'Real-time monitor'), icon: 'monitor', perm: 'attendance.view', page: pageMonitor },
    ] },
    { key: 'personnel', label: T('الملف الشخصي', 'Personnel'), icon: 'people', items: [
      { r: 'personnel/employees', label: T('الموظفون', 'Employees'), icon: 'people', perm: 'personnel.view', page: pageEmployees },
      { r: 'personnel/departments', label: T('الأقسام', 'Departments'), icon: 'building', perm: 'personnel.view', page: pageDepartments },
      { r: 'personnel/positions', label: T('المسميات الوظيفية', 'Positions'), icon: 'badge', perm: 'personnel.view', page: pagePositions },
      { r: 'personnel/areas', label: T('المناطق', 'Areas'), icon: 'map', perm: 'personnel.view', page: pageAreas },
      { r: 'personnel/resigned', label: T('المستقيلون', 'Resigned'), icon: 'exit', perm: 'personnel.view', page: pageResigned },
      { r: 'personnel/portal', label: T('بوابة الموظف', 'Employee portal'), icon: 'device', perm: 'personnel.view', page: pagePortal },
    ] },
    { key: 'device', label: T('الأجهزة', 'Device'), icon: 'device', items: [
      { r: 'device/terminals', label: T('الجهاز', 'Device'), icon: 'device', perm: 'device.view', page: pageDevices },
      { r: 'device/transactions', label: T('سجل الحركات', 'Transactions'), icon: 'list', perm: 'attendance.view', page: pageTransactions },
      { r: 'device/commands', label: T('أوامر الأجهزة', 'Device commands'), icon: 'terminal', perm: 'device.view', page: pageCommands },
      { r: 'device/traffic', label: T('مراقبة الاتصال', 'Communication'), icon: 'sync', perm: 'device.view', page: pageTraffic },
      { r: 'device/intake', label: T('دفعات الاستقبال والمرفوضات', 'Intake and rejected records'), icon: 'list', perm: 'device.view', page: pageIntake },
      { r: 'device/oplogs', label: T('سجل عمليات الجهاز', 'Operation log'), icon: 'list', perm: 'device.view', page: pageOplogs },
      { r: 'device/errors', label: T('سجل الأخطاء', 'Error log'), icon: 'list', perm: 'device.view', page: pageErrors },
    ] },
    { key: 'attendance', label: T('الحضور والإنصراف', 'Attendance'), icon: 'clock', items: [
      { r: 'att/timetables', label: T('أوقات الدوام', 'Timetables'), icon: 'clock', perm: 'attendance.view', page: pageTimetables },
      { r: 'att/shifts', label: T('الورديات', 'Shifts'), icon: 'cal', perm: 'attendance.view', page: pageShifts },
      { r: 'att/schedules', label: T('جدولة الموظفين', 'Employee schedule'), icon: 'people', perm: 'attendance.view', page: pageSchedules },
      { r: 'att/dept-schedules', label: T('جدولة الأقسام', 'Department schedule'), icon: 'building', perm: 'attendance.view', page: pageDeptSchedules },
      { r: 'att/temp', label: T('الجدول المؤقت', 'Temporary schedule'), icon: 'cal', perm: 'attendance.view', page: pageTemp },
      { r: 'att/holidays', label: T('العطل الرسمية', 'Holidays'), icon: 'cal', perm: 'attendance.view', page: pageHolidays },
      { r: 'att/leave-types', label: T('أنواع الإجازات', 'Leave types'), icon: 'leave', perm: 'attendance.view', page: pageLeaveTypes },
      { r: 'att/leaves', label: T('الإجازات', 'Leave'), icon: 'leave', perm: 'attendance.view', page: pageLeaves },
      { r: 'att/manual', label: T('البصمات اليدوية', 'Manual punch'), icon: 'hand', perm: 'attendance.view', page: pageManual },
      { r: 'att/overtime', label: T('العمل الإضافي', 'Overtime'), icon: 'clock', perm: 'attendance.view', page: pageOvertime },
      { r: 'att/calc', label: T('نتائج الحضور', 'Attendance results'), icon: 'check', perm: 'attendance.view', page: pageCalc },
      { r: 'att/calendar', label: T('تقويم الموظف', 'Employee calendar'), icon: 'cal', perm: 'attendance.view', page: pageCalendar },
      { r: 'att/rules', label: T('قواعد الحضور', 'Attendance rules'), icon: 'system', perm: 'system.admin', page: pageRules },
      { r: 'att/policies', label: T('القواعد المؤرخة', 'Dated policies'), icon: 'cal', perm: 'attendance.view', page: pagePolicies },
    ] },
    { key: 'reports', label: T('التقارير', 'Reports'), icon: 'report', items: [
      { r: 'reports', label: T('كل التقارير', 'All reports'), icon: 'report', perm: 'reports.view', page: pageReports },
      { r: 'reports/exports', label: T('ملفات التصدير', 'Export tasks'), icon: 'download', perm: 'reports.view', page: pageExportTasks },
      { r: 'reports/daily', label: T('الحضور اليومي', 'Daily attendance'), icon: 'list', perm: 'reports.view', page: (c) => pageReport(c, 'daily') },
      { r: 'reports/summary', label: T('الملخص الشهري', 'Monthly summary'), icon: 'list', perm: 'reports.view', page: (c) => pageReport(c, 'summary') },
      { r: 'reports/monthly_status', label: T('كشف الحضور الشهري', 'Monthly status'), icon: 'cal', perm: 'reports.view', page: (c) => pageReport(c, 'monthly_status') },
      { r: 'reports/late', label: T('التأخير', 'Late'), icon: 'clock', perm: 'reports.view', page: (c) => pageReport(c, 'late') },
      { r: 'reports/absent', label: T('الغياب', 'Absence'), icon: 'exit', perm: 'reports.view', page: (c) => pageReport(c, 'absent') },
    ] },
    { key: 'system', label: T('النظام', 'System'), icon: 'system', items: [
      { r: 'system/settings', label: T('إعدادات النظام', 'Settings'), icon: 'system', perm: 'system.admin', page: pageSettings },
      { r: 'system/alerts', label: T('التنبيهات', 'Alerts'), icon: 'bell', perm: 'system.admin', page: pageAlerts },
      { r: 'system/users', label: T('المستخدمون', 'Users'), icon: 'people', perm: 'system.admin', page: pageUsers },
      { r: 'system/roles', label: T('الأدوار والصلاحيات', 'Roles'), icon: 'shield', perm: 'system.admin', page: pageRoles },
      { r: 'system/backup', label: T('النسخ الاحتياطي', 'Backup'), icon: 'db', perm: 'system.admin', page: pageBackup },
      { r: 'system/experiments', label: T('التشغيل التجريبي', 'Trial runs'), icon: 'check', perm: 'system.admin', page: pageExperiments },
      { r: 'system/demo', label: T('تجربة البرنامج ببيانات افتراضية', 'Full application demo'), icon: 'device', perm: 'system.admin', page: pageDemoEnvironments },
      { r: 'system/health', label: T('صحة التشغيل', 'Operational health'), icon: 'monitor', perm: 'system.admin', page: pageOperationalHealth },
      { r: 'system/audit', label: T('سجل التدقيق', 'Audit log'), icon: 'list', perm: 'system.admin', page: pageAudit },
      { r: 'system/about', label: T('حول البرنامج', 'About'), icon: 'badge', page: pageAbout },
    ] },
  ];
}
const REPORT_ROUTE = /^reports\/(\w+)$/;

function renderShell() {
  document.body.innerHTML = `
    <header class="topbar">
      <div class="logo"><button class="btn link menu-toggle" aria-label="menu">☰</button><div class="mark">${BRAND().mark}</div><span>${esc(BRAND().name)}<small>${esc(T('نظام الحضور والانصراف', 'Time & Attendance'))}</small></span></div>
      <nav class="modules"></nav>
      <div class="top-right">
        <span class="live-dot" title="${esc(T('التحديث الفوري', 'Live updates'))}"></span>
        <div class="dropdown bell-dd" id="bell"><button class="icon-btn" aria-label="${esc(T('الإشعارات', 'Notifications'))}" title="${esc(T('الإشعارات', 'Notifications'))}">${icon('bell')}<b class="bell-n hidden"></b></button>
          <div class="menu bell-menu" style="inset-inline-start:auto;inset-inline-end:0"></div></div>
        <button class="chip" id="langBtn">${icon('lang').replace('<svg', '<svg style="width:14px;height:14px;vertical-align:-2px"')} <span class="u-name">${App.lang === 'ar' ? 'English' : 'العربية'}</span></button>
        <div class="dropdown" id="userMenu"><button class="chip">👤 <span class="u-name">${esc(App.user.full_name || App.user.username)}</span> ▾</button>
          <div class="menu" style="inset-inline-start:auto;inset-inline-end:0">
            <button data-u="pw">${icon('shield')}${esc(T('تغيير كلمة المرور', 'Change password'))}</button>
            <button data-u="out">${icon('exit')}${esc(T('تسجيل الخروج', 'Log out'))}</button></div></div>
      </div>
    </header>
    <button class="nav-scrim" aria-label="${esc(T('إغلاق القائمة', 'Close menu'))}" hidden></button>
    <aside class="sidebar" id="app-sidebar" aria-label="${esc(T('قائمة الصفحات', 'Page navigation'))}"></aside>
    <main class="main"><div class="crumbs"></div><div id="page"></div></main>`;
  const nav = $('.modules');
  for (const m of modules()) {
    const visible = m.items.filter(i => !i.perm || can(i.perm));
    if (!visible.length) continue;
    const a = h(`<a href="#/${visible[0].r}" data-mod="${m.key}">${icon(m.icon)}<span>${esc(m.label)}</span></a>`);
    nav.appendChild(a);
  }
  $('#langBtn').onclick = async () => { setLang(App.lang === 'ar' ? 'en' : 'ar'); try { await POST('/api/auth/language', { language: App.lang }); } catch (e) { /* offline */ } renderShell(); route(); };
  const um = $('#userMenu');
  $('button', um).onclick = (e) => { e.stopPropagation(); um.classList.toggle('open'); };
  $('[data-u=pw]', um).onclick = () => { um.classList.remove('open'); changePassword(); };
  $('[data-u=out]', um).onclick = async () => { await POST('/api/auth/logout'); Live.stop(); App.user = null; showLogin(); };
  const menu = $('.menu-toggle'), scrim = $('.nav-scrim');
  menu.setAttribute('aria-controls', 'app-sidebar'); menu.setAttribute('aria-expanded', 'false');
  menu.setAttribute('aria-label', T('فتح قائمة الصفحات', 'Open page navigation'));
  const closeMenu = () => { document.body.classList.remove('menu-open'); menu.setAttribute('aria-expanded', 'false'); scrim.hidden = true; };
  menu.onclick = () => { const open = document.body.classList.toggle('menu-open'); menu.setAttribute('aria-expanded', String(open)); scrim.hidden = !open; };
  scrim.onclick = closeMenu;
  Live.keep = [];
  bell();
  enhanceApplicationShell();
}

// ============================================================== the bell (in-app notifications)
const LEVEL_DOT = { good: '#0ca30c', info: '#2a78d6', warn: '#fab219', bad: '#d03b3b' };
function ago(ts) {
  const s = Math.max(0, (Date.now() - new Date(String(ts).replace(' ', 'T'))) / 1000);
  if (s < 60) return T('الآن', 'now');
  if (s < 3600) return T(`قبل ${Math.floor(s / 60)} د`, `${Math.floor(s / 60)}m ago`);
  if (s < 86400) return T(`قبل ${Math.floor(s / 3600)} س`, `${Math.floor(s / 3600)}h ago`);
  return String(ts).slice(0, 10);
}
function bell() {
  const dd = $('#bell'); if (!dd) return;
  const btn = $('button', dd), badge = $('.bell-n', dd), menu = $('.bell-menu', dd);
  let rows = [], unread = 0;
  const paint = () => {
    badge.textContent = unread > 99 ? '99+' : unread; badge.classList.toggle('hidden', !unread);
    menu.innerHTML = `<div class="bell-head"><b>${esc(T('الإشعارات', 'Notifications'))}</b>
        ${unread ? `<button class="btn link small" data-all>${esc(T('تعليم الكل كمقروء', 'Mark all read'))}</button>` : ''}</div>
      <div class="bell-list">${rows.length ? rows.map(n => `<a class="bell-item ${n.read_at ? '' : 'new'}" data-id="${n.id}" href="${esc(n.link || '#/system/alerts')}">
          <i style="background:${LEVEL_DOT[n.level] || LEVEL_DOT.info}"></i>
          <span><b>${esc(n.title)}</b><small>${esc(n.body)}</small></span><em>${esc(ago(n.created_at))}</em></a>`).join('')
        : `<div class="bell-empty">${icon('bell')}<span>${esc(T('لا توجد إشعارات', 'You are all caught up'))}</span></div>`}</div>
      ${can('system.admin') ? `<a class="bell-foot" href="#/system/alerts">${icon('system')}${esc(T('إعداد التنبيهات', 'Alert settings'))}</a>` : ''}`;
    const all = $('[data-all]', menu);
    if (all) all.onclick = async (e) => { e.stopPropagation(); await POST('/api/notifications/read'); load(); };
    $$('.bell-item', menu).forEach(a => a.onclick = () => { dd.classList.remove('open'); POST('/api/notifications/read', { ids: [+a.dataset.id] }).then(load).catch(() => {}); });
  };
  const load = async () => { try { const r = await GET('/api/notifications?limit=30'); const was = unread; rows = r.rows; unread = r.unread; paint(); if (unread > was && was >= 0 && load.done) btn.classList.add('ring'); setTimeout(() => btn.classList.remove('ring'), 1200); load.done = true; } catch (e) { /* offline */ } };
  btn.onclick = (e) => { e.stopPropagation(); $$('.dropdown.open').forEach(x => x !== dd && x.classList.remove('open')); dd.classList.toggle('open'); };
  menu.onclick = (e) => e.stopPropagation();
  Live.always('notify', load);
  load();
}
document.addEventListener('click', () => $$('.dropdown.open').forEach(d => d.classList.remove('open')));

function route() {
  App.timers.forEach(t => clearInterval(t)); App.timers = []; Live.clear(); Live.start();
  document.body.classList.remove('menu-open');
  $('.menu-toggle')?.setAttribute('aria-expanded', 'false'); if ($('.nav-scrim')) $('.nav-scrim').hidden = true;
  const path = (location.hash.replace(/^#\/?/, '') || 'dashboard').split('?')[0];
  $$('.mobile-bottom-nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#/' + path));
  const mods = modules();
  let mod = null, item = null;
  for (const m of mods) for (const i of m.items) if (i.r === path) { mod = m; item = i; }
  const rm = path.match(REPORT_ROUTE);
  if (!item && rm) { mod = mods.find(m => m.key === 'reports'); item = { r: path, label: T('تقرير', 'Report'), page: (c) => pageReport(c, rm[1]) }; }
  if (!item) { mod = mods[0]; item = mod.items[0]; }
  $$('.modules a').forEach(a => a.classList.toggle('active', a.dataset.mod === mod.key));
  const side = $('.sidebar');
  side.innerHTML = `<h4>${esc(mod.label)}</h4>` + mod.items.filter(i => !i.perm || can(i.perm)).map(i =>
    `<a href="#/${i.r}" class="${i.r === item.r ? 'active' : ''}" ${i.r === item.r ? 'aria-current="page"' : ''}>${icon(i.icon)}<span>${esc(i.label)}</span></a>`).join('');
  $('.crumbs').textContent = `${mod.label} / ${item.label}`;
  const page = $('#page'); page.innerHTML = '';
  if (item.perm && !can(item.perm)) { page.innerHTML = `<div class="alert">${esc(T('ليست لديك صلاحية لهذه الصفحة', 'You do not have permission for this page'))}</div>`; return; }
  Promise.resolve(item.page(page, item)).catch(e => { page.innerHTML = `<div class="alert">${esc(e.message)}</div>`; });
}
window.addEventListener('hashchange', () => App.user && route());

function title(c, text, right = '') { c.appendChild(h(`<div class="page-title"><h1>${esc(text)}</h1><div>${right}</div></div>`)); }

// ============================================================== login
function showLogin(msg) {
  App.passwordDialog?.close(); App.passwordDialog = null;
  App.timers.forEach(t => clearInterval(t)); App.timers = [];
  document.body.innerHTML = `<div class="login-wrap"><form class="login" method="post" action="#">
    <div class="logo" style="width:auto;padding:0;margin-bottom:14px;color:var(--brand-dark)"><div class="mark" style="color:#fff">${BRAND().mark}</div><b>${esc(BRAND().name)}</b></div>
    <h2>${esc(T('تسجيل الدخول', 'Sign in'))}</h2><p>${esc(T('نظام الحضور والانصراف لأجهزة البصمة', 'Time & attendance for biometric terminals'))}</p>
    <div class="field"><label>${esc(T('اسم المستخدم', 'Username'))}</label><input class="inp" name="u" autocomplete="username" required></div>
    <div class="field"><label>${esc(T('كلمة المرور', 'Password'))}</label><input class="inp" name="p" type="password" autocomplete="current-password" required></div>
    <div class="alert hidden" id="lerr"></div>
    <button class="btn primary" type="submit">${esc(T('دخول', 'Sign in'))}</button>
    <div class="lang"><a href="#" id="lsw">${App.lang === 'ar' ? 'English' : 'العربية'}</a></div></form></div>`;
  if (msg) { $('#lerr').textContent = msg; $('#lerr').classList.remove('hidden'); }
  $('#lsw').onclick = (e) => { e.preventDefault(); setLang(App.lang === 'ar' ? 'en' : 'ar'); showLogin(); };
  $('.login').onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      const r = await api('POST', '/api/auth/login', { username: $('[name=u]').value, password: $('[name=p]').value }, { noAuthRedirect: true });
      await startApp(r.user);
    } catch (e) { $('#lerr').textContent = e.message; $('#lerr').classList.remove('hidden'); }
  };
  setTimeout(() => $('[name=u]').focus(), 20);
}
async function startApp(user) {
  App.user = user;
  App.settings = await GET('/api/settings');
  App.clockUTC = Date.parse(App.settings._server?.server_time_utc) || Date.now();
  App.clockStarted = performance.now();
  if (user.language && user.language !== App.lang && !localStorage.getItem('app_lang')) setLang(user.language);
  App.lookups = null;
  renderShell();
  if (!location.hash || location.hash === '#login') location.hash = '#/dashboard'; else route();
  if (user.must_change_password) changePassword(true);
}
function changePassword(forced) {
  if (App.passwordDialog?.el.isConnected) return App.passwordDialog;
  forced = !!forced || !!App.user?.must_change_password;
  const fields = [
    { key: 'old_password', label: T('كلمة المرور الحالية', 'Current password'), type: 'password', required: true },
    { key: 'new_password', label: T('كلمة المرور الجديدة (10 أحرف على الأقل)', 'New password (min 10)'), type: 'password', required: true, minlength: 10, maxlength: 200 },
    { key: 'confirm', label: T('تأكيد كلمة المرور', 'Confirm password'), type: 'password', required: true },
  ];
  const d = dialog({ title: forced ? T('يرجى تغيير كلمة المرور الافتراضية', 'Please change the default password') : T('تغيير كلمة المرور', 'Change password'),
    size: 'narrow', closable: !forced, onClose: () => { App.passwordDialog = null; }, body: formHtml(fields, {}, 'one'), buttons: [...(forced ? [] : [{ label: T('إلغاء', 'Cancel') }]), { label: T('حفظ', 'Save'), cls: 'primary', action: async () => {
      const v = readForm(d.content, fields);
      if (v.new_password !== v.confirm) throw new Error(T('كلمتا المرور غير متطابقتين', 'Passwords do not match'));
      await POST('/api/auth/password', v); App.user.must_change_password = false; App.lookups = null; toast(T('تم تغيير كلمة المرور', 'Password changed'), 'ok'); route();
    } }] });
  App.passwordDialog = d; return d;
}

// ============================================================== dashboard
// ============================================================== dashboard
const VIZ = { good: '#0ca30c', warn: '#fab219', bad: '#d03b3b', leave: '#4a3aa7', idle: '#c9c7bf', s1: '#2a78d6', s2: '#eb6834' };
const fmtN = (n) => Number(n || 0).toLocaleString('en-US');
const pct = (n) => (n == null ? '—' : `${Math.round(n)}%`);
function vizTip() {
  let t = document.getElementById('viz-tip');
  if (!t) { t = document.createElement('div'); t.id = 'viz-tip'; t.className = 'viz-tip hidden'; t.setAttribute('role', 'tooltip'); document.body.appendChild(t); }
  return t;
}
function bindTips(root) {
  const tip = vizTip();
  const show = (el, x, y) => {
    tip.replaceChildren();
    for (const line of (el.dataset.tip || '').split('\n')) {
      const [v, ...rest] = line.split('|'); const row = document.createElement('div');
      const b = document.createElement('b'); b.textContent = v; row.appendChild(b);
      if (rest.length) { const sp = document.createElement('span'); sp.textContent = ' ' + rest.join('|'); row.appendChild(sp); }
      tip.appendChild(row);
    }
    tip.classList.remove('hidden');
    const r = tip.getBoundingClientRect();
    tip.style.left = Math.min(window.innerWidth - r.width - 8, Math.max(8, x + 12)) + 'px';
    tip.style.top = Math.max(8, y - r.height - 12) + 'px';
  };
  root.addEventListener('pointermove', (e) => { const el = e.target.closest('[data-tip]'); if (el && root.contains(el)) show(el, e.clientX, e.clientY); else tip.classList.add('hidden'); });
  root.addEventListener('pointerleave', () => tip.classList.add('hidden'));
  root.addEventListener('focusin', (e) => { const el = e.target.closest('[data-tip]'); if (el) { const r = el.getBoundingClientRect(); show(el, r.left + r.width / 2, r.top); } });
  root.addEventListener('focusout', () => tip.classList.add('hidden'));
}
function sparkline(values, color) {
  const v = values.map(x => x || 0), W = 96, H = 26, mx = Math.max(1, ...v);
  const pts = v.map((y, i) => `${(i * W / Math.max(1, v.length - 1)).toFixed(1)},${(H - 3 - (y / mx) * (H - 6)).toFixed(1)}`);
  const last = pts[pts.length - 1].split(',');
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><polyline points="${pts.join(' ')}" fill="none" stroke="#b9c3cf" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${last[0]}" cy="${last[1]}" r="3.5" fill="${color}" stroke="#fff" stroke-width="2"/></svg>`;
}
function delta(now, before, upIsGood) {
  if (before == null) return '';
  const d = (now || 0) - (before || 0);
  if (!d) return `<span class="delta flat">= ${esc(T('مثل أمس', 'same as yesterday'))}</span>`;
  const good = upIsGood ? d > 0 : d < 0;
  return `<span class="delta ${good ? 'up' : 'down'}">${d > 0 ? '▲' : '▼'} ${fmtN(Math.abs(d))} ${esc(T('عن أمس', 'vs yesterday'))}</span>`;
}
function columnsChart({ labels, stacks, line, height = 210, yLabel = '' }) {
  // stacks: [{name, color, values[]}] stacked from the baseline; line: {name, color, values[]} on the same axis
  const n = labels.length, W = 640, H = height, L = 34, R = 8, Tp = 10, B = 26;
  const totals = labels.map((_, i) => stacks.reduce((a, s) => a + (s.values[i] || 0), 0));
  const top = Math.max(1, ...totals, ...(line ? line.values : [0]));
  const mag = Math.pow(10, Math.floor(Math.log10(top / 4 || 1))); const tickStep = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 7.5, 8, 10].map(f => f * mag).find(st => st * 4 >= top * 1.05) || 10 * mag; const yMax = Math.max(4, tickStep * 4);
  const y = (v) => Tp + (H - Tp - B) * (1 - v / yMax);
  const band = (W - L - R) / n, bw = Math.min(24, band * 0.62);
  let g = '';
  for (let k = 0; k <= 4; k++) { const v = yMax * k / 4; g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid-l"/><text x="${L - 6}" y="${y(v) + 4}" class="tick" text-anchor="end">${fmtN(v)}</text>`; }
  let marks = '';
  labels.forEach((lab, i) => {
    const cx = L + band * i + band / 2; let base = 0;
    const visible = stacks.map((s, k) => ({ s, k, v: s.values[i] || 0 })).filter(o => o.v > 0);
    visible.forEach((o, j) => {
      const y0 = y(base), y1 = y(base + o.v); base += o.v;
      const hgt = Math.max(0, y0 - y1 - (j ? 2 : 0)); const isTop = j === visible.length - 1;
      const r = isTop ? Math.min(4, hgt / 2) : 0, x = cx - bw / 2, yy = y1;
      const path = r ? `M${x},${yy + hgt} V${yy + r} Q${x},${yy} ${x + r},${yy} H${x + bw - r} Q${x + bw},${yy} ${x + bw},${yy + r} V${yy + hgt} Z` : `M${x},${yy} H${x + bw} V${yy + hgt} H${x} Z`;
      marks += `<path d="${path}" fill="${o.s.color}"/>`;
    });
    const tip = [lab, ...stacks.map(s => `${fmtN(s.values[i])}|${s.name}`), ...(line ? [`${fmtN(line.values[i])}|${line.name}`] : [])].join('\n');
    marks += `<rect x="${cx - band / 2}" y="${Tp}" width="${band}" height="${H - Tp - B}" fill="transparent" data-tip="${esc(tip)}" tabindex="0" class="hit"/>`;
    if (n <= 14 || i % 2 === 0) g += `<text x="${cx}" y="${H - 8}" class="tick" text-anchor="middle">${esc(lab)}</text>`;
  });
  let ln = '';
  if (line) {
    const pts = line.values.map((v, i) => `${L + band * i + band / 2},${y(v || 0)}`).join(' ');
    ln = `<polyline points="${pts}" fill="none" stroke="${line.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" pointer-events="none"/>`;
  }
  return `<svg class="viz" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(yLabel)}"><line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" class="axis-l"/>${g}${marks}${ln}</svg>`;
}
function legend(items) {
  return `<div class="legend">${items.map(i => `<span><i class="${i.line ? 'k-line' : 'k-box'}" style="background:${i.color}"></i>${esc(i.name)}${i.value != null ? ` <b>${fmtN(i.value)}</b>` : ''}</span>`).join('')}</div>`;
}
async function pageDashboard(c) {
  const hr = new Date().getHours();
  const hello = hr < 12 ? T('صباح الخير', 'Good morning') : hr < 18 ? T('مساء الخير', 'Good afternoon') : T('مساء الخير', 'Good evening');
  const head = h(`<div class="dash-head"><div><h2>${esc(hello)}${T('، ', ', ')}${esc((App.user.full_name || App.user.username).split(' ')[0])} 👋</h2>
    <span class="muted">${esc(new Date().toLocaleDateString(App.lang === 'ar' ? 'ar' : 'en-GB', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' }))}</span></div>
    <div class="quick">${[
      can('personnel.edit') && ['#/personnel/employees', 'people', T('الموظفون', 'Employees')],
      can('device.view') && ['#/device/terminals', 'device', T('الأجهزة', 'Terminals')],
      can('attendance.view') && ['#/att/leaves', 'leave', T('الطلبات', 'Requests')],
      can('reports.view') && ['#/reports', 'report', T('التقارير', 'Reports')],
      can('attendance.view') && ['#/monitor', 'monitor', T('المراقبة', 'Monitor')],
    ].filter(Boolean).map(([href, ic, label]) => `<a class="q" href="${href}">${icon(ic)}<span>${esc(label)}</span></a>`).join('')}
    <span class="live-pill"><i></i>${esc(T('مباشر', 'Live'))} <b class="upd"></b></span></div></div>`);
  c.appendChild(head);
  // the newest punches, with photos, first thing on the page; a punch shows up the moment it arrives
  if (can('attendance.view')) {
    const strip = h(`<section class="pstrip"><div class="ps-head"><h3>${esc(T('الحركات الآن', 'Punches right now'))}</h3><a href="#/monitor">${esc(T('المراقبة الحية', 'Live monitor'))} ›</a></div><div class="ps-row"></div></section>`);
    c.appendChild(strip);
    const row = $('.ps-row', strip);
    let stripDate = '', stripBusy = false;
    const card = (x, fresh) => `<div class="ps ${fresh ? 'new' : ''}" data-id="${x.id}" title="${esc(`${x.emp_code} · ${x.department || ''}`)}">
        <span class="ps-ph">${x.employee_has_photo ? `<img src="/api/employees/${x.employee_id}/photo" alt="" loading="lazy">` : `<b>${esc((x.name || x.emp_code || '?').trim().charAt(0))}</b>`}<i class="st${x.punch_state}"></i></span>
        <span class="ps-name">${esc(x.name || x.emp_code)}</span>
        <span class="ps-time ltr">${esc(x.punch_time.slice(11, 16))}</span>
        <span class="ps-sub">${esc(stateName(x.punch_state))} · <bdi>${esc(x.device || '')}</bdi></span></div>`;
    const put = (rows, fresh) => {
      if (!rows.length) return;
      if (!row.querySelector('.ps')) row.innerHTML = '';
      row.insertAdjacentHTML('afterbegin', rows.map(x => card(x, fresh)).join(''));
      while (row.children.length > 40) row.lastElementChild.remove();
    };
    try {
      const r = await GET('/api/monitor?limit=40');
      if (r.rows.length) put(r.rows, false); else row.innerHTML = `<div class="empty">${esc(T('لا توجد حركات بعد اليوم', 'No punches yet'))}</div>`;
      stripDate = r.date || '';
    } catch (e) { row.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
    const pull = async () => { if (!strip.isConnected || stripBusy) return; stripBusy = true; try { const n = await GET('/api/monitor?limit=40'); if (!strip.isConnected) return; const oldIds = new Set($$('.ps[data-id]', row).map(x => +x.dataset.id)); row.innerHTML = n.rows.length ? n.rows.map(x => card(x, stripDate === n.date && !oldIds.has(x.id))).join('') : `<div class="empty">${esc(T('لا توجد حركات اليوم', 'No movements today'))}</div>`; stripDate = n.date || ''; } catch (e) { /* next event retries */ } finally { stripBusy = false; } };
    Live.on('punch', pull, 300);
    App.timers.push(setInterval(() => document.visibilityState === 'visible' && pull(), 15000));
  }
  const box = h('<div class="dash"></div>'); c.appendChild(box);
  bindTips(box);
  const draw = async () => {
    const d = await GET('/api/dashboard');
    const y = d.yesterday || {};
    const onTime = Math.max(0, d.present - d.late);
    const rate = d.expected ? 100 * d.present / d.expected : null;
    const comp = [
      { name: T('في الموعد', 'On time'), color: VIZ.good, value: onTime, icon: '✓' },
      { name: T('متأخر', 'Late'), color: VIZ.warn, value: d.late, icon: '◷' },
      { name: T('غائب', 'Absent'), color: VIZ.bad, value: d.absent, icon: '✕' },
      { name: T('إجازة', 'On leave'), color: VIZ.leave, value: d.leave, icon: '✈' },
      { name: T('لم يصل بعد', 'Not in yet'), color: VIZ.idle, value: d.not_yet, icon: '…' },
    ];
    const compTotal = Math.max(1, comp.reduce((a, x) => a + x.value, 0));
    const meter = comp.filter(x => x.value).map(x => `<i style="flex:${x.value};background:${x.color}" data-tip="${esc(`${fmtN(x.value)}|${x.name} (${Math.round(100 * x.value / compTotal)}%)`)}" tabindex="0"></i>`).join('');
    const tr = d.trend;
    const tile = (label, value, extra, spark, href) => `<a class="tile" ${href ? `href="#/${href}"` : ''}><span class="t-label">${esc(label)}</span><span class="t-value">${value}</span><span class="t-foot">${extra || ''}${spark || ''}</span></a>`;
    const days = tr.map(t => `${+t.date.slice(8, 10)}/${+t.date.slice(5, 7)}`);
    const hours = [...Array(24).keys()].map(h => String(h).padStart(2, '0'));
    const firstH = Math.max(0, Math.min(5, d.hourly.findIndex(v => v > 0) >= 0 ? d.hourly.findIndex(v => v > 0) : 5));
    const devs = d.device_list;
    box.innerHTML = `
      ${App.user.must_change_password ? `<div class="alert">${esc(T('ما زلت تستخدم كلمة المرور الافتراضية admin — غيّرها من قائمة المستخدم.', 'You are still using the default password — change it from the user menu.'))}</div>` : ''}
      <section class="hero card2">
        <div class="hero-main">
          <span class="t-label">${esc(T('نسبة الحضور اليوم', 'Attendance rate today'))}</span>
          <span class="hero-num">${pct(rate)}</span>
          <span class="muted">${esc(T(`${fmtN(d.present)} من ${fmtN(d.expected)} موظفاً مطلوب حضورهم اليوم`, `${fmtN(d.present)} of ${fmtN(d.expected)} expected today`))}${y.rate != null ? ` · ${esc(T('أمس', 'yesterday'))} ${pct(y.rate)}` : ''}</span>
        </div>
        <div class="hero-side">
          <div class="meter" role="img" aria-label="${esc(T('توزيع حالة الموظفين اليوم', 'Employee status today'))}">${meter || '<i style="flex:1;background:#e7e9ee"></i>'}</div>
          <div class="comp">${comp.map(x => `<span><i style="background:${x.color}"></i><em>${x.icon}</em>${esc(x.name)} <b>${fmtN(x.value)}</b></span>`).join('')}</div>
        </div>
      </section>
      <section class="tiles">
        ${tile(T('الموظفون النشطون', 'Active employees'), fmtN(d.employees), '', '', 'personnel/employees')}
        ${tile(T('حضروا اليوم', 'Came in today'), fmtN(d.present), delta(d.present, y.present, true), sparkline(tr.map(t => t.present), VIZ.good), 'att/calc')}
        ${tile(T('متأخرون', 'Late'), fmtN(d.late), delta(d.late, y.late, false), sparkline(tr.map(t => t.late), VIZ.warn), 'att/calc')}
        ${tile(T('غائبون', 'Absent'), fmtN(d.absent), delta(d.absent, y.absent, false), sparkline(tr.map(t => t.absent), VIZ.bad), 'att/calc')}
        ${tile(T('حركات اليوم', 'Punches today'), fmtN(d.punches_today), '', '', 'device/transactions')}
        ${tile(T('الأجهزة المتصلة', 'Terminals online'), `${fmtN(d.online)}<small>/${fmtN(d.devices)}</small>`, d.offline ? `<span class="delta down">● ${fmtN(d.offline)} ${esc(T('غير متصل', 'offline'))}</span>` : `<span class="delta up">● ${esc(T('كلها تعمل', 'all working'))}</span>`, '', 'device/terminals')}
      </section>
      ${d.pending.leaves + d.pending.manual + d.pending.overtime ? `<div class="alert info">${esc(T('طلبات بانتظار الموافقة', 'Pending approvals'))}: ${esc(T('إجازات', 'leave'))} ${d.pending.leaves} · ${esc(T('بصمات يدوية', 'manual punches'))} ${d.pending.manual} · ${esc(T('عمل إضافي', 'overtime'))} ${d.pending.overtime}</div>` : ''}
      <section class="dash-cols">
        <div class="card2 span2">
          <div class="c-head"><h3>${esc(T('إيقاع اليوم: الحركات في كل ساعة', 'Today’s rhythm: punches per hour'))}</h3></div>
          ${legend([{ name: T('اليوم', 'Today'), color: VIZ.s1 }, { name: T('متوسط آخر 7 أيام', '7-day average'), color: VIZ.s2, line: true }])}
          <div class="viz-wrap" dir="ltr">${columnsChart({ labels: hours.slice(firstH), stacks: [{ name: T('اليوم', 'Today'), color: VIZ.s1, values: d.hourly.slice(firstH) }], line: { name: T('متوسط 7 أيام', '7-day average'), color: VIZ.s2, values: d.hourly_avg.slice(firstH) }, yLabel: T('الحركات في كل ساعة', 'Punches per hour') })}</div>
        </div>
        <div class="card2 tall">
          <div class="c-head"><h3>${esc(T('الأقسام اليوم', 'Departments today'))}</h3></div>
          <div class="depts">${d.departments.map(x => `<div class="dept" data-tip="${esc(`${pct(x.rate)}|${x.department}\n${fmtN(x.present)}|${T('حضروا', 'came in')} / ${fmtN(x.expected)}\n${fmtN(x.late)}|${T('متأخر', 'late')}`)}" tabindex="0">
              <span class="d-name">${esc(x.department)}</span><span class="d-bar"><i style="width:${x.rate || 0}%"></i></span><span class="d-val">${pct(x.rate)} <small>${fmtN(x.present)}/${fmtN(x.expected)}</small></span></div>`).join('') || `<div class="empty">${esc(T('لا توجد بيانات', 'No data'))}</div>`}</div>
        </div>
        <div class="card2 span2">
          <div class="c-head"><h3>${esc(T('آخر 14 يوماً', 'Last 14 days'))}</h3></div>
          ${legend([{ name: T('في الموعد', 'On time'), color: VIZ.good }, { name: T('متأخر', 'Late'), color: VIZ.warn }, { name: T('غائب', 'Absent'), color: VIZ.bad }])}
          <div class="viz-wrap" dir="ltr">${columnsChart({ labels: days, stacks: [
            { name: T('في الموعد', 'On time'), color: VIZ.good, values: tr.map(t => Math.max(0, t.present - t.late)) },
            { name: T('متأخر', 'Late'), color: VIZ.warn, values: tr.map(t => t.late) },
            { name: T('غائب', 'Absent'), color: VIZ.bad, values: tr.map(t => t.absent) }], yLabel: T('الحضور اليومي', 'Daily attendance') })}</div>
        </div>
      </section>
      <section class="card2">
        <div class="c-head"><h3>${esc(T('صحة الأجهزة', 'Terminal health'))}</h3><a href="#/device/terminals">${esc(T('إدارة الأجهزة', 'Manage terminals'))} ›</a></div>
        <div class="devgrid">${devs.map(x => `<a class="dev ${x.state}" href="#/device/terminals">
            <span class="dv-top"><i class="dot"></i><b><bdi>${esc(x.alias || x.sn)}</bdi></b><span class="tag">${x.link}</span></span>
            <span class="muted ltr">${esc(x.ip || '')} · ${esc(stateLabel(x.state))}${x.transferring ? ' · ⇅' : ''}</span>
            <span class="dv-counts"><span title="${esc(T('المستخدمون', 'Users'))}">👤 ${fmtN(x.users)}</span><span title="${esc(T('البصمات', 'Fingerprints'))}">☝ ${fmtN(x.fps)}</span><span title="${esc(T('الوجوه', 'Faces'))}">☺ ${fmtN(x.faces)}</span><span title="${esc(T('الكف', 'Palms'))}">✋ ${fmtN(x.palms)}</span><span title="${esc(T('السجلات', 'Records'))}">≡ ${fmtN(x.records)}</span></span>
            <span class="muted small">${esc(T('آخر اتصال', 'Last seen'))}: <bdi>${esc(x.last_activity || '—')}</bdi></span></a>`).join('') || `<div class="empty">${esc(T('لا توجد أجهزة بعد', 'No terminals yet'))}</div>`}</div>
      </section>`;
    $('.upd', head).textContent = new Date().toLocaleTimeString(App.lang === 'ar' ? 'ar' : 'en-GB', { hour: '2-digit', minute: '2-digit' });
  };
  await draw();
  Live.on(['punch', 'device', 'people'], () => draw().catch(() => {}), 5000);
  App.timers.push(setInterval(() => { if (document.visibilityState === 'visible') draw().catch(() => {}); }, 120000));
}
function stateLabel(s) { return { online: T('متصل', 'Online'), offline: T('غير متصل', 'Offline'), disabled: T('معطل', 'Disabled') }[s] || s; }
function avatar(r) {
  return r.employee_id && r.employee_has_photo ? `<span class="avatar"><img src="/api/employees/${r.employee_id}/photo" alt="" loading="lazy"></span>` : `<span class="avatar">${esc((r.name || r.emp_code || '?').trim().charAt(0))}</span>`;
}

// ============================================================== real-time monitor
async function pageMonitor(c) {
  title(c, T('جميع حركات اليوم', 'All of today’s movements'));
  const lk = await lookups();
  const p = h(`<section class="panel monitor-page"><div class="monitor-summary"><div><span class="muted">${esc(T('تاريخ اليوم على الخادم', 'Today on the server'))}</span><b class="monitor-date ltr">—</b></div><div><span class="muted">${esc(T('الحركات المطابقة', 'Matching movements'))}</span><b class="monitor-total">—</b></div><span class="monitor-live badge info" role="status">${esc(T('جارٍ الاتصال', 'Connecting'))}</span></div>
    <p class="monitor-note">${esc(T('تعرض هذه الشاشة كل بصمة خام وصلت بتاريخ اليوم، بما فيها الحركات المتكررة زمنياً والدخول والخروج. سجل الحضور اليومي يحسب نتيجة الموظف بعد تطبيق الدوام والسماح واستبعاد التكرار؛ لذلك عدد الحركات يختلف عن عدد الموظفين.', 'This screen lists every raw movement dated today, including check-ins, check-outs and closely repeated punches. Daily attendance applies the schedule, allowances and duplicate rules to calculate each employee’s result; movement counts and employee counts differ.'))}</p>
    <div class="toolbar"><input class="inp search monitor-q" type="search" aria-label="${esc(T('بحث برقم أو اسم الموظف', 'Employee ID or name'))}" placeholder="${esc(T('بحث برقم أو اسم الموظف', 'Employee ID or name'))}"><select class="inp monitor-device" aria-label="${esc(T('الجهاز', 'Device'))}"><option value="">${esc(T('كل الأجهزة', 'All devices'))}</option>${lk.devices.map(d => `<option value="${esc(d.sn)}">${esc(d.name || d.alias || d.sn)}</option>`).join('')}</select><button class="btn monitor-refresh">${icon('refresh')}${esc(T('تحديث', 'Refresh'))}</button><span class="grow"></span><a class="btn link" href="#/reports/daily">${esc(T('نتائج الحضور اليومي', 'Daily attendance results'))}</a></div><div class="monitor-movements"></div><div class="pager"><span class="monitor-range"></span><span class="btns"><button class="btn small monitor-prev" aria-label="${esc(T('السابق', 'Previous'))}" disabled>‹</button><select class="inp monitor-size" aria-label="${esc(T('حركات في الصفحة', 'Movements per page'))}">${[100, 200, 500, 1000].map(n => `<option value="${n}" ${n === 100 ? 'selected' : ''}>${n}</option>`).join('')}</select><button class="btn small monitor-next" aria-label="${esc(T('التالي', 'Next'))}" disabled>›</button></span></div></section>`);
  c.appendChild(p);
  let offset = 0, limit = 100, currentDate = '', running = false, again = false, revision = 0;
  const load = async () => {
    if (!p.isConnected) return;
    if (running) { again = true; return; }
    running = true; const id = revision;
    try {
      const r = await GET('/api/monitor?' + qs({ offset, limit, q: $('.monitor-q', p).value.trim(), device: $('.monitor-device', p).value }));
      if (!p.isConnected || id !== revision) { again = true; return; }
      if (currentDate && r.date !== currentDate && offset) { offset = 0; again = true; return; }
      currentDate = r.date; $('.monitor-date', p).textContent = r.date || '—';
      $('.monitor-total', p).textContent = fmtN(r.total);
      const box = $('.monitor-movements', p), rows = r.rows || [];
      box.innerHTML = rows.length ? rows.map(x => {
        const photo = x.has_photo ? `/api/transactions/${x.id}/photo` : x.employee_has_photo ? `/api/employees/${x.employee_id}/photo` : '';
        return `<article class="movement-row"><span class="movement-photo">${photo ? `<img src="${photo}" alt="" loading="lazy">` : esc((x.name || x.emp_code || '?').trim().charAt(0))}</span><div class="movement-person"><b>${esc(x.name || x.emp_code)}</b><small><bdi>${esc(x.emp_code)}</bdi>${x.department ? ` · ${esc(x.department)}` : ''}</small></div><time class="movement-time ltr" datetime="${esc(x.punch_time)}">${esc(String(x.punch_time || '').slice(11))}</time><div class="movement-state"><span class="badge ${[1, 2, 5].includes(+x.punch_state) ? 'warn' : 'ok'}">${esc(stateName(x.punch_state))}</span><small>${esc(verifyName(x.verify || 'other'))}</small></div><div class="movement-device"><b>${esc(x.device || x.device_sn || '')}</b><small>${esc(T('حركة خام', 'Raw movement'))} #${esc(x.id)}</small></div></article>`;
      }).join('') : `<div class="empty">${esc(T('لا توجد حركات اليوم تطابق البحث', 'No movements today match these filters'))}</div>`;
      const total = r.total || 0, to = Math.min(offset + rows.length, total);
      $('.monitor-range', p).textContent = `${total ? offset + 1 : 0}–${to} / ${fmtN(total)}`;
      $('.monitor-prev', p).disabled = !offset; $('.monitor-next', p).disabled = !r.has_more;
      $('.monitor-live', p).className = 'monitor-live badge ok';
      $('.monitor-live', p).textContent = T('مباشر · ', 'Live · ') + new Date().toLocaleTimeString(App.lang === 'ar' ? 'ar' : 'en-GB', { hour: '2-digit', minute: '2-digit' });
    } catch (e) { if (p.isConnected) { $('.monitor-live', p).className = 'monitor-live badge bad'; $('.monitor-live', p).textContent = T('تعذر التحديث: ', 'Refresh failed: ') + e.message; } }
    finally { running = false; if (again && p.isConnected) { again = false; load(); } }
  };
  const reset = () => { revision++; offset = 0; load(); };
  $('.monitor-q', p).oninput = debounce(reset); $('.monitor-device', p).onchange = reset;
  $('.monitor-refresh', p).onclick = load;
  $('.monitor-prev', p).onclick = () => { revision++; offset = Math.max(0, offset - limit); load(); };
  $('.monitor-next', p).onclick = () => { revision++; offset += limit; load(); };
  $('.monitor-size', p).onchange = (e) => { limit = +e.target.value; reset(); };
  Live.on('punch', load, 800);
  App.timers.push(setInterval(() => { if (document.visibilityState === 'visible') load(); }, 15000));
  await load();
}

// ============================================================== personnel
async function employeeFields(row) {
  const lk = await lookups();
  return [
    { section: T('البيانات الأساسية', 'Basic information') },
    { key: 'emp_code', label: T('رقم الموظف (رقم البصمة)', 'Employee ID (device PIN)'), required: true, readonly: !!row },
    { key: 'first_name', label: T('الاسم الأول', 'First name'), required: true },
    { key: 'last_name', label: T('اسم العائلة', 'Last name') },
    { key: 'name_en', label: T('الاسم بالإنجليزية (اختياري — يُكتب تلقائياً إن تُرك فارغاً)', 'Name in English (optional — written automatically if left empty)'), placeholder: 'e.g. Abdulrahman Al-Maqadma' },
    { key: 'gender', label: T('الجنس', 'Gender'), type: 'select', options: [{ value: 'M', label: T('ذكر', 'Male') }, { value: 'F', label: T('أنثى', 'Female') }] },
    { key: 'department_id', label: T('القسم', 'Department'), type: 'select', options: opts(lk.departments), required: true, default: lk.departments[0]?.id },
    { key: 'position_id', label: T('المسمى الوظيفي', 'Position'), type: 'select', options: opts(lk.positions) },
    { key: 'hire_date', label: T('تاريخ التعيين', 'Hire date'), type: 'date', default: today() },
    { key: 'emp_type', label: T('نوع التوظيف', 'Employment type'), type: 'select', blank: false, options: [{ value: 'permanent', label: T('دائم', 'Permanent') }, { value: 'contract', label: T('عقد', 'Contract') }, { value: 'temporary', label: T('مؤقت', 'Temporary') }, { value: 'probation', label: T('تحت التجربة', 'Probation') }] },
    { key: 'birthday', label: T('تاريخ الميلاد', 'Birthday'), type: 'date' },
    { key: 'national_id', label: T('رقم الهوية', 'National ID') },
    { key: 'mobile', label: T('الجوال', 'Mobile') },
    { key: 'email', label: T('البريد الإلكتروني', 'Email'), type: 'email' },
    { key: 'address', label: T('العنوان', 'Address'), full: true },
    { section: T('إعدادات الجهاز', 'Device settings') },
    { key: 'card_no', label: T('رقم البطاقة', 'Card number') },
    { key: 'dev_password', label: T('كلمة مرور الجهاز', 'Device password'), type: 'password', hint: T('اتركها فارغة للاحتفاظ بالكلمة الحالية؛ لا تُعرض الكلمة المحفوظة.', 'Leave blank to keep the current password; stored passwords are not displayed.') },
    ...(row ? [{ key: 'clear_dev_password', label: T('مسح كلمة مرور الجهاز', 'Clear device password'), type: 'checkbox', text: T('إزالة كلمة المرور المحفوظة لهذا الموظف', 'Remove this employee’s stored device password') }] : []),
    { key: 'dev_privilege', label: T('الصلاحية على الجهاز', 'Device privilege'), type: 'select', blank: false, options: [{ value: 0, label: T('مستخدم عادي', 'User') }, { value: 2, label: T('مسجّل', 'Enroller') }, { value: 6, label: T('مدير', 'Administrator') }, { value: 14, label: T('مدير عام', 'Super administrator') }] },
    { key: 'verify_mode', label: T('طريقة التحقق', 'Verification mode'), type: 'select', blank: false, options: [{ value: -1, label: T('حسب إعداد الجهاز', 'Device default') }, { value: 15, label: T('وجه', 'Face') }, { value: 1, label: T('بصمة إصبع', 'Fingerprint') }, { value: 25, label: T('كف', 'Palm') }, { value: 4, label: T('بطاقة', 'Card') }, { value: 3, label: T('كلمة مرور', 'Password') }, { value: 0, label: T('أي طريقة', 'Any') }] },
    { key: 'enable_att', label: T('الحضور', 'Attendance'), type: 'checkbox', text: T('يُحتسب حضوره', 'Counts for attendance'), default: true },
    { key: 'area_ids', label: T('المناطق (يُرسل الموظف لكل أجهزة المنطقة)', 'Areas (the employee is sent to every device in them)'), type: 'multi', options: opts(lk.areas), default: lk.areas.length ? [lk.areas[0].id] : [] },
  ];
}
function bioCell(r) {
  const parts = [];
  if (r.face_count) parts.push(`<span title="${esc(T('وجه', 'Face'))}">😊 ${r.face_count}</span>`);
  if (r.fp_count) parts.push(`<span title="${esc(T('بصمة إصبع', 'Fingerprint'))}">☝ ${r.fp_count}</span>`);
  if (r.palm_count) parts.push(`<span title="${esc(T('كف', 'Palm'))}">✋ ${r.palm_count}</span>`);
  if (r.card_no) parts.push(`<span title="${esc(T('بطاقة', 'Card'))}">💳</span>`);
  return `<span class="bio">${parts.join('')}</span>`;
}
// ============================================================== employee portal (admin side)
const qrUrl = (text) => `/api/portal/qr.svg?text=${encodeURIComponent(text)}`;
async function pagePortal(c) {
  let info = await GET('/api/portal/info');
  const s = await GET('/api/settings');
  const head = h(`<div class="al-hero pt-hero"><div class="al-hero-ic">${icon('people')}</div>
      <div class="grow"><h1>${esc(T('بوابة الموظف', 'Employee portal'))}</h1><p></p></div>
      <span class="al-saved"></span>${toggle(s['portal.enabled'], 'id="ptOn"')}</div>`);
  c.appendChild(head);
  const saved = $('.al-saved', head);
  const flash = () => { saved.textContent = '✓ ' + T('تم الحفظ', 'Saved'); saved.classList.add('show'); clearTimeout(flash.t); flash.t = setTimeout(() => saved.classList.remove('show'), 1600); };
  const save = async (v) => { await guard(() => PUT('/api/settings', v)); flash(); info = await GET('/api/portal/info'); paint(); };
  $('#ptOn input', head).onchange = (e) => save({ 'portal.enabled': e.target.checked });
  const body = h('<div class="pt"></div>'); c.appendChild(body);
  const paint = () => {
    $('p', head).textContent = info.on ? T('يدخل الموظفون من جوالاتهم ليروا حضورهم ويرسلوا طلباتهم.', 'Employees sign in from their phones to see their attendance and send requests.')
      : T('البوابة متوقفة الآن، لا يستطيع أي موظف الدخول.', 'The portal is off: no employee can sign in.');
    const pct = info.active ? Math.round(100 * info.enabled / info.active) : 0;
    body.innerHTML = `
      <section class="pt-stats">
        <div><b>${fmtN(info.enabled)}<small>/${fmtN(info.active)}</small></b><span>${esc(T('لديهم وصول', 'have access'))}</span><i style="--p:${pct}%"></i></div>
        <div><b>${fmtN(info.signed_in)}</b><span>${esc(T('دخلوا فعلاً', 'signed in'))}</span></div>
        <div><a href="#/att/leaves"><b>${fmtN(info.pending)}</b><span>${esc(T('طلبات بانتظارك', 'requests waiting'))} ›</span></a></div>
      </section>
      <section class="pt-grid">
        <div class="card2 pt-link">
          <div class="c-head"><h3>${esc(T('رابط الموظفين', 'Link for employees'))}</h3></div>
          <div class="pt-qr"><img src="${qrUrl(info.best)}" alt="QR" onerror="this.remove()"></div>
          <div class="pt-url ltr">${esc(info.best)}</div>
          <div class="pt-btns"><button class="btn small" data-copy="${esc(info.best)}">${icon('list')}${esc(T('نسخ', 'Copy'))}</button>
            <a class="btn small" href="${esc(info.best)}" target="_blank" rel="noopener">${icon('exit')}${esc(T('فتح', 'Open'))}</a>
            <button class="btn small primary" data-act="all">${icon('people')}${esc(T('إصدار كلمات مرور لكل الموظفين', 'Passwords for everyone'))}</button></div>
          <p class="muted small">${esc(T('يفتح الموظف الرابط أو يمسح الرمز بكاميرا جواله، ثم «إضافة إلى الشاشة الرئيسية» ليصبح تطبيقاً.', 'Employees open the link or scan the code with the phone camera, then “Add to Home screen” to keep it as an app.'))}</p>
        </div>
        <div class="card2">
          <div class="c-head"><h3>${esc(T('داخل الشبكة (واي فاي المؤسسة)', 'Inside the network (office Wi-Fi)'))}</h3></div>
          ${info.lan.length ? info.lan.map(u => `<div class="pt-row"><span class="ltr">${esc(u)}</span><button class="btn small" data-copy="${esc(u)}">${esc(T('نسخ', 'Copy'))}</button></div>`).join('') : `<p class="muted">${esc(T('لم يُعرف عنوان هذا الحاسوب.', 'This PC’s address is unknown.'))}</p>`}
          <p class="muted small">${esc(info.portal_port ? T(`المنفذ ${info.portal_port} مخصص للبوابة فقط: لا تفتح منه صفحات الإدارة ولا الأجهزة، لذلك هو المنفذ الآمن لفتحه على الإنترنت.`, `Port ${info.portal_port} serves the portal only — no admin pages, no terminals — so it is the safe one to open to the internet.`) : T('منفذ البوابة المستقل غير مفعّل؛ البوابة على منفذ الواجهة /me.', 'The separate portal port is off; the portal is at /me on the web port.'))}</p>
          <div class="c-head" style="padding:12px 0 4px"><h3>${esc(T('من خارج المؤسسة (الإنترنت)', 'From outside (internet)'))}</h3></div>
          <div class="pt-row"><input class="inp ltr grow" id="ptPublic" placeholder="https://portal.example.com" value="${esc(info.public)}"><button class="btn small primary" id="ptSavePublic">${esc(T('حفظ', 'Save'))}</button></div>
          <details class="pt-how"><summary>${esc(T('كيف أفتح البوابة على الإنترنت؟', 'How do I open the portal to the internet?'))}</summary>
            <ol>
              <li>${esc(T('الأسهل والأكثر أماناً: شغّل portal_internet.bat واختر 1 لتجربة رابط مؤقت فوراً (بدون حساب).', 'Easiest and safest: run portal_internet.bat and pick 1 for an instant temporary link (no account).'))}</li>
              <li>${esc(T('لرابط دائم باسم نطاقكم: أنشئ نفقاً مجانياً في Cloudflare (Zero Trust ← Networks ← Tunnels) يوجّه اسم النطاق إلى http://localhost:' + (info.portal_port || info.web_port) + '، ثم شغّل portal_internet.bat واختر 2 والصق الرمز. يعمل كخدمة مع ويندوز وبشهادة HTTPS.', 'For a permanent link on your domain: create a free Cloudflare tunnel (Zero Trust → Networks → Tunnels) pointing your hostname to http://localhost:' + (info.portal_port || info.web_port) + ', then run portal_internet.bat, pick 2 and paste the token. It runs as a Windows service with HTTPS.'))}</li>
              <li>${esc(T('بديل: إعادة توجيه المنفذ ' + (info.portal_port || info.web_port) + ' في الراوتر — ويُفضّل وضعه خلف HTTPS.', 'Alternative: forward port ' + (info.portal_port || info.web_port) + ' on the router — preferably behind HTTPS.'))}</li>
              <li>${esc(T('اكتب الرابط النهائي في الخانة أعلاه ليظهر على البطاقات المطبوعة ورمز QR.', 'Put the final link in the box above so it appears on printed cards and the QR code.'))}</li>
            </ol></details>
        </div>
        <div class="card2">
          <div class="c-head"><h3>${esc(T('ماذا يستطيع الموظف؟', 'What can employees do?'))}</h3></div>
          <div class="pt-row"><span class="grow">${esc(T('إرسال طلبات (إجازة، تصحيح بصمة، عمل إضافي)', 'Send requests (leave, punch correction, overtime)'))}</span>${toggle(s['portal.requests'], 'id="ptReq"')}</div>
          <div class="pt-row"><span class="grow">${esc(T('أقصى عمر لتصحيح البصمة (يوم)', 'Oldest punch that can be corrected (days)'))}</span><input class="inp ltr" type="number" min="1" id="ptDays" style="width:80px" value="${esc(s['portal.correction_days'])}"></div>
          <ul class="ticks-sm"><li>${esc(T('يرى دوامه اليوم وسجله الشهري يوماً بيوم', 'Sees today and the month day by day'))}</li><li>${esc(T('أرصدة الإجازات وحالة طلباته', 'Leave balances and request status'))}</li><li>${esc(T('إشعارات التأخير والغياب والرد على طلباته', 'Late, absence and request notifications'))}</li><li>${esc(T('عربي وإنجليزي ووضع ليلي، ويُثبّت كتطبيق', 'Arabic/English, dark mode, installs as an app'))}</li></ul>
          <p class="muted small">${esc(T('الأمان: كل موظف يرى بياناته فقط، وكلمة المرور الأولى تُغيَّر إجبارياً، والدخول يُقفل بعد محاولات خاطئة.', 'Security: each employee sees only their own data, the first password must be changed, and sign-in locks after wrong attempts.'))}</p>
        </div>
      </section>`;
    $$('[data-copy]', body).forEach(b => b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.copy); toast(T('نُسخ الرابط', 'Link copied'), 'ok'); } catch (e) { prompt('', b.dataset.copy); } });
    $('[data-act=all]', body).onclick = async () => { await portalAccess('enable', null); info = await GET('/api/portal/info'); paint(); };
    $('#ptSavePublic', body).onclick = () => save({ 'portal.public_url': $('#ptPublic', body).value.trim() });
    $('#ptReq input', body).onchange = (e) => save({ 'portal.requests': e.target.checked });
    $('#ptDays', body).onchange = (e) => save({ 'portal.correction_days': +e.target.value || 31 });
  };
  paint();
}
async function portalAccess(action, sel) {
  const ask = { enable: T('ستُنشأ كلمة مرور لكل موظف ليس لديه وصول بعد. متابعة؟', 'A password is created for every employee without access. Continue?'),
    reset: T('ستُلغى كلمات المرور الحالية للمحددين وتُنشأ جديدة. متابعة؟', 'Current passwords of the selected are replaced. Continue?'),
    disable: T('لن يتمكن المحددون من الدخول إلى البوابة. متابعة؟', 'The selected will no longer be able to sign in. Continue?') }[action];
  if (!await confirmBox(ask, { danger: action === 'disable' })) return;
  const r = await guard(() => POST('/api/employees/portal', sel ? { action, ids: sel.map(x => x.id) } : { action, all: true }));
  if (action === 'disable') return toast(T('أُوقفت البوابة للمحددين', 'Portal closed for the selected'), 'ok');
  if (!r.rows.length) return toast(T('كل المحددين لديهم وصول مسبقاً', 'Everyone selected already has access'), 'ok');
  const url = (await GET('/api/portal/info').catch(() => null))?.best || `${location.protocol}//${location.host}/me`;
  const body = h(`<div><div class="alert info">${esc(T('سلّم كل موظف رقمه وكلمة مروره. يدخل من جواله على الرابط التالي ويختار كلمة مرور خاصة به في أول دخول:', 'Give each employee their number and password. They sign in on their phone at this address and choose their own password the first time:'))} <b class="ltr">${esc(url)}</b></div>
    <div class="grid-wrap" style="max-height:50vh"><table class="grid"><thead><tr><th>${esc(T('الرقم', 'No.'))}</th><th>${esc(T('الاسم', 'Name'))}</th><th>${esc(T('القسم', 'Department'))}</th><th>${esc(T('كلمة المرور', 'Password'))}</th></tr></thead>
    <tbody>${r.rows.map(x => `<tr><td class="ltr">${esc(x.emp_code)}</td><td>${esc(x.name)}</td><td>${esc(x.department)}</td><td class="ltr"><b>${esc(x.password)}</b></td></tr>`).join('')}</tbody></table></div>
    <p class="muted">${esc(T('تظهر كلمات المرور هذه مرة واحدة فقط. اطبعها أو نزّلها الآن.', 'These passwords are shown only once. Print or download them now.'))}</p></div>`);
  const csv = () => { const lines = [['No', 'Name', 'Department', 'Password', 'Address'], ...r.rows.map(x => [x.emp_code, x.name, x.department, x.password, url])].map(l => l.map(v => `"${String(v).replace(/"/g, '""')}"`).join(','));
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv' })); a.download = 'portal-passwords.csv'; a.click(); };
  const print = () => { const w = window.open('', '_blank'); w.document.write(`<html dir="${document.documentElement.dir}"><head><meta charset="utf-8"><title>Portal</title><style>body{font-family:Segoe UI,Tahoma,sans-serif;margin:10px}.c{display:inline-flex;gap:12px;align-items:center;width:47%;border:1px dashed #999;border-radius:12px;padding:12px;margin:5px;vertical-align:top;page-break-inside:avoid}.c img{width:92px;height:92px;flex:none}.t{flex:1;line-height:1.7}b{font-size:18px}small{direction:ltr;display:block;word-break:break-all;color:#444}h4{margin:0 0 2px}</style></head><body>${r.rows.map(x => `<div class="c"><img src="${qrUrl(url)}" alt=""><div class="t"><h4>${esc(T('بوابة الموظف', 'Employee portal'))}</h4>${esc(x.name)}<br>${esc(T('الرقم', 'No.'))}: <b>${esc(x.emp_code)}</b><br>${esc(T('كلمة المرور', 'Password'))}: <b>${esc(x.password)}</b><small>${esc(url)}</small></div></div>`).join('')}<script>window.onload=()=>setTimeout(()=>print(),300)</scr`+`ipt></body></html>`); w.document.close(); };
  dialog({ title: T(`وصول البوابة — ${r.rows.length} موظف`, `Portal access — ${r.rows.length} employee(s)`), size: 'wide', body,
    buttons: [{ label: T('تنزيل CSV', 'Download CSV'), icon: 'download', action: () => { csv(); return false; } }, { label: T('طباعة بطاقات', 'Print cards'), icon: 'print', action: () => { print(); return false; } }, { label: T('تم', 'Done'), cls: 'primary' }] });
}
async function pageEmployees(c, _i, resigned = false) {
  title(c, resigned ? T('الموظفون المستقيلون', 'Resigned employees') : T('الموظفون', 'Employees'));
  const lk = await lookups();
  let g;
  const edit = async (row) => {
    const full = row ? await GET(`/api/employees/${row.id}`) : null;
    const flds = await employeeFields(full);
    const body = h(`<div><div class="tabs"><button class="active" data-t="info">${esc(T('البيانات', 'Details'))}</button>${full ? `<button data-t="bio">${esc(T('القياسات الحيوية', 'Biometrics'))}</button><button data-t="att">${esc(T('الحضور', 'Attendance'))}</button>` : ''}</div>
      <div data-p="info">${full ? `<div style="display:flex;gap:14px;align-items:center;margin-bottom:12px">${full.has_photo ? `<img src="/api/employees/${full.id}/photo?${Date.now()}" style="width:72px;height:72px;border-radius:8px;object-fit:cover">` : '<span class="avatar" style="width:72px;height:72px;border-radius:8px">👤</span>'}
        <label class="btn small">${icon('upload')}${esc(T('رفع صورة', 'Upload photo'))}<input type="file" accept="image/jpeg" hidden id="photoIn"></label></div>` : ''}${formHtml(flds, full || {})}</div>
      <div data-p="bio" class="hidden"></div><div data-p="att" class="hidden"></div></div>`);
    const d = dialog({ title: (full ? T('تعديل موظف', 'Edit employee') + ' — ' + full.emp_code : T('إضافة موظف', 'New employee')), size: 'wide', body,
      buttons: [{ label: T('إلغاء', 'Cancel') }, ...(can('personnel.edit') ? [{ label: T('حفظ', 'Save'), cls: 'primary', icon: 'check', action: async () => {
        const v = readForm($('[data-p=info]', body), flds);
        if (full) await PUT(`/api/employees/${full.id}`, v); else await POST('/api/employees', v);
        toast(T('تم الحفظ — ستُرسل التغييرات للأجهزة تلقائياً', 'Saved — changes are sent to the devices automatically'), 'ok'); g.reload();
      } }] : [])] });
    $$('.tabs button', body).forEach(b => b.onclick = () => {
      $$('.tabs button', body).forEach(x => x.classList.toggle('active', x === b));
      $$('[data-p]', body).forEach(p => p.classList.toggle('hidden', p.dataset.p !== b.dataset.t));
      if (b.dataset.t === 'bio') renderBio($('[data-p=bio]', body), full);
      if (b.dataset.t === 'att') renderEmpCalendar($('[data-p=att]', body), full.id, today().slice(0, 7));
    });
    const pin = $('#photoIn', body);
    if (pin) pin.onchange = async () => { const fd = new FormData(); fd.append('file', pin.files[0]); await guard(() => api('POST', `/api/employees/${full.id}/photo`, fd), T('تم رفع الصورة', 'Photo uploaded')); d.close(); g.reload(); };
  };
  g = grid(c, {
    columns: [
      { key: 'emp_code', label: T('الرقم', 'ID') },
      { key: 'name', label: T('الاسم', 'Name'), render: r => `${r.has_photo ? '📷 ' : ''}${esc(r.name)}` },
      { key: 'department', label: T('القسم', 'Department') },
      { key: 'position', label: T('الوظيفة', 'Position') },
      { key: 'areas', label: T('المناطق', 'Areas') },
      { key: 'bio', label: T('التحقق', 'Verification'), render: bioCell },
      { key: 'card_no', label: T('البطاقة', 'Card') },
      { key: 'hire_date', label: resigned ? T('تاريخ الاستقالة', 'Resign date') : T('تاريخ التعيين', 'Hire date'), render: r => esc(resigned ? r.resign_date : r.hire_date) },
      { key: 'mobile', label: T('الجوال', 'Mobile') },
    ],
    fetch: serverFetch(`/api/employees?status=${resigned ? 'resigned' : 'active'}`),
    filters: [{ key: 'department_id', label: T('كل الأقسام', 'All departments'), type: 'select', options: opts(lk.departments) },
      { key: 'area_id', label: T('كل المناطق', 'All areas'), type: 'select', options: opts(lk.areas) }],
    onRow: (r) => edit(r),
    toolbar: resigned ? [
      { label: T('إعادة للعمل', 'Reinstate'), icon: 'sync', cls: 'primary', perm: 'personnel.edit', needSel: true, action: async (sel) => { await guard(() => POST('/api/employees/batch', { ids: sel.map(r => r.id), action: 'reinstate' }), T('تمت الإعادة', 'Reinstated')); g.reload(); } },
      { label: T('حذف نهائي', 'Delete'), icon: 'del', cls: 'danger', perm: 'personnel.edit', needSel: true, action: async (sel) => { if (await confirmBox(T('حذف الموظفين نهائياً مع سجلاتهم؟', 'Permanently delete the employees and their records?'))) { await guard(() => POST('/api/employees/batch', { ids: sel.map(r => r.id), action: 'delete' }), T('تم الحذف', 'Deleted')); g.reload(); } } },
    ] : [
      { label: T('إضافة', 'Add'), icon: 'add', cls: 'primary', perm: 'personnel.edit', action: () => edit(null) },
      { label: T('تعديل', 'Edit'), icon: 'edit', perm: 'personnel.edit', needSel: true, action: (sel) => edit(sel[0]) },
      { label: T('بوابة الموظف', 'Employee portal'), icon: 'people', perm: 'personnel.edit', menu: [
        { label: T('فتح البوابة للمحددين', 'Open the portal for the selected'), icon: 'check', needSel: true, action: (sel) => portalAccess('enable', sel) },
        { label: T('فتح البوابة لكل الموظفين', 'Open the portal for everyone'), icon: 'people', action: () => portalAccess('enable', null) },
        { label: T('كلمة مرور جديدة للمحددين', 'New password for the selected'), icon: 'shield', needSel: true, action: (sel) => portalAccess('reset', sel) },
        { label: T('إيقاف البوابة للمحددين', 'Close the portal for the selected'), icon: 'close', needSel: true, action: (sel) => portalAccess('disable', sel) },
      ] },
      { label: T('إجراءات', 'Actions'), icon: 'more', perm: 'personnel.edit', menu: [
        { label: T('نقل إلى قسم', 'Change department'), icon: 'building', needSel: true, action: (sel) => batchSelect(sel, 'set_department', 'department_id', T('القسم', 'Department'), opts(lk.departments), g) },
        { label: T('تغيير المسمى الوظيفي', 'Change position'), icon: 'badge', needSel: true, action: (sel) => batchSelect(sel, 'set_position', 'position_id', T('المسمى', 'Position'), opts(lk.positions), g) },
        { label: T('تحديد المناطق', 'Set areas'), icon: 'map', needSel: true, action: (sel) => batchAreas(sel, 'set_areas', g) },
        { label: T('إضافة إلى مناطق', 'Add to areas'), icon: 'map', needSel: true, action: (sel) => batchAreas(sel, 'add_areas', g) },
        '-',
        { label: T('إرسال تسجيلات الموظفين إلى أجهزة مناطقهم', 'Send employee enrollment to area devices'), icon: 'to_device', needSel: true, action: async (sel) => { if (!await confirmDeviceOperation('batch_sync', [], T(`الموظفون المحددون: ${sel.length}`, `Selected employees: ${sel.length}`))) return; const r = await guard(() => POST('/api/employees/batch', { ids: sel.map(x => x.id), action: 'sync' })); toast(T(`أُضيف ${r.commands} أمر للأجهزة`, `${r.commands} commands queued`), 'ok'); } },
        { label: T('استقالة', 'Resign'), icon: 'exit', needSel: true, action: (sel) => resignDialog(sel, g) },
        { label: T('حذف', 'Delete'), icon: 'del', needSel: true, action: async (sel) => { if (await confirmBox(T(`حذف ${sel.length} موظف؟ سيُحذفون من الأجهزة أيضاً.`, `Delete ${sel.length} employee(s)? They are removed from the devices too.`))) { await guard(() => POST('/api/employees/batch', { ids: sel.map(r => r.id), action: 'delete' }), T('تم الحذف', 'Deleted')); g.reload(); } } },
      ] },
      { label: T('استيراد', 'Import'), icon: 'upload', perm: 'personnel.edit', action: () => importEmployees(g) },
      { label: T('تصدير', 'Export'), icon: 'download', menu: [
        { label: 'Excel', action: () => download(`/api/employees-export?fmt=xlsx`) },
        { label: 'CSV', action: () => download(`/api/employees-export?fmt=csv`) }] },
    ],
  });
}
const pageResigned = (c, i) => pageEmployees(c, i, true);
function batchSelect(sel, action, key, label, options, g) {
  const f = [{ key, label, type: 'select', options, required: true }];
  formDialog({ title: label, size: 'narrow', cls: 'one', fields: f, save: (v) => POST('/api/employees/batch', { ids: sel.map(r => r.id), action, [key]: v[key] }) }).then(() => g.reload());
}
async function batchAreas(sel, action, g) {
  const lk = await lookups();
  const f = [{ key: 'area_ids', label: T('المناطق', 'Areas'), type: 'multi', options: opts(lk.areas) }];
  await formDialog({ title: action === 'set_areas' ? T('تحديد المناطق', 'Set areas') : T('إضافة إلى مناطق', 'Add to areas'), size: 'narrow', cls: 'one', fields: f,
    save: (v) => POST('/api/employees/batch', { ids: sel.map(r => r.id), action, area_ids: v.area_ids }) });
  g.reload();
}
async function resignDialog(sel, g) {
  const f = [
    { key: 'resign_date', label: T('تاريخ الاستقالة', 'Resign date'), type: 'date', default: today(), required: true },
    { key: 'resign_type', label: T('النوع', 'Type'), type: 'select', blank: false, options: [{ value: 'resign', label: T('استقالة', 'Resignation') }, { value: 'terminate', label: T('إنهاء خدمة', 'Termination') }, { value: 'other', label: T('أخرى', 'Other') }] },
    { key: 'resign_reason', label: T('السبب', 'Reason'), type: 'textarea' },
  ];
  await formDialog({ title: T('استقالة الموظفين', 'Resign employees') + ` (${sel.length})`, fields: f, cls: 'one', size: 'narrow',
    save: (v) => POST('/api/employees/batch', { ids: sel.map(r => r.id), action: 'resign', ...v }) });
  g.reload();
}
function importEmployees(g) {
  const body = h(`<div><p>${esc(T('ملف Excel أو CSV. الأعمدة المقبولة:', 'Excel or CSV file. Accepted columns:'))}</p>
    <pre class="code">emp_code, first_name, last_name, department, position, card_no, gender, hire_date, mobile, email, national_id</pre>
    <p class="muted">${esc(T('يمكن استخدام العناوين العربية: الرقم، الاسم، القسم، الوظيفة، البطاقة. الموظف الموجود يُحدَّث، والأقسام الجديدة تُنشأ تلقائياً.', 'Existing employees are updated; new departments are created automatically.'))}</p>
    <input type="file" accept=".xlsx,.csv" class="inp"><div class="res"></div></div>`);
  dialog({ title: T('استيراد الموظفين', 'Import employees'), body, buttons: [{ label: T('إغلاق', 'Close') }, { label: T('استيراد', 'Import'), cls: 'primary', action: async () => {
    const f = $('input', body).files[0]; if (!f) throw new Error(T('اختر ملفاً', 'Choose a file'));
    const fd = new FormData(); fd.append('file', f);
    const r = await api('POST', '/api/employees/import', fd);
    $('.res', body).innerHTML = `<div class="alert info">${esc(T('جديد', 'New'))}: ${r.created} · ${esc(T('محدّث', 'Updated'))}: ${r.updated}${r.errors.length ? '<br>' + r.errors.map(esc).join('<br>') : ''}</div>`;
    g.reload(); return false;
  } }] });
}
async function renderBio(el, emp) {
  const e = await GET(`/api/employees/${emp.id}`);
  const lk = await lookups();
  el.innerHTML = `<div class="alert info">${esc(T('القوالب تُسجَّل على أي جهاز ثم تُوزَّع تلقائياً على باقي أجهزة مناطق الموظف (إذا كانت خوارزمية الجهاز متوافقة؛ وإلا تُرسل صورة الوجه ليستخرج الجهاز قالبه).', 'Templates enrolled on any terminal are distributed to the other devices of the employee\'s areas (if the algorithm matches; otherwise the face photo is sent so the device builds its own template).'))}</div>
    <table class="grid"><thead><tr><th>${esc(T('النوع', 'Type'))}</th><th>${esc(T('الرقم', 'No.'))}</th><th>${esc(T('الإصدار', 'Version'))}</th><th>${esc(T('المصدر', 'Source'))}</th><th>${esc(T('آخر تحديث', 'Updated'))}</th><th></th></tr></thead><tbody>
    ${e.templates.map(t => `<tr><td>${esc(T(...(BIO_NAMES[t.bio_type] || [t.type, t.type])))}</td><td>${t.no}</td><td class="ltr">${esc(t.version)}</td><td class="ltr">${esc(t.source)}</td><td class="ltr">${esc(t.updated_at)}</td><td>${can('personnel.edit') ? `<button class="btn small danger" data-del="${t.id}">${icon('del')}</button>` : ''}</td></tr>`).join('') || `<tr><td class="empty" colspan="6">${esc(T('لا توجد قوالب — سجّل الوجه أو البصمة على الجهاز أو استخدم التسجيل عن بعد', 'No templates — enroll on a terminal or use remote enrollment'))}</td></tr>`}
    </tbody></table>
    ${e.bio_photos.length ? `<p class="muted">${esc(T('صورة تسجيل الوجه محفوظة', 'Face enrollment photo stored'))} ✓</p>` : ''}
    ${e.bio_photos.length ? `<figure class="bio-photo"><img src="/api/employees/${emp.id}/biophoto?t=${Date.now()}" alt=""><figcaption class="muted">${esc(T('صورة الوجه من الجهاز', 'Face photo from the terminal'))}</figcaption></figure>` : ''}
    ${can('device.control') ? `<button class="btn pull-emp">${icon('from_device')}${esc(T('سحب بصماته ووجهه وكفه من الأجهزة', 'Pull this person’s biometrics from the terminals'))}</button>` : ''}
    <h4 style="margin:14px 0 6px">${esc(T('حالة المزامنة على أجهزة المنطقة', 'Sync state on the area terminals'))}</h4><div class="emp-devs muted">…</div>
    ${can('device.control') ? `<div style="margin-top:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>${esc(T('تسجيل عن بعد', 'Remote enrollment'))}:</b>
      <select class="inp dev"></select>
      <select class="inp typ"><option value="1">${esc(T('بصمة إصبع', 'Fingerprint'))}</option><option value="9">${esc(T('وجه', 'Face'))}</option><option value="8">${esc(T('كف', 'Palm'))}</option></select>
      <select class="inp fng">${FINGERS.map((f, i) => `<option value="${i}">${esc(T(...f))}</option>`).join('')}</select>
      <button class="btn primary enroll">${esc(T('ابدأ التسجيل على الجهاز', 'Start on device'))}</button></div>` : ''}`;
  $$('[data-del]', el).forEach(b => b.onclick = async () => { if (await confirmBox(T('حذف القالب من البرنامج ومن الأجهزة؟', 'Delete the template from the server and devices?'))) { await guard(() => DEL(`/api/employees/${emp.id}/templates/${b.dataset.del}`), T('تم الحذف', 'Deleted')); renderBio(el, emp); } });
  const pe = $('.pull-emp', el);
  if (pe) pe.onclick = async () => {
    const r = await guard(() => POST(`/api/employees/${emp.id}/pull-bio`));
    toast(T(`طُلبت البيانات من ${r.queued ? 'أجهزة المنطقة' : 'الأجهزة'} — تظهر هنا خلال ثوانٍ`, 'Requested from the area terminals — they appear here within seconds'), 'ok');
    setTimeout(() => renderBio(el, emp), 6000);
  };
  // per-terminal delivery state
  GET(`/api/employees/${emp.id}/devices`).then(r => {
    $('.emp-devs', el).innerHTML = r.rows.length ? `<table class="grid"><thead><tr><th>${esc(T('الجهاز', 'Terminal'))}</th><th>${esc(T('الحالة', 'State'))}</th><th>${esc(T('الاتصال', 'Link'))}</th><th>${esc(T('المزامنة', 'Sync'))}</th></tr></thead><tbody>
      ${r.rows.map(d => `<tr><td>${esc(d.alias)}</td><td>${esc(stateLabel(d.state))}</td><td class="ltr">${d.link}</td><td>${d.failed ? `<span class="badge bad" title="${esc(d.last_error)}">✗ ${d.failed}</span> ` : ''}${d.pending ? `<span class="badge warn">⇈ ${d.pending}</span>` : (d.failed ? '' : '<span class="badge ok">✓</span>')}</td></tr>`).join('')}</tbody></table>`
      : esc(T('الموظف ليس في أي منطقة لها أجهزة', 'The employee is in no area with terminals'));
  }).catch(() => {});
  const en = $('.enroll', el);
  if (!en) return;
  const devs = (await GET('/api/devices')).rows.filter(d => d.managed_by !== 'tcp');
  const dsel = $('.dev', el), tsel = $('.typ', el), fsel = $('.fng', el);
  dsel.innerHTML = devs.length ? devs.map(d => `<option value="${d.id}" ${d.state !== 'online' ? 'disabled' : ''}>${esc(d.label || d.alias || d.sn)}${d.state !== 'online' ? ' — ' + esc(stateLabel(d.state)) : ''}</option>`).join('')
    : `<option value="">${esc(T('لا توجد أجهزة متصلة بالمنفذ 90', 'No terminal pushing to port 90'))}</option>`;
  const fit = () => {
    const d = devs.find(x => String(x.id) === dsel.value); const sup = d ? Object.keys(d.bio_support).map(Number) : [];
    [...tsel.options].forEach(o => { o.disabled = sup.length > 0 && !sup.includes(+o.value) && !(+o.value === 9 && sup.includes(2)); });
    if (tsel.selectedOptions[0]?.disabled) { const ok = [...tsel.options].find(o => !o.disabled); if (ok) tsel.value = ok.value; }
    fsel.classList.toggle('hidden', tsel.value !== '1');
  };
  dsel.onchange = fit; tsel.onchange = fit; fit();
  en.onclick = async () => {
    if (!dsel.value) return toast(T('لا يوجد جهاز', 'No device'), 'bad');
    const r = await guard(() => POST(`/api/employees/${emp.id}/enroll`, { device_id: +dsel.value, bio_type: +tsel.value, finger: +fsel.value }));
    enrollProgress(r, emp, () => renderBio(el, emp));
  };
}
const FINGERS = [['إبهام اليد اليسرى', 'Left thumb'], ['سبابة اليسرى', 'Left index'], ['وسطى اليسرى', 'Left middle'], ['بنصر اليسرى', 'Left ring'], ['خنصر اليسرى', 'Left little'],
  ['إبهام اليد اليمنى', 'Right thumb'], ['سبابة اليمنى', 'Right index'], ['وسطى اليمنى', 'Right middle'], ['بنصر اليمنى', 'Right ring'], ['خنصر اليمنى', 'Right little']];
function enrollProgress(r, emp, done) {
  const steps = [T('أُرسل الأمر إلى الجهاز', 'Command queued'), T('الجهاز استلم الأمر — اطلب من الموظف الوقوف أمام الجهاز', 'Terminal is asking the employee'), T('وصل القالب إلى البرنامج', 'Template received'), T('أُرسل إلى باقي أجهزة المنطقة', 'Sent to the other area terminals')];
  const body = h(`<div><p>${esc(emp.emp_code)} — ${esc(r.device)}</p><ol class="enroll-steps">${steps.map(x => `<li class="muted">${esc(x)}</li>`).join('')}</ol><p class="enroll-msg muted"></p></div>`);
  let stop = false;
  dialog({ title: T('التسجيل عن بعد', 'Remote enrollment'), size: 'narrow', body, buttons: [{ label: T('إغلاق', 'Close') }], onClose: () => { stop = true; done(); } });
  const mark = (n) => $$('li', body).forEach((li, i) => { li.classList.toggle('muted', i >= n); li.innerHTML = (i < n ? '✓ ' : '') + esc(steps[i]); });
  const t0 = Date.now();
  const tick = async () => {
    if (stop) return;
    let st; try { st = await GET(`/api/enroll/${r.cmd_id}`); } catch (e) { return setTimeout(tick, 3000); }
    let n = 1;
    if (st.status === 'sent' || st.status === 'done') n = 2;
    if (st.received) n = 3;
    if (st.received && st.delivered) n = 4;
    mark(n);
    const msg = $('.enroll-msg', body);
    if (st.status === 'failed') { msg.textContent = `✗ ${T('رفض الجهاز الأمر', 'The terminal refused')} (${st.return_code}) ${st.result || ''}`; return; }
    if (n === 4 || (n === 3 && !st.distributed)) { msg.textContent = '✓ ' + T('اكتمل التسجيل', 'Enrollment complete'); return; }
    if (Date.now() - t0 > 4 * 60000) { msg.textContent = T('انتهت المهلة — لم يُسجَّل شيء بعد. تأكد أن الموظف أمام الجهاز وأعد المحاولة.', 'Timed out — nothing enrolled yet. Try again with the employee at the terminal.'); return; }
    setTimeout(tick, 2000);
  };
  tick();
}

function simplePage(c, heading, endpoint, perm, columns, fields, extra = {}) {
  title(c, heading);
  return crudPage(c, { endpoint, columns, fields, title: heading, perm, ...extra });
}
async function pageDepartments(c) {
  const lk = await lookups();
  simplePage(c, T('الأقسام', 'Departments'), '/api/departments', 'personnel.edit',
    [{ key: 'code', label: T('الرمز', 'Code') }, { key: 'name', label: T('الاسم', 'Name'), render: r => esc(nm(r)) },
      { key: 'parent_id', label: T('القسم الرئيسي', 'Parent'), render: r => esc(lk.departments.find(d => d.id === r.parent_id)?.name || '') },
      { key: 'employees', label: T('عدد الموظفين', 'Employees'), cls: 'num' }],
    async () => { const l = await lookups(true); return [{ key: 'code', label: T('الرمز', 'Code'), required: true }, { key: 'name', label: T('الاسم (عربي)', 'Name (Arabic)'), required: true }, NAME_EN, { key: 'parent_id', label: T('القسم الرئيسي', 'Parent'), type: 'select', options: opts(l.departments) }]; });
}
function pagePositions(c) {
  simplePage(c, T('المسميات الوظيفية', 'Positions'), '/api/positions', 'personnel.edit',
    [{ key: 'code', label: T('الرمز', 'Code') }, { key: 'name', label: T('الاسم', 'Name'), render: r => esc(nm(r)) }],
    [{ key: 'code', label: T('الرمز', 'Code'), required: true }, { key: 'name', label: T('الاسم (عربي)', 'Name (Arabic)'), required: true }, NAME_EN]);
}
function pageAreas(c) {
  c.appendChild(h(`<div class="alert info">${esc(T('المنطقة = مجموعة أجهزة. كل موظف في منطقة يُرسل تلقائياً (بياناته وبصماته ووجهه) إلى جميع أجهزة تلك المنطقة، تماماً.', 'An area is a group of devices. Every employee of an area is automatically sent (with templates) to all of its devices.'))}</div>`));
  simplePage(c, T('المناطق', 'Areas'), '/api/areas', 'personnel.edit',
    [{ key: 'code', label: T('الرمز', 'Code') }, { key: 'name', label: T('الاسم', 'Name'), render: r => esc(nm(r)) }, { key: 'devices', label: T('الأجهزة', 'Devices'), cls: 'num' }, { key: 'employees', label: T('الموظفون', 'Employees'), cls: 'num' }],
    [{ key: 'code', label: T('الرمز', 'Code'), required: true }, { key: 'name', label: T('الاسم', 'Name'), required: true }]);
}

// ============================================================== devices
function stateIcon(r) {
  if (r.managed_by === 'tcp') return `${stateIconCore(r)} <span class="badge info" title="${esc(T('اتصال مباشر عبر المنفذ 4370؛ الكتابة تتطلب إذن الكتابة من الإعدادات', 'Direct link over port 4370; writing requires permission in settings'))}">4370</span>`;
  return stateIconCore(r);
}
function stateIconCore(r) {
  if (r.state === 'online' && r.transferring) return `<span class="st-icon xfer to" title="${esc(T('البرنامج يرسل بيانات إلى الجهاز', 'Sending data to the terminal'))}">${icon('to_device')}</span>`;
  if (r.state === 'online' && r.receiving) return `<span class="st-icon xfer from" title="${esc(T('الجهاز يرسل بيانات إلى البرنامج', 'The terminal is sending data'))}">${icon('from_device')}</span>`;
  if (r.state === 'online') return `<span class="st-icon online" title="${esc(T('متصل', 'Online'))}">✓</span>`;
  return `<span class="st-icon ${r.state}" title="${esc(stateLabel(r.state))}"></span>`;
}
async function pageDevices(c) {
  title(c, T('الجهاز', 'Device'));
  const [s, first] = await Promise.all([GET('/api/settings'), GET('/api/devices')]);
  if (!first.rows.length) {
    c.appendChild(h(`<div class="alert">${esc(T('لا توجد أجهزة بعد. اضغط «بحث عن الأجهزة»: يجد البرنامج كل أجهزة البصمة في الشبكة ويضيفها ويقرأ مستخدميها وبصماتها ووجوهها وحركاتها مباشرة — بدون أي برنامج آخر.', 'No devices yet. Press “Search devices”: the program finds every terminal on the network, adds it and reads its users, fingerprints, faces and punches directly — no other software needed.'))}</div>`));
    const ports = (s._server.adms_ports || []).join(' / ');
    c.appendChild(h(`<div class="alert info">${esc(T('لربط جهاز (مثل SpeedFace-V5L): من قائمة الجهاز ← الاتصال ← إعدادات الخادم السحابي (Cloud Server / ADMS): فعّل ADMS، ضع عنوان IP لهذا الحاسوب، والمنفذ', 'To connect a terminal (e.g. SpeedFace-V5L): device menu → Comm. → Cloud Server Setting (ADMS): enable it, enter this PC\'s IP address and port'))} <b class="ltr">${esc(ports)}</b>${esc(T('، وألغِ تفعيل HTTPS والـ Proxy. سيظهر الجهاز هنا تلقائياً خلال ثوانٍ.', ', with HTTPS and proxy off. The device appears here automatically within seconds.'))}</div>`));
  }
  const lk = await lookups(true);
  const mode = await GET('/api/link-mode');
  const writeEnabled = !!s['tcp.write_back'];
  const RO = !writeEnabled ? T('إذن الكتابة متوقف في إعدادات النظام', 'Device writing is disabled in system settings') : mode.writing ? false : T('وضع القراءة فقط: منفذ الأجهزة غير متاح للبرنامج', 'Read-only mode: the device port is unavailable');
  const banner = h(`<div class="alert ${mode.full ? 'info' : ''}"></div>`);
  banner.innerHTML = mode.full
    ? `✓ <b>${esc(T('استقبال الأجهزة متاح', 'Device reception available'))}</b>: ${esc(T(`البرنامج يستقبل الأجهزة على المنفذ ${mode.port}.`, `This program receives terminals on port ${mode.port}.`))} <b>${esc(writeEnabled ? T('إذن الكتابة مفعّل', 'Writing is enabled') : T('القراءة فقط — إذن الكتابة متوقف', 'Read-only — writing is disabled'))}</b>. ${can('system.admin') ? `<button class="btn link-release">${esc(T(`تحرير المنفذ ${mode.port} (لتشغيل البرنامج القديم)`, `Release port ${mode.port} (for the previous program)`))}</button>` : ''}`
    : `👁 <b>${esc(T('وضع القراءة فقط', 'Read-only mode'))}</b>: ${esc(mode.mode === 'released' ? T(`حُرّر المنفذ ${mode.port} يدوياً.`, `Port ${mode.port} was released by hand.`) : T(`المنفذ ${mode.port} مشغول ببرنامج آخر.`, `Port ${mode.port} is held by another program.`))} ${esc(T('الأجهزة تُقرأ عبر 4370. الكتابة تتطلب توفر المنفذ وتفعيل إذن الكتابة في الإعدادات.', 'Terminals are read over 4370. Writing requires the port to be available and writing to be enabled in settings.'))} ${mode.mode === 'released' && can('system.admin') ? `<button class="btn link-take">${esc(T(`استخدام المنفذ ${mode.port} عند توفره`, `Use port ${mode.port} when free`))}</button>` : ''}`;
  c.appendChild(banner);
  const directionGuide = h(`<details class="panel direction-guide"><summary>${esc(T('ما اتجاه انتقال البيانات؟', 'How does data move?'))}</summary>${deviceDirectionGuide()}</details>`); c.appendChild(directionGuide);
  const rel = $('.link-release', banner), tk = $('.link-take', banner);
  if (rel) rel.onclick = async () => { if (await confirmBox(T('سيتوقف البرنامج عن استقبال الأجهزة على المنفذ 90 ليتمكن البرنامج القديم من العمل عليه. متابعة؟', 'The program stops serving port 90 so البرنامج القديم can use it. Continue?'))) { await guard(() => POST('/api/link-mode', { action: 'release' })); setTimeout(() => location.reload(), 1500); } };
  if (tk) tk.onclick = async () => { await guard(() => POST('/api/link-mode', { action: 'take' })); toast(T('سيأخذ البرنامج المنفذ فور تحرره', 'The program takes the port as soon as it is free'), 'ok'); };
  App.timers.push(setInterval(async () => { if (document.visibilityState !== 'visible') return; try { const m2 = await GET('/api/link-mode'); if (m2.full !== mode.full || m2.mode !== mode.mode) location.reload(); } catch (e) { /* ignore */ } }, 10000));
  const act = async (sel, action, confirmMsg, extra = {}) => {
    if (!await confirmDeviceOperation(action, sel, confirmMsg)) return;
    let n = 0, done = 0, failed = 0;
    for (const d of sel) { const r = await guard(() => POST(`/api/devices/${d.id}/action`, { action, ...extra })); n += r.queued; if (r.delivered) { done += r.delivered.done; failed += r.delivered.failed; } }
    toast(done || failed ? T(`نُفّذ ${done} أمر مباشرة${failed ? ` · فشل ${failed} (راجع سجل الأوامر)` : ''}`, `${done} command(s) executed directly${failed ? ` · ${failed} failed (see commands)` : ''}`)
      : T(`أُضيف ${n} أمر — يُنفذ عند اتصال الجهاز التالي`, `${n} command(s) queued — executed at the next heartbeat`), failed ? 'bad' : 'ok'); g.reload();
  };
  const num = (k, label) => ({ key: k, label, cls: 'num' });
  // Device list columns
  const g = grid(c, {
    columns: [
      { key: 'alias', label: T('إسم الجهاز', 'Device name'), render: r => `<a href="#" data-act="detail">${esc(r.label || r.alias || r.sn)}</a>` },
      { key: 'sn', label: T('الرقم التسلسلي', 'Serial number'), cls: 'ltr' },
      { key: 'area', label: T('المنطقة', 'Area') },
      { key: 'ip', label: T('IP عنوان الجهاز', 'Device IP'), cls: 'ltr' },
      { key: 'state', label: T('الحالة', 'State'), cls: 'num', render: stateIcon },
      { key: 'last_activity', label: T('النشاط الأخير', 'Last activity'), cls: 'ltr' },
      num('user_count', T('المستخدم', 'User')),
      num('fp_count', T('بصمة الإصبع', 'Fingerprint')),
      num('face_count', T('الوجه', 'Face')),
      num('palm_count', T('كف اليد', 'Palm')),
      num('att_count', T('سجلات الحضور', 'Transactions')),
      { key: 'last_sync', label: T('آخر إرسال تسجيلات إلى الجهاز', 'Last enrollment send to device'), cls: 'ltr' },
      { key: 'pending', label: T('أوامر منتظرة', 'Pending cmds'), cls: 'num', render: r => r.pending ? `<span class="badge warn">${r.pending}</span>` : '0' },
      { key: 'model', label: T('الطراز', 'Model') },
      { key: 'firmware', label: T('إصدار البرنامج', 'Firmware'), cls: 'ltr' },
    ],
    fetch: localFetch('/api/devices'),
    actions: { detail: (r) => deviceDetail(r, g) },
    onRow: (r) => deviceDetail(r, g),
    toolbar: [
      { label: T('الدخول إلى الجهاز', 'Open terminal'), icon: 'device', cls: 'primary', perm: 'device.view', needSel: true, action: (sel) => devicePanel(sel[0], g) },
      { label: T('بحث عن الأجهزة', 'Search devices'), icon: 'search', perm: 'device.control', action: () => discoverDialog(g) },
      { label: T('إضافة بعنوان IP', 'Add by IP'), icon: 'add', perm: 'device.control', action: () => probeDialog(g) },
      { label: T('إضافة', 'Add'), icon: 'add', perm: 'device.control', action: () => deviceForm(null, lk, g) },
      { label: T('تعديل', 'Edit'), icon: 'edit', perm: 'device.control', needSel: true, action: (sel) => deviceForm(sel[0], lk, g) },
      { label: T('حذف', 'Delete'), icon: 'del', perm: 'device.control', needSel: true, action: async (sel) => { if (await confirmBox(T('حذف الجهاز من البرنامج؟ (لا يُمسح شيء من الجهاز نفسه)', 'Remove the device from the server? (nothing is erased on the device)'))) { for (const d of sel) await DEL(`/api/devices/${d.id}`); g.reload(); } } },
      { label: T('منطقة جديدة', 'New area'), icon: 'map', perm: 'device.control', needSel: true, action: (sel) => setDeviceArea(sel, lk, g) },
      { label: T('حذف الأوامر غير المنفذة', 'Clear pending commands'), icon: 'close', perm: 'device.control', needSel: true, action: async (sel) => {
        if (!await confirmBox(T('حذف كل الأوامر التي لم ينفذها الجهاز بعد؟', 'Delete all commands the device has not executed yet?'))) return;
        let n = 0; for (const d of sel) n += (await guard(() => POST('/api/device-commands/clear', { sn: d.sn, status: ['pending', 'sent'] }))).deleted;
        toast(T(`حُذف ${n} أمر`, `${n} command(s) deleted`), 'ok'); g.reload();
      } },
      { label: T('حذف البيانات', 'Clear data'), icon: 'del', perm: 'device.control', menu: [
        { label: T('حذف سجلات الحضور من الجهاز', 'Clear attendance records'), icon: 'del', needSel: true, disabled: RO, action: (sel) => act(sel, 'clear_log', T('سيتم حذف سجلات الحضور من ذاكرة الجهاز (تبقى محفوظة في البرنامج). متابعة؟', 'Attendance records are erased from the device memory (they stay on the server). Continue?')) },
        { label: T('حذف صور الحضور', 'Clear attendance photos'), icon: 'del', needSel: true, disabled: RO, action: (sel) => act(sel, 'clear_photo', T('متابعة؟', 'Continue?')) },
        { label: T('حذف كل بيانات الجهاز', 'Clear all device data'), icon: 'del', needSel: true, disabled: RO, action: (sel) => act(sel, 'clear_data', T('سيُمسح كل المستخدمين والبصمات والسجلات من الجهاز! هل أنت متأكد؟', 'ALL users, templates and records will be erased from the device! Are you sure?')) },
      ] },
      { label: T('قراءة وإرسال البيانات', 'Read and send data'), icon: 'two_way', perm: 'device.control', menu: [
        { label: T('إرسال بيانات التسجيل من الخادم إلى الجهاز', 'Send enrollment data: server → device'), icon: 'to_device', needSel: true, disabled: RO, action: (sel) => act(sel, 'sync_all') },
        { label: T('قراءة التسجيلات الحيوية من الجهاز إلى الخادم', 'Read biometric enrollment: device → server'), icon: 'from_device', needSel: true, action: (sel) => pullBio(sel[0], g) },
        { label: T('قراءة بيانات الموظفين من كل الأجهزة إلى الخادم', 'Read employees: all devices → server'), icon: 'from_device', action: () => pullEveryone(g) },
        { label: T('طلب المستخدمين والقوالب من الجهاز إلى الخادم', 'Request users and templates: device → server'), icon: 'from_device', needSel: true, disabled: RO, action: (sel) => act(sel, 'upload_users') },
        { label: T('طلب حركات فترة من الجهاز إلى الخادم', 'Request period movements: device → server'), icon: 'from_device', needSel: true, disabled: RO, action: (sel) => uploadAtt(sel, act) },
        { label: T('طلب إعادة إرسال السجلات من الجهاز إلى الخادم', 'Request records again: device → server'), icon: 'from_device', needSel: true, disabled: RO, action: (sel) => act(sel, 'reupload_all', T('سيعيد الجهاز إرسال كل السجلات (المكرر يُتجاهل تلقائياً).', 'The device will resend records; duplicates are ignored.')) },
        '-',
        { label: T('قراءة مباشرة من الجهاز إلى الخادم (4370)', 'Direct read 4370: device → server'), icon: 'from_device', needSel: true, action: async (sel) => { if (!await confirmDeviceOperation('direct_read', sel)) return; for (const d of sel) { toast(`${d.alias}: ${T('جارٍ القراءة...', 'reading...')}`); const r = await guard(() => POST(`/api/devices/${d.id}/pull`)); toast(`${d.alias}: ${T('مستخدمون', 'users')} ${r.users} · ${T('قوالب', 'templates')} ${r.templates} · ${T('حركات جديدة', 'new punches')} ${r.new}`, 'ok'); } g.reload(); } },
      ] },
      { label: T('قائمة الجهاز', 'Device menu'), icon: 'system', perm: 'device.control', menu: [
        { label: T('إعادة تشغيل', 'Reboot'), icon: 'sync', needSel: true, disabled: RO, action: (sel) => act(sel, 'reboot', T('إعادة تشغيل الأجهزة المحددة؟', 'Reboot the selected devices?')) },
        { label: T('إرسال وقت الخادم إلى الجهاز', 'Set device to server time'), icon: 'clock', needSel: true, disabled: RO, action: (sel) => act(sel, 'sync_time') },
        { label: T('قراءة معلومات الجهاز', 'Get device info'), icon: 'device', needSel: true, disabled: RO, action: (sel) => act(sel, 'info') },
        { label: T('إعادة تحميل الإعدادات', 'Reload options'), icon: 'refresh', needSel: true, disabled: RO, action: (sel) => act(sel, 'check') },
        { label: T('توجيه الجهاز إلى هذا الخادم (ADMS)', 'Point the terminal to this server (ADMS)'), icon: 'device', needSel: true, disabled: RO, action: (sel) => pointToServer(sel[0], g) },
        '-',
        { label: T('إرسال أمر مخصص', 'Send custom command'), icon: 'terminal', needSel: true, disabled: RO, action: (sel) => customCmd(sel, act) },
      ] },
    ],
  });
  Live.on(['device', 'commands', 'punch'], () => g.reload(), 1500);
  App.timers.push(setInterval(() => document.visibilityState === 'visible' && g.reload(), 60000));
}
async function discoverDialog(g) {
  const st = await GET('/api/device-discovery');
  const f = [{ key: 'networks', label: T('الشبكات (فارغ = شبكة هذا الحاسوب)', 'Networks (empty = this PC\'s network)'), placeholder: st.default_networks.join(', ') || '10.0.0.0/24', full: true,
    hint: T('مثال: 10.0.0.0/24 أو 10.28.65.200-10.28.65.254. مفاتيح الاتصال المجربة من الإعدادات.', 'e.g. 10.0.0.0/24 or 10.28.65.200-10.28.65.254. Comm keys tried come from the settings.') }];
  formDialog({ title: T('بحث عن الأجهزة في الشبكة', 'Search the network for terminals'), size: 'narrow', cls: 'one', fields: f, data: { networks: '' },
    save: async (v) => {
      toast(T('جارٍ البحث... (ثوانٍ قليلة لكل شبكة)', 'Searching... (a few seconds per network)'));
      const r = await POST('/api/devices/discover', v.networks ? { networks: v.networks } : {});
      const ok = r.found.filter(x => x.ok), bad = r.found.filter(x => !x.ok);
      toast(T(`وُجد ${r.found.length} جهاز (${ok.filter(x => x.new).length} جديد) في ${r.networks}`, `${r.found.length} terminal(s) found (${ok.filter(x => x.new).length} new) on ${r.networks}`), 'ok');
      if (bad.length) toast(bad.map(x => `${x.ip}: ${x.error}`).join(' · '), 'bad');
    } }).then(() => { App.lookups = null; g.reload(); });
}
function probeDialog(g) {
  const f = [{ key: 'ip', label: T('عنوان IP للجهاز', 'Terminal IP address'), required: true, placeholder: '10.0.0.20' },
    { key: 'comm_key', label: T('مفتاح الاتصال (Comm Key)', 'Comm key'), default: '0' },
    { key: 'port', label: T('المنفذ', 'Port'), type: 'number', default: 4370 }];
  formDialog({ title: T('إضافة جهاز بعنوان IP', 'Add a terminal by IP'), size: 'narrow', fields: f,
    save: async (v) => { const r = await POST('/api/devices/probe', v); toast(`${r.sn} ${r.info.DeviceName || ''}: ${T('مستخدمون', 'users')} ${r.read.users} · ${T('قوالب', 'templates')} ${r.read.templates} · ${T('حركات', 'punches')} ${r.read.new}`, 'ok'); } })
    .then(() => { App.lookups = null; g.reload(); });
}
async function pointToServer(d, g) {
  if (!d.ip) return toast(T('اكتب عنوان IP للجهاز أولاً (تعديل)', 'Set the terminal IP first (Edit)'), 'bad');
  const cur = await guard(() => GET(`/api/devices/${d.id}/server`));
  const now = Object.entries(cur.current).map(([k, v]) => `${k}=${v}`).join(' · ') || '—';
  const f = [{ key: 'ip', label: T('عنوان هذا الخادم', 'This server address'), required: true },
    { key: 'port', label: T('منفذ ADMS', 'ADMS port'), type: 'number', required: true },
    { key: 'reboot', label: T('إعادة التشغيل', 'Reboot'), type: 'checkbox', text: T('إعادة تشغيل الجهاز ليتصل فوراً', 'Reboot so it connects right away'), default: true,
      hint: T('الإعداد الحالي في الجهاز: ', 'Current terminal setting: ') + now }];
  formDialog({ title: T('توجيه الجهاز إلى هذا البرنامج (ADMS)', 'Point the terminal to this program (ADMS)'), size: 'narrow', fields: f,
    data: { ip: cur.suggested_ip, port: cur.suggested_port, reboot: true },
    save: async (v) => { const r = await POST(`/api/devices/${d.id}/server`, v); toast(T(`الجهاز يرسل الآن إلى ${r.server} — الوجوه المرئية والكف والصور تُنقل عبر ADMS`, `The terminal now pushes to ${r.server} — visible-light faces, palms and photos travel over ADMS`), 'ok'); } })
    .then(() => g.reload());
}
// Body and screen sizes (mm) of common terminal families, so the panel has the terminal's own
// proportions. layout: tall = camera on top, touch screen, sensor below; keys = screen + keypad.
const DEVICE_SHAPES = [
  { re: /proface ?x|pfx/i, body: [132, 262], screen: [108, 172], cams: 2, layout: 'tall', inch: 8 },
  { re: /v5l|v5 l|speedface.?v5/i, body: [92, 220], screen: [70, 124], cams: 2, layout: 'tall', inch: 5 },
  { re: /v4l|speedface.?v4|speedface.?h5l/i, body: [86, 190], screen: [60, 106], cams: 2, layout: 'tall', inch: 4 },
  { re: /v3l|uface|mb\d|iface|k\d0|x\d{3}|u\d{3}|ua\d/i, body: [180, 168], screen: [62, 46], cams: 1, layout: 'keys', inch: 2.8 },
];
function deviceShape(model, hasFp) {
  const s = DEVICE_SHAPES.find(x => x.re.test(model || '')) || { body: [92, 220], screen: [70, 124], cams: 2, layout: 'tall', inch: 5 };
  return { ...s, fp: hasFp };
}
async function devicePanel(d, g) {
  const L = (x) => App.lang === 'ar' ? x.ar : x.en;
  let P = await GET(`/api/devices/${d.id}/panel?live=false`).catch(() => null);
  const model = (P && P.model) || d.model || '';
  const shape = deviceShape(model, !!((P && P.fps) || d.fp_count));
  // scale the real proportions to the screen
  const H = Math.min(window.innerHeight * (window.innerWidth < 900 ? .72 : .84), 820);
  const k = H / shape.body[1], W = Math.min(shape.body[0] * k, window.innerWidth - 40), kk = W / shape.body[0];
  const sw = shape.screen[0] * kk, sh = shape.screen[1] * kk;
  const body = h(`<div class="dp"><div class="dp-bezel ${shape.layout}" style="width:${W}px;height:${shape.body[1] * kk}px">
      <div class="dp-cams">${'<i class="lens"></i>'.repeat(shape.cams)}<i class="led"></i></div>
      <div class="dp-screen" style="width:${sw}px;height:${sh}px">
        <div class="dp-status"><span class="dp-link"></span><span class="dp-name"></span><span class="dp-clock ltr"></span></div>
        <div class="dp-view"></div>
      </div>
      ${shape.layout === 'keys' ? `<div class="dp-keys">${['1', '2', '3', 'M', '4', '5', '6', '▲', '7', '8', '9', '▼', 'ESC', '0', '⌫', 'OK'].map(x => `<button data-key="${x}">${x}</button>`).join('')}</div>` : ''}
      <div class="dp-bottom">${shape.fp ? '<span class="dp-fp" title="sensor"></span>' : ''}<span class="dp-brand">${esc(model || '')}</span></div>
    </div></div>`);
  const dlg = dialog({ title: `${T('الدخول إلى الجهاز', 'Open terminal')} — ${d.label || d.alias || d.sn}`, size: 'device', body });
  $('.dialog', dlg.el).style.width = `${Math.max(W + 48, 340)}px`;
  const view = $('.dp-view', body);
  let where = 'idle', offset = 0, refreshing = true;   // offset: terminal clock minus PC clock (ms)
  const devNow = () => new Date(Date.now() + offset);
  const two = (n) => String(n).padStart(2, '0');
  const fmt = (t) => `${t.getFullYear()}-${two(t.getMonth() + 1)}-${two(t.getDate())} ${two(t.getHours())}:${two(t.getMinutes())}:${two(t.getSeconds())}`;
  const tick = setInterval(() => {
    if (!document.body.contains(body)) return clearInterval(tick);
    const t = devNow();
    $('.dp-clock', body).textContent = `${two(t.getHours())}:${two(t.getMinutes())}`;
    const big = $('.dp-big', view); if (big) big.textContent = `${two(t.getHours())}:${two(t.getMinutes())}`;
    const sec = $('.dp-sec', view); if (sec) sec.textContent = two(t.getSeconds());
    const live = $('.dp-devtime', view); if (live) live.textContent = fmt(t);
  }, 500);
  const field = (key) => { for (const s of P.sections) for (const f of s.fields) if (f.key === key) return f; return null; };
  const status = () => {
    $('.dp-name', body).textContent = P.alias || P.sn;
    const st = refreshing ? ['wait', T('جارٍ القراءة…', 'Reading…')] : P.live ? ['on', T('مباشر', 'Live')] : P.busy ? ['busy', T('مشغول — قيم محفوظة', 'Busy — saved values')] : ['off', T('آخر ما أبلغ به', 'Last reported')];
    $('.dp-link', body).innerHTML = `<i class="${st[0]}"></i>${esc(st[1])}`;
    const dt = field('DeviceTime');
    if (dt && dt.value && P.live) offset = new Date(String(dt.value).replace(' ', 'T')) - Date.now();
  };
  const screen = (titleText, inner, back = menu) => {
    view.innerHTML = `<div class="dp-bar"><button class="dp-back" aria-label="back">‹</button><b>${esc(titleText)}</b></div><div class="dp-list">${inner}</div>`;
    $('.dp-back', view).onclick = back;
  };
  // ---------- idle (the terminal's standby screen)
  const idle = () => {
    where = 'idle';
    const t = devNow();
    const days = App.lang === 'ar' ? ['الأحد', 'الإثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'] : ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    const cnt = [[P.users, T('مستخدم', 'users')], [P.faces, T('وجه', 'faces')], [P.fps, T('بصمة', 'fingers')], [P.palms, T('كف', 'palms')], [P.records, T('سجل', 'records')]].filter(x => x[0]);
    view.innerHTML = `<div class="dp-idle">
        <div class="dp-time ltr"><span class="dp-big">${two(t.getHours())}:${two(t.getMinutes())}</span><span class="dp-sec">${two(t.getSeconds())}</span></div>
        <div class="dp-date">${esc(days[t.getDay()])} · <bdi>${t.getFullYear()}-${two(t.getMonth() + 1)}-${two(t.getDate())}</bdi></div>
        <div class="dp-face"><span></span></div>
        <div class="dp-hint">${esc(T('ضع وجهك أو كفك أمام الجهاز', 'Show your face or palm'))}</div>
        <div class="dp-counts">${cnt.map(c => `<span><b>${c[0]}</b>${esc(c[1])}</span>`).join('')}</div>
        <button class="dp-menu-btn">☰ ${esc(T('القائمة', 'Menu'))}</button></div>`;
    $('.dp-menu-btn', view).onclick = menu;
  };
  // ---------- main menu
  const apps = () => [
    { id: 'users', icon: '👥', ar: 'المستخدمون', en: 'Users' },
    ...P.sections.filter(s => s.fields.length && s.id !== 'info'),
    { id: 'data', icon: '🗂', ar: 'إدارة البيانات', en: 'Data' },
    ...(Object.keys(P.advanced).length ? [{ id: 'adv', icon: '⚙', ar: 'خيارات متقدمة', en: 'Advanced' }] : []),
    { id: 'any', icon: '🔎', ar: 'أي إعداد بالاسم', en: 'Any setting' },
    { id: 'info', icon: 'ℹ', ar: 'حول الجهاز', en: 'About' },
  ];
  const menu = () => {
    where = 'menu';
    view.innerHTML = `<div class="dp-grid">${apps().map(a => `<button class="dp-app" data-s="${a.id}"><span class="dp-ic">${a.icon}</span>${esc(L(a))}</button>`).join('')}</div>
      ${P.writable ? '' : `<p class="dp-note">${esc(T('وضع القراءة فقط: يمكنك الاطلاع، والتعديل يُفعَّل عندما يملك البرنامج المنفذ 90.', 'Read-only: you can look; changes are enabled when the program owns port 90.'))}</p>`}
      ${!P.live && !refreshing ? `<p class="dp-note">${esc(P.busy ? T('الجهاز مشغول بقراءة الحركات الآن؛ القيم المعروضة آخر ما أرسله. اضغط ⟳ بعد قليل.', 'The terminal is busy being read; these are its last reported values. Tap ⟳ in a moment.') : P.has_ip ? T('لم يرد الجهاز على المنفذ 4370؛ القيم المعروضة آخر ما أرسله، والتعديلات تُرسل عند اتصاله التالي.', 'No answer on port 4370; values are as last reported and changes go out at its next connection.') : T('لا يُعرف عنوان IP للجهاز؛ القيم آخر ما أرسله.', 'No IP address known; values are as last reported.'))}</p>` : ''}
      <button class="dp-refresh">⟳ ${esc(T('تحديث من الجهاز', 'Refresh from terminal'))}</button>`;
    $$('.dp-app', view).forEach(b => b.onclick = () => open(b.dataset.s));
    $('.dp-refresh', view).onclick = () => refresh(true);
  };
  const open = (id) => {
    where = id;
    if (id === 'users') return usersView();
    if (id === 'data') return dataView();
    if (id === 'any') return anyView();
    if (id === 'time') return timeView();
    return section(id);
  };
  const valueText = (f) => f.kind === 'bool' ? (String(f.value) === '1' ? T('مفعّل', 'On') : T('معطّل', 'Off')) : (f.value ?? '');
  const section = (id) => {
    const sec = id === 'adv' ? { ar: 'خيارات متقدمة', en: 'Advanced', fields: Object.entries(P.advanced).map(([k, v]) => ({ key: k, ar: k, en: k, kind: 'text', value: v, raw: true })) } : P.sections.find(x => x.id === id);
    if (!sec) return menu();
    screen(L(sec), sec.fields.map((f, i) => `<button class="dp-row ${f.kind === 'ro' ? 'ro' : ''}" data-i="${i}"><span>${esc(f.raw ? f.key : L(f))}${f.danger ? ' ⚠' : ''}</span><b class="ltr">${esc(valueText(f))}</b>${f.kind === 'ro' ? '' : '<em>›</em>'}</button>`).join('')
      || `<p class="dp-note">${esc(T('لا توجد قيم.', 'Nothing here.'))}</p>`);
    $$('.dp-row', view).forEach(r => r.onclick = () => { const f = sec.fields[+r.dataset.i]; if (f.kind !== 'ro') edit(f, () => section(id)); });
  };
  const edit = (f, back) => {
    where = 'edit';
    const label = f.raw ? f.key : L(f);
    const input = f.kind === 'bool' ? `<div class="dp-switch"><button data-v="1" class="${String(f.value) === '1' ? 'on' : ''}">${esc(T('مفعّل', 'On'))}</button><button data-v="0" class="${String(f.value) !== '1' ? 'on' : ''}">${esc(T('معطّل', 'Off'))}</button></div>`
      : f.kind === 'number' && /threshold|حد|Thr|level|VOLUME|Brightness/i.test(f.key + label) ? `<input class="dp-range" type="range" min="0" max="${/VOLUME|Brightness/i.test(f.key) ? 100 : Math.max(100, +f.value * 2 || 100)}" value="${esc(f.value ?? 0)}"><input class="inp dp-inp ltr" inputmode="numeric" value="${esc(f.value ?? '')}">`
      : `<input class="inp dp-inp ltr" ${f.kind === 'number' ? 'inputmode="numeric"' : ''} value="${esc(f.value ?? '')}">`;
    screen(label, `<div class="dp-edit"><small class="dp-key ltr">${esc(f.key)}</small>${input}
      ${f.danger ? `<p class="dp-warn">⚠ ${esc(T('تغيير هذا الإعداد قد يقطع اتصال الجهاز بالبرنامج. تأكد من القيمة قبل الحفظ.', 'Changing this can cut the terminal off from the program. Check the value before saving.'))}</p>` : ''}
      ${/Thr|Threshold/i.test(f.key) ? `<p class="dp-note">${esc(T('رقم أعلى = تطابق أدق وأصعب، ورقم أقل = قبول أسرع مع احتمال خطأ أكبر.', 'Higher = stricter match; lower = faster acceptance with more risk of a wrong match.'))}</p>` : ''}
      <button class="btn primary dp-save" ${P.writable ? '' : 'disabled'}>${esc(T('حفظ في الجهاز', 'Save to terminal'))}</button><p class="dp-msg"></p></div>`, back);
    let val = String(f.value ?? '');
    const rng = $('.dp-range', view), inp = $('.dp-inp', view);
    if (rng && inp) { rng.oninput = () => { inp.value = rng.value; }; inp.oninput = () => { rng.value = inp.value; }; }
    $$('.dp-switch button', view).forEach(b => b.onclick = () => { val = b.dataset.v; $$('.dp-switch button', view).forEach(x => x.classList.toggle('on', x === b)); });
    $('.dp-save', view).onclick = async () => {
      if (inp) val = inp.value.trim();
      if (f.danger && !await confirmBox(T(`تغيير «${label}» إلى ${val}؟`, `Change “${label}” to ${val}?`))) return;
      const msg = $('.dp-msg', view); msg.textContent = T('جارٍ الحفظ…', 'Saving…');
      try {
        const r = await POST(`/api/devices/${d.id}/panel`, { options: { [f.key]: val } });
        if (r.applied) { const got = r.applied[f.key]; msg.textContent = got === val ? '✓ ' + T('حُفظ في الجهاز', 'Saved on the terminal') : `⚠ ${T('الجهاز رفض القيمة أو لا يدعم هذا الخيار', 'The terminal refused the value or lacks this option')} (${got ?? '—'})`; }
        else msg.textContent = '✓ ' + T('سيُطبَّق عند اتصال الجهاز التالي', 'Applied at the next connection');
        f.value = r.applied ? (r.applied[f.key] ?? f.value) : val;
      } catch (e) { msg.textContent = '✗ ' + e.message; }
    };
  };
  // ---------- date & time
  const timeView = () => {
    const sec = P.sections.find(x => x.id === 'time') || { fields: [] };
    const fields = sec.fields.filter(f => f.key !== 'DeviceTime');
    const t = new Date(); t.setMinutes(t.getMinutes() - t.getTimezoneOffset());
    screen(T('التاريخ والوقت', 'Date & time'), `<div class="dp-clockcard"><small>${esc(T('ساعة الجهاز', 'Terminal clock'))}</small><b class="dp-devtime ltr">${fmt(devNow())}</b>
        <small>${esc(Math.abs(offset) < 60000 ? T('مطابقة لساعة هذا الحاسوب', 'In step with this PC') : T(`تختلف عن هذا الحاسوب بـ ${Math.round(offset / 60000)} دقيقة`, `${Math.round(offset / 60000)} min off this PC`))}</small></div>
      <button class="dp-row" data-t="pc" ${P.writable ? '' : 'disabled'}><span>🕒 ${esc(T('مزامنة مع وقت هذا الحاسوب', 'Sync with this PC'))}</span><em>›</em></button>
      <div class="dp-row ro dp-setrow"><input type="datetime-local" step="1" class="inp ltr" value="${t.toISOString().slice(0, 19)}"><button class="btn small" data-t="custom" ${P.writable ? '' : 'disabled'}>${esc(T('ضبط', 'Set'))}</button></div>
      ${fields.map((f, i) => `<button class="dp-row" data-i="${i}"><span>${esc(L(f))}</span><b class="ltr">${esc(valueText(f))}</b><em>›</em></button>`).join('')}<p class="dp-msg"></p>`);
    const go = async (time) => {
      const msg = $('.dp-msg', view); msg.textContent = T('جارٍ الضبط…', 'Setting…');
      try {
        const r = await POST(`/api/devices/${d.id}/panel/time`, time ? { time } : {});
        if (r.applied) { offset = new Date(r.applied.replace(' ', 'T')) - Date.now(); msg.textContent = '✓ ' + T('ضُبط وقت الجهاز', 'Terminal clock set'); }
        else msg.textContent = '✓ ' + T('سيُضبط عند اتصال الجهاز التالي', 'Set at the next connection');
      } catch (e) { msg.textContent = '✗ ' + e.message; }
    };
    $('[data-t=pc]', view).onclick = () => go(null);
    $('[data-t=custom]', view).onclick = () => go($('.dp-setrow input', view).value.replace('T', ' '));
    $$('[data-i]', view).forEach(r => r.onclick = () => edit(fields[+r.dataset.i], timeView));
  };
  // ---------- users & data
  const usersView = () => {
    const rows = [[T('المستخدمون', 'Users'), P.users], [T('الوجوه', 'Faces'), P.faces], [T('البصمات', 'Fingerprints'), P.fps], [T('الكف', 'Palms'), P.palms], [T('سجلات الحضور', 'Records'), P.records]];
    screen(T('المستخدمون', 'Users'), rows.map(r => `<div class="dp-row ro"><span>${esc(r[0])}</span><b class="ltr">${esc(r[1] ?? '—')}</b></div>`).join('')
      + `<button class="dp-row" data-t="pull_bio"><span>⬇ ${esc(T('سحب المستخدمين والبصمات والوجوه والكف', 'Pull users, fingerprints, faces & palms'))}</span><em>›</em></button>
      <button class="dp-row" data-t="emps"><span>👥 ${esc(T('فتح قائمة الموظفين', 'Open the employee list'))}</span><em>›</em></button>`);
    $('[data-t=pull_bio]', view).onclick = () => { dlg.close(); pullBio(d, g); };
    $('[data-t=emps]', view).onclick = () => { dlg.close(); location.hash = '#/personnel/employees'; };
  };
  const tools = [
    { id: 'pull_bio', icon: '⬇', label: T('سحب البصمات والوجوه والكف', 'Pull biometrics'), read: true },
    { id: 'sync_time', icon: '🕒', label: T('ضبط الوقت الآن', 'Set time now') },
    { id: 'reboot', icon: '⟳', label: T('إعادة التشغيل', 'Restart'), confirm: T('إعادة تشغيل الجهاز؟', 'Restart the terminal?') },
    { id: 'clear_log', icon: '🗑', label: T('حذف سجلات الحضور من الجهاز', 'Erase records on the terminal'), confirm: T('حذف سجلات الحضور من ذاكرة الجهاز؟ (تبقى في البرنامج)', 'Erase the records from the terminal? (they stay in the program)') },
  ];
  const dataView = () => {
    screen(T('إدارة البيانات', 'Data'), tools.map(t => `<button class="dp-row" data-t="${t.id}" ${!P.writable && !t.read ? 'disabled' : ''}><span>${t.icon} ${esc(t.label)}</span><em>›</em></button>`).join('') + '<p class="dp-msg"></p>');
    $$('.dp-row', view).forEach(b => b.onclick = async () => {
      const t = tools.find(x => x.id === b.dataset.t);
      if (t.id === 'pull_bio') { dlg.close(); return pullBio(d, g); }
      if (!await confirmDeviceOperation(t.id, [d], t.confirm)) return;
      const r = await guard(() => POST(`/api/devices/${d.id}/action`, { action: t.id }));
      $('.dp-msg', view).textContent = '✓ ' + (r.delivered ? T('نُفّذ على الجهاز', 'Done on the terminal') : T('أُرسل — يُنفذ عند اتصال الجهاز', 'Sent — runs at the next connection'));
    });
  };
  const anyView = () => {
    screen(T('أي إعداد بالاسم', 'Any setting'), `<div class="dp-edit"><p class="dp-note">${esc(T('اكتب اسم الإعداد كما يعرفه الجهاز (مثل MThreshold أو VOLUME) لقراءته وتعديله.', 'Type the setting name the terminal uses (e.g. MThreshold or VOLUME) to read and change it.'))}</p>
      <input class="inp dp-inp ltr" placeholder="MThreshold"><button class="btn primary">${esc(T('قراءة', 'Read'))}</button><p class="dp-msg"></p></div>`);
    const inp = $('.dp-inp', view);
    $('.btn', view).onclick = async () => {
      const key = inp.value.trim(); if (!key) return;
      const msg = $('.dp-msg', view); msg.textContent = T('جارٍ القراءة…', 'Reading…');
      try {
        const r = await GET(`/api/devices/${d.id}/panel/option?key=${encodeURIComponent(key)}`);
        const known = r.value ?? P.advanced[key] ?? null;
        if (known === null || known === '') { msg.textContent = r.live ? T('الجهاز لا يعرف هذا الإعداد.', 'The terminal does not know this setting.') : T('لا يوجد اتصال مباشر بالجهاز الآن.', 'No live link to the terminal now.'); return; }
        edit({ key, ar: key, en: key, kind: 'text', value: known, raw: true, danger: false }, anyView);
      } catch (e) { msg.textContent = '✗ ' + e.message; }
    };
  };
  const repaint = () => { status(); if (where === 'idle') idle(); else if (where === 'menu') menu(); else if (where !== 'edit' && where !== 'any') open(where); };
  async function refresh(manual) {
    refreshing = true; if (P) status();
    if (manual && where === 'menu') menu();
    try { P = await GET(`/api/devices/${d.id}/panel?live=true`); } catch (e) { if (!P) { view.innerHTML = `<div class="dp-loading">✗ ${esc(e.message || e)}</div>`; return; } }
    refreshing = false; repaint();
  }
  $('.dp-bottom', body).onclick = idle;
  $$('.dp-keys button', body).forEach(b => b.onclick = () => { const k = b.dataset.key; if (k === 'M' || k === 'OK') { if (where === 'idle') menu(); } else if (k === 'ESC') { where === 'menu' ? idle() : menu(); } });
  if (P) { status(); idle(); } else view.innerHTML = `<div class="dp-loading">${esc(T('جارٍ الاتصال بالجهاز…', 'Connecting to the terminal…'))}</div>`;
  refresh(false);
}
async function pullEveryone(g) {
  if (!await confirmDeviceOperation('pull_everyone', [])) return;
  const r = await guard(() => POST('/api/devices/pull-everyone'));
  const m = r.missing || {};
  dialog({ title: T('سحب كل البيانات', 'Pull everything'), size: 'narrow', body: `<div class="pull-all">
      <p>${esc(T(`طُلبت البيانات من ${r.push} جهاز يتصل بالبرنامج (تصل خلال دقيقة عند اتصاله التالي)، ويُقرأ الآن ${r.direct} جهاز متصل مباشرة.`, `Asked ${r.push} push terminal(s) (data arrives at their next connection, within a minute) and reading ${r.direct} directly linked terminal(s) now.`))}</p>
      <p class="muted">${esc(T('يشمل: الأسماء، البطاقات، كلمات مرور الجهاز، البصمات، الوجوه، الكف، وصور الموظفين.', 'Includes names, cards, terminal passwords, fingerprints, faces, palms and employee pictures.'))}</p>
      <div class="kv"><div>${esc(T('موظفون لهم حركات', 'People with punches'))}</div><div><b>${m.punched ?? 0}</b></div>
      <div>${esc(T('بدون اسم', 'Without a name'))}</div><div><b>${m.no_name ?? 0}</b></div>
      <div>${esc(T('بدون صورة', 'Without a picture'))}</div><div><b>${m.no_photo ?? 0}</b></div>
      <div>${esc(T('بدون بصمة أو وجه أو كف', 'Without any biometric'))}</div><div><b>${m.no_bio ?? 0}</b></div></div>
      <p class="muted">${esc(T('ملاحظة: الصور تصل فقط من الأجهزة التي ترسل للبرنامج (المنفذ 90)؛ الاتصال المباشر 4370 ينقل الأسماء والبصمات والوجوه.', 'Pictures only come from terminals that send to the program (port 90); the direct 4370 link carries names, fingerprints and faces.'))}</p></div>`,
    buttons: [{ label: T('حسناً', 'OK'), cls: 'primary' }] });
  if (g) setTimeout(() => g.reload(), 3000);
}
async function pullBio(d, g) {
  if (!await confirmDeviceOperation('pull_bio', [d])) return;
  const r = await guard(() => POST(`/api/devices/${d.id}/action`, { action: 'pull_bio' }));
  const names = { fp: T('بصمات الأصابع', 'Fingerprints'), face: T('الوجوه', 'Faces'), palm: T('الكف', 'Palms'), photo: T('صور الوجه', 'Face photos') };
  const body = h(`<div>${operationFlow('device_to_server')}<p class="muted">${esc(T('يرسل الجهاز بيانات التسجيل المتاحة على دفعات عبر ADMS، أو تُقرأ البيانات المدعومة مباشرة عبر 4370. الأعداد هنا تقارن المخزون؛ انتهاء الأوامر لا يثبت وحده وصول كل قالب.', 'Available enrollment data arrives in ADMS batches, or supported data is read directly over 4370. These counters compare inventories; completed commands alone do not prove every template arrived.'))}</p><div class="pull-rows"></div><p class="pull-msg muted"></p></div>`);
  let stop = false;
  dialog({ title: `${T('سحب البيانات الحيوية', 'Pull biometrics')} — ${d.label || d.alias || d.sn}`, size: 'narrow', body, buttons: [{ label: T('إغلاق', 'Close') }], onClose: () => { stop = true; g && g.reload(); } });
  const tick = async () => {
    if (stop) return;
    let st; try { st = await GET(`/api/devices/${d.id}/bio-status`); } catch (e) { return setTimeout(tick, 3000); }
    $('.pull-rows', body).innerHTML = st.rows.map(x => {
      const total = x.device || 0, p = total ? Math.min(100, Math.round(100 * x.server / total)) : (x.server ? 100 : 0);
      return `<div class="pull-row"><span>${esc(names[x.key])}</span><span class="d-bar"><i style="width:${p}%"></i></span><b>${x.server}${x.device != null ? ` / ${x.device}` : ''}</b></div>`;
    }).join('');
    if (stop || !body.isConnected) return;
    $('.pull-msg', body).textContent = st.pending ? T(`بانتظار الجهاز (${st.pending} طلب)…`, `Waiting for the terminal (${st.pending} request(s))…`) : T('لا توجد طلبات معلقة — راجع مقارنة الأعداد', 'No pending requests — review the inventory counts');
    if (st.pending || st.link === '4370') setTimeout(tick, 2500);
  };
  tick();
  if (r && r.read) toast(T('قُرئ الجهاز مباشرة', 'Terminal read directly'), 'ok');
}
function setDeviceArea(sel, lk, g) {
  const f = [{ key: 'area_id', label: T('المنطقة', 'Area'), type: 'select', options: opts(lk.areas), required: true }];
  formDialog({ title: T('منطقة جديدة', 'New area'), size: 'narrow', cls: 'one', fields: f,
    save: async (v) => { for (const d of sel) await PUT(`/api/devices/${d.id}`, { area_id: v.area_id }); } })
    .then(() => { toast(T('ستُرسل بيانات موظفي المنطقة الجديدة إلى الأجهزة', 'Employees of the new area will be sent to the devices'), 'ok'); g.reload(); });
}
function uploadAtt(sel, act) {
  const f = [{ key: 'start', label: T('من', 'From'), type: 'date', default: addDays(today(), -30), required: true }, { key: 'end', label: T('إلى', 'To'), type: 'date', default: today(), required: true }];
  formDialog({ title: T('سحب الحركات من الجهاز', 'Upload transactions'), size: 'narrow', fields: f, save: (v) => act(sel, 'upload_att', null, { start: v.start + ' 00:00:00', end: v.end + ' 23:59:59' }) });
}
function customCmd(sel, act) {
  const f = [{ key: 'command', label: T('الأمر (بدون C:ID:)', 'Command (without C:ID:)'), required: true, placeholder: 'DATA QUERY USERINFO PIN=1', full: true }];
  formDialog({ title: T('أمر مخصص', 'Custom command'), size: 'narrow', cls: 'one', fields: f, save: (v) => act(sel, 'custom', null, v) });
}
function deviceForm(row, lk, g) {
  const f = [
    { key: 'sn', label: T('الرقم التسلسلي', 'Serial number'), required: true, readonly: !!row },
    { key: 'alias', label: T('اسم الجهاز', 'Device name'), required: true },
    { key: 'alias_en', label: 'Device name (English)', placeholder: 'e.g. Gaza' },
    { key: 'area_id', label: T('المنطقة', 'Area'), type: 'select', options: opts(lk.areas), required: true },
    { key: 'ip', label: T('عنوان IP للجهاز', 'Terminal IP address') },
    { key: 'adms_allowed_ips', label: T('مصادر اتصال ADMS المسموحة', 'Allowed ADMS sources'), placeholder: '10.0.0.20, 10.0.1.0/24', hint: T('عناوين IP أو شبكات CIDR مفصولة بفواصل. فارغ = قبول الجهاز من أي عنوان (DHCP).', 'Comma-separated IP addresses or CIDR networks. Blank accepts this terminal from any address (DHCP).'), full: true },
    { key: 'tcp_poll', label: T('القراءة المباشرة', 'Direct read'), type: 'checkbox', text: T('قراءة الجهاز دورياً عبر 4370 (مستخدمون، بصمات، وجوه، حركات)', 'Read the terminal periodically over 4370 (users, fingerprints, faces, punches)') },
    { key: 'time_zone', label: T('المنطقة الزمنية (ساعات، فارغ = حسب النظام)', 'Time zone (hours, empty = system)'), type: 'number', min: -12, max: 14 },
    { key: 'heartbeat', label: T('فترة الاتصال (ثانية)', 'Heartbeat (seconds)'), type: 'number', min: 5, default: 10 },
    { key: 'trans_interval', label: T('فترة رفع البيانات (دقيقة)', 'Upload interval (minutes)'), type: 'number', min: 1, default: 1 },
    { key: 'trans_times', label: T('أوقات الرفع المجدولة', 'Scheduled upload times'), default: '00:00;14:05' },
    { key: 'comm_key', label: T('مفتاح الاتصال (Comm Key)', 'Comm key'), default: '0' },
    { key: 'tcp_port', label: T('منفذ الاتصال المباشر', 'Direct link port'), type: 'number', default: 4370 },
    { key: 'realtime', label: T('الرفع الفوري', 'Real-time upload'), type: 'checkbox', text: T('إرسال كل بصمة فوراً', 'Send each punch immediately'), default: true },
    { key: 'is_attendance', label: T('جهاز حضور', 'Attendance device'), type: 'checkbox', text: T('بصماته تُحتسب في الحضور', 'Punches count for attendance'), default: true },
    { key: 'is_registration', label: T('جهاز تسجيل', 'Registration device'), type: 'checkbox', text: T('يُستخدم لتسجيل البصمات', 'Used for enrollment') },
    { key: 'enabled', label: T('مفعّل', 'Enabled'), type: 'checkbox', text: T('السماح للجهاز بالاتصال', 'Allow the device to connect'), default: true },
  ];
  formDialog({ title: row ? T('تعديل جهاز', 'Edit device') : T('إضافة جهاز', 'Add device'), fields: f, data: row ? { ...row, adms_allowed_ips: (row.adms_allowed_ips || []).join(', ') } : {},
    save: (v) => { v.adms_allowed_ips = v.adms_allowed_ips.split(/[\s,،]+/).filter(Boolean); return row ? PUT(`/api/devices/${row.id}`, v) : POST('/api/devices', v); } }).then(() => { App.lookups = null; g.reload(); });
}
async function deviceDetail(r, g) {
  const d = await GET(`/api/devices/${r.id}`);
  const bio = Object.entries(d.bio_support).map(([t, v]) => `<span class="badge info">${esc(T(...(BIO_NAMES[t] || [t, t])))}${v ? ' v' + esc(v) : ''}</span>`).join(' ') || '<span class="muted">—</span>';
  const kv = [[T('الرقم التسلسلي', 'Serial'), d.sn], [T('الطراز', 'Model'), d.model], [T('البرنامج الثابت', 'Firmware'), d.firmware], [T('إصدار البروتوكول', 'Push version'), d.push_ver],
    ['IP', d.ip], ['MAC', d.mac], [T('المنصة', 'Platform'), d.platform], [T('الحالة', 'State'), stateLabel(d.state)], [T('آخر اتصال', 'Last activity'), d.last_activity], [T('آخر تهيئة', 'Last init'), d.last_init],
    [T('المستخدمون', 'Users'), d.user_count], [T('الوجوه', 'Faces'), d.face_count], [T('البصمات', 'Fingerprints'), d.fp_count], [T('الكف', 'Palms'), d.palm_count], [T('الحركات', 'Records'), d.att_count],
    [T('خوارزمية البصمة', 'FP algorithm'), d.fp_alg], [T('خوارزمية الوجه', 'Face algorithm'), d.face_alg], ['ATTLOG Stamp', d.att_stamp], ['OPERLOG Stamp', d.op_stamp]];
  const body = h(`<div><div class="tabs"><button class="active" data-t="i">${esc(T('المعلومات', 'Info'))}</button><button data-t="c">${esc(T('الأوامر', 'Commands'))}</button><button data-t="t">${esc(T('الاتصال', 'Traffic'))}</button><button data-t="o">${esc(T('خيارات الجهاز', 'Options'))}</button></div>
    <div data-p="i"><details class="direction-guide"><summary>${esc(T('شرح القراءة والإرسال والتوزيع', 'Read, send and distribute explained'))}</summary>${deviceDirectionGuide()}</details><div class="kv">${kv.map(([k, v]) => `<div>${esc(k)}</div><div><bdi>${esc(v ?? '')}</bdi></div>`).join('')}<div>${esc(T('أنواع التحقق المدعومة', 'Supported biometrics'))}</div><div>${bio}</div></div></div>
    <div data-p="c" class="hidden"></div><div data-p="t" class="hidden"></div>
    <div data-p="o" class="hidden"><pre class="code">${esc(JSON.stringify(d.options, null, 2))}</pre></div></div>`);
  dialog({ title: `${d.alias} (${d.sn})`, size: 'wide', body, buttons: [{ label: T('إغلاق', 'Close') }] });
  $$('.tabs button', body).forEach(b => b.onclick = async () => {
    $$('.tabs button', body).forEach(x => x.classList.toggle('active', x === b));
    $$('[data-p]', body).forEach(p => p.classList.toggle('hidden', p.dataset.p !== b.dataset.t));
    if (b.dataset.t === 'c') { const p = $('[data-p=c]', body); p.innerHTML = ''; commandsGrid(p, d.sn); }
    if (b.dataset.t === 't') { const t = await GET('/api/device-traffic?sn=' + encodeURIComponent(d.sn)); $('[data-p=t]', body).innerHTML = trafficTable(t.rows); }
  });
}
function cmdBadge(s) { return `<span class="badge ${{ done: 'ok', failed: 'bad', sent: 'info', pending: 'warn' }[s] || ''}">${esc({ done: T('نُفذ', 'Done'), failed: T('فشل', 'Failed'), sent: T('أُرسل', 'Sent'), pending: T('بالانتظار', 'Pending') }[s] || s)}</span>`; }
function commandsGrid(c, sn) {
  const g = grid(c, {
    columns: [{ key: 'id', label: 'ID', cls: 'num' }, { key: 'device_sn', label: T('الجهاز', 'Device'), cls: 'ltr' }, { key: 'title', label: T('الأمر', 'Command') },
      { key: 'direction', label: T('طبيعة الإجراء', 'Operation direction'), render: r => `<span class="wrap">${esc(commandDescription(r).title)}</span>` },
      { key: 'status', label: T('الحالة', 'Status'), render: r => cmdBadge(r.status) }, { key: 'return_code', label: T('النتيجة', 'Return'), cls: 'ltr' },
      { key: 'created_at', label: T('وقت الإنشاء', 'Created'), cls: 'ltr' }, { key: 'returned_at', label: T('وقت التنفيذ', 'Returned'), cls: 'ltr' },
      { key: 'content', label: T('المحتوى', 'Content'), render: r => `<span class="ltr muted" title="${esc(r.content)}">${esc(r.content.slice(0, 70))}</span>` }],
    fetch: serverFetch('/api/device-commands' + (sn ? '?sn=' + encodeURIComponent(sn) : '')), select: false,
    onRow: r => { const d = commandDescription(r); dialog({title:d.title,size:'wide',body:`${operationFlow(d.direction)}<p>${esc(d.text)}</p><p>${esc(r.title)} · ${esc(r.device_sn)}</p><p>${cmdBadge(r.status)} · ${esc(T('رد الجهاز','Device return'))}: <bdi>${esc(r.return_code ?? '—')}</bdi></p>`,buttons:[{label:T('إغلاق','Close')}]}); },
    filters: [{ key: 'status', label: T('كل الحالات', 'All states'), type: 'select', options: [{ value: 'pending', label: T('بالانتظار', 'Pending') }, { value: 'sent', label: T('أُرسل', 'Sent') }, { value: 'done', label: T('نُفذ', 'Done') }, { value: 'failed', label: T('فشل', 'Failed') }] }],
    toolbar: [{ label: T('حذف المنفذة والفاشلة', 'Clear done/failed'), icon: 'del', perm: 'device.control', action: async () => { await guard(() => POST('/api/device-commands/clear', { sn })); g.reload(); } },
      { label: T('إلغاء المعلقة', 'Cancel pending'), icon: 'close', perm: 'device.control', action: async () => { if (await confirmBox(T('إلغاء كل الأوامر التي لم تُنفذ بعد؟', 'Cancel all commands not yet executed?'))) { await guard(() => POST('/api/device-commands/clear', { sn, status: ['pending', 'sent'] })); g.reload(); } } }],
  });
  return g;
}
function pageCommands(c) { title(c, T('أوامر الأجهزة', 'Device commands')); const g = commandsGrid(c); Live.on('commands', () => g.reload(), 1500); App.timers.push(setInterval(() => document.visibilityState === 'visible' && g.reload(), 30000)); }
function trafficTable(rows) {
  return `<div class="grid-wrap" style="max-height:60vh"><table class="grid"><thead><tr><th>${esc(T('الوقت', 'Time'))}</th><th>SN</th><th>${esc(T('الطلب', 'Request'))}</th><th>${esc(T('الحجم', 'Bytes'))}</th><th>${esc(T('الرد', 'Reply'))}</th></tr></thead><tbody>
    ${rows.map(t => `<tr><td class="ltr">${esc(t.time.slice(11))}</td><td class="ltr">${esc(t.sn)}</td><td class="ltr wrap">${esc(t.method)} ${esc(t.path)}?${esc(t.query)}</td><td class="num">${t.bytes}</td><td class="ltr wrap"><pre style="margin:0;white-space:pre-wrap;font-size:11px">${esc(t.reply)}</pre></td></tr>`).join('') || `<tr><td class="empty" colspan="5">${esc(T('لم يتصل أي جهاز منذ تشغيل الخادم', 'No device has contacted the server since it started'))}</td></tr>`}</tbody></table></div>`;
}
async function pageTraffic(c) {
  title(c, T('مراقبة اتصال الأجهزة (ADMS)', 'Device communication (ADMS)'));
  c.appendChild(h(`<div class="alert info">${esc(T('آخر 500 طلب من الأجهزة إلى الخادم. مفيد لتشخيص مشاكل الربط: إن لم يظهر الجهاز هنا فالمشكلة في الشبكة أو الجدار الناري أو إعداد الخادم على الجهاز.', 'The last 500 device requests. Useful for troubleshooting: if a device never shows up here, check the network, firewall or the server setting on the device.'))}</div>`));
  const p = h('<div class="panel"></div>'); c.appendChild(p);
  const draw = async () => {
    const t = await GET('/api/device-traffic');
    p.innerHTML = trafficTable(t.rows);
  };
  await draw(); App.timers.push(setInterval(() => document.visibilityState === 'visible' && draw(), 5000));
}
async function pageTransactions(c) {
  title(c, T('سجل الحركات', 'Transactions'));
  const lk = await lookups();
  grid(c, {
    columns: [
      { key: 'emp_code', label: T('الرقم', 'ID') }, { key: 'name', label: T('الاسم', 'Name') }, { key: 'department', label: T('القسم', 'Department') },
      { key: 'punch_time', label: T('وقت البصمة', 'Punch time'), cls: 'ltr' },
      { key: 'punch_state', label: T('الحالة', 'State'), render: r => esc(stateName(r.punch_state)) },
      { key: 'verify', label: T('التحقق', 'Verify'), render: r => esc(verifyName(r.verify)) },
      { key: 'device', label: T('الجهاز', 'Device') },
      { key: 'temperature', label: T('الحرارة', 'Temp.'), render: r => r.temperature ? esc(r.temperature) + '°' : '' },
      { key: 'photo', label: T('صورة', 'Photo'), render: r => r.has_photo ? `<a href="/api/transactions/${r.id}/photo" target="_blank">📷</a>` : '' },
      { key: 'source', label: T('المصدر', 'Source') },
      { key: 'upload_time', label: T('وقت الرفع', 'Uploaded'), cls: 'ltr' },
    ],
    fetch: serverFetch('/api/transactions'), select: false,
    filters: [{ key: 'start', label: T('من', 'From'), type: 'date', value: addDays(today(), -7) }, { key: 'end', label: T('إلى', 'To'), type: 'date', value: today() },
      { key: 'department_id', label: T('كل الأقسام', 'All departments'), type: 'select', options: opts(lk.departments) },
      { key: 'sn', label: T('كل الأجهزة', 'All devices'), type: 'select', options: opts(lk.devices, 'sn', 'name') }],
    toolbar: [{ label: T('تصدير', 'Export'), icon: 'download', menu: [
      { label: 'Excel', action: (s, g) => download(`/api/reports/transactions?${qs({ start: g.state.filters.start, end: g.state.filters.end, department_ids: g.state.filters.department_id, device: g.state.filters.sn, lang: App.lang, fmt: 'xlsx' })}`) },
      { label: 'CSV', action: (s, g) => download(`/api/reports/transactions?${qs({ start: g.state.filters.start, end: g.state.filters.end, department_ids: g.state.filters.department_id, device: g.state.filters.sn, lang: App.lang, fmt: 'csv' })}`) }] }],
  });
}
function pageOplogs(c) {
  title(c, T('سجل عمليات الجهاز', 'Device operation log'));
  grid(c, { columns: [{ key: 'device_sn', label: T('الجهاز', 'Device'), cls: 'ltr' }, { key: 'op_code', label: T('رمز العملية', 'Operation'), cls: 'num' }, { key: 'admin', label: T('المدير', 'Admin') },
    { key: 'op_time', label: T('الوقت', 'Time'), cls: 'ltr' }, { key: 'obj1', label: T('الهدف 1', 'Object 1') }, { key: 'obj2', label: T('الهدف 2', 'Object 2') }, { key: 'upload_time', label: T('وقت الرفع', 'Uploaded'), cls: 'ltr' }],
    fetch: serverFetch('/api/device-oplogs'), select: false, search: false });
}
function pageErrors(c) {
  title(c, T('سجل أخطاء الأجهزة', 'Device error log'));
  grid(c, { columns: [{ key: 'device_sn', label: T('الجهاز', 'Device'), cls: 'ltr' }, { key: 'err_code', label: T('الرمز', 'Code') }, { key: 'err_msg', label: T('الرسالة', 'Message'), cls: 'wrap' }, { key: 'upload_time', label: T('الوقت', 'Time'), cls: 'ltr' }],
    fetch: serverFetch('/api/device-errorlogs'), select: false, search: false });
}

// ============================================================== attendance setup
function ttFields() {
  return [
    { key: 'alias', label: T('الاسم', 'Name'), required: true },
    { key: 'kind', label: T('النوع', 'Type'), type: 'select', blank: false, options: [{ value: 'normal', label: T('ثابت', 'Normal') }, { value: 'flexible', label: T('مرن (عدد ساعات)', 'Flexible (hours)') }] },
    { key: 'check_in', label: T('وقت الدخول', 'Check-in'), type: 'time', required: true, default: '08:00' },
    { key: 'check_out', label: T('وقت الخروج', 'Check-out'), type: 'time', required: true, default: '16:00', hint: T('إذا كان أقل من وقت الدخول فالدوام ليلي ينتهي اليوم التالي', 'Earlier than check-in = overnight shift') },
    { key: 'late_grace', label: T('السماح بالتأخير (دقيقة)', 'Late allowance (min)'), type: 'number', min: 0, default: 0 },
    { key: 'early_grace', label: T('السماح بالخروج المبكر (دقيقة)', 'Early-leave allowance (min)'), type: 'number', min: 0, default: 0 },
    { section: T('نوافذ البصمة (بالدقائق)', 'Punch windows (minutes)') },
    { key: 'in_ahead', label: T('بداية نافذة الدخول قبل الموعد', 'Check-in window starts before'), type: 'number', min: 0, default: 120 },
    { key: 'in_above', label: T('نهاية نافذة الدخول بعد الموعد', 'Check-in window ends after'), type: 'number', min: 0, default: 240 },
    { key: 'out_ahead', label: T('بداية نافذة الخروج قبل الموعد', 'Check-out window starts before'), type: 'number', min: 0, default: 240 },
    { key: 'out_above', label: T('نهاية نافذة الخروج بعد الموعد', 'Check-out window ends after'), type: 'number', min: 0, default: 240 },
    { key: 'must_check_in', label: T('بصمة الدخول', 'Check-in punch'), type: 'checkbox', text: T('إلزامية', 'Required'), default: true },
    { key: 'must_check_out', label: T('بصمة الخروج', 'Check-out punch'), type: 'checkbox', text: T('إلزامية', 'Required'), default: true },
    { section: T('الاستراحة والمدة', 'Break & duration') },
    { key: 'break_start', label: T('بداية الاستراحة (تُخصم)', 'Break start (deducted)'), type: 'time' },
    { key: 'break_end', label: T('نهاية الاستراحة', 'Break end'), type: 'time' },
    { key: 'work_minutes', label: T('ساعات الدوام المرن (دقيقة)', 'Flexible work minutes'), type: 'number', default: 480 },
    { key: 'workday', label: T('يُحسب كأيام عمل', 'Counts as work days'), type: 'number', step: '0.5', default: 1 },
    { key: 'color', label: T('اللون', 'Color'), type: 'color', default: '#1e88e5' },
  ];
}
function pageTimetables(c) {
  simplePage(c, T('أوقات الدوام', 'Timetables'), '/api/timetables', 'attendance.edit', [
    { key: 'alias', label: T('الاسم', 'Name'), render: r => `<span class="dot" style="background:${esc(r.color)}"></span>${esc(r.alias)}` },
    { key: 'kind', label: T('النوع', 'Type'), render: r => r.kind === 'flexible' ? T('مرن', 'Flexible') : T('ثابت', 'Normal') },
    { key: 'check_in', label: T('الدخول', 'Check-in'), cls: 'ltr' }, { key: 'check_out', label: T('الخروج', 'Check-out'), cls: 'ltr' },
    { key: 'late_grace', label: T('سماح التأخير', 'Late allow.'), cls: 'num' }, { key: 'early_grace', label: T('سماح الخروج', 'Early allow.'), cls: 'num' },
    { key: 'break', label: T('الاستراحة', 'Break'), render: r => r.break_start ? `${r.break_start}-${r.break_end}` : '' },
    { key: 'workday', label: T('أيام عمل', 'Work days'), cls: 'num' }], ttFields(), { dialogSize: 'wide', formCls: 'three', editForm: (r, g) => timetableDatedDialog(r, g) });
}
async function pageShifts(c) {
  title(c, T('الورديات', 'Shifts'));
  c.appendChild(h(`<div class="alert info">${esc(T('الوردية = دورة (أسبوع/أيام/شهر) يُحدد لكل يوم فيها وقت دوام أو أكثر. الأيام بدون وقت دوام تعتبر راحة.', 'A shift is a cycle (week/days/month) with one or more timetables per day; days without a timetable are days off.'))}</div>`));
  const g = grid(c, {
    columns: [{ key: 'alias', label: T('الاسم', 'Name') }, { key: 'cycle', label: T('الدورة', 'Cycle'), render: r => `${r.cycle} × ${esc({ day: T('يوم', 'day'), week: T('أسبوع', 'week'), month: T('شهر', 'month') }[r.cycle_unit])}` }, { key: 'summary', label: T('أوقات الدوام', 'Timetables') }, { key: 'days', label: T('أيام العمل', 'Work days'), render: r => new Set(r.details.map(d => d.day_index)).size }],
    fetch: localFetch('/api/shifts'),
    onRow: (r) => can('attendance.edit') && shiftEditor(r, g),
    toolbar: [{ label: T('إضافة', 'Add'), icon: 'add', cls: 'primary', perm: 'attendance.edit', action: () => shiftEditor(null, g) },
      { label: T('تعديل', 'Edit'), icon: 'edit', perm: 'attendance.edit', needSel: true, action: (s) => shiftEditor(s[0], g) },
      { label: T('حذف', 'Delete'), icon: 'del', cls: 'danger', perm: 'attendance.edit', needSel: true, action: async (s) => { if (await confirmBox(T('حذف الوردية وجداولها؟', 'Delete the shift and its schedules?'))) { for (const x of s) await DEL(`/api/shifts/${x.id}`); App.lookups = null; g.reload(); } } }],
  });
}
async function shiftEditor(row, g) {
  const lk = await lookups(true);
  const s = row || { alias: '', cycle_unit: 'week', cycle: 1, details: [] };
  const sel = new Set(s.details.map(d => `${d.day_index}:${d.timetable_id}`));
  const body = h(`<div>${formHtml([{ key: 'alias', label: T('الاسم', 'Name'), required: true }, { key: 'cycle_unit', label: T('وحدة الدورة', 'Cycle unit'), type: 'select', blank: false, options: [{ value: 'week', label: T('أسبوع', 'Week') }, { value: 'day', label: T('يوم', 'Day') }, { value: 'month', label: T('شهر', 'Month') }] }, { key: 'cycle', label: T('طول الدورة', 'Cycle length'), type: 'number', min: 1, max: 12 }], s, 'three')}
    <div style="margin-top:12px" class="grid-wrap"></div></div>`);
  const holder = $('.grid-wrap', body);
  const draw = () => {
    const unit = $('[name=cycle_unit]', body).value, cyc = Math.max(1, +$('[name=cycle]', body).value || 1);
    const span = cyc * ({ day: 1, week: 7, month: 31 }[unit]);
    const label = (i) => unit === 'week' ? `${cyc > 1 ? T('أ', 'W') + (Math.floor(i / 7) + 1) + ' ' : ''}${WEEKDAYS()[i % 7]}` : unit === 'month' ? `${cyc > 1 ? T('ش', 'M') + (Math.floor(i / 31) + 1) + ' ' : ''}${(i % 31) + 1}` : `${T('يوم', 'Day')} ${i + 1}`;
    holder.innerHTML = `<table class="shift-grid"><thead><tr><th>${esc(T('اليوم', 'Day'))}</th>${lk.timetables.map(t => `<th><span class="dot" style="background:${esc(t.color)}"></span>${esc(t.name)}<br><small class="ltr">${t.check_in}-${t.check_out}</small></th>`).join('')}</tr></thead><tbody>
      ${Array.from({ length: span }, (_, i) => `<tr><th>${esc(label(i))}</th>${lk.timetables.map(t => `<td><input type="checkbox" data-k="${i}:${t.id}" ${sel.has(`${i}:${t.id}`) ? 'checked' : ''}></td>`).join('')}</tr>`).join('')}</tbody></table>
      ${lk.timetables.length ? '' : `<div class="alert">${esc(T('أضف وقت دوام أولاً', 'Add a timetable first'))}</div>`}`;
    $$('input[data-k]', holder).forEach(i => i.onchange = () => i.checked ? sel.add(i.dataset.k) : sel.delete(i.dataset.k));
  };
  $('[name=cycle_unit]', body).onchange = draw; $('[name=cycle]', body).onchange = draw; draw();
  dialog({ title: row ? T('تعديل وردية', 'Edit shift') : T('وردية جديدة', 'New shift'), size: 'wide', body, buttons: [{ label: T('إلغاء', 'Cancel') }, { label: T('حفظ', 'Save'), cls: 'primary', action: async () => {
    const data = { alias: $('[name=alias]', body).value, cycle_unit: $('[name=cycle_unit]', body).value, cycle: +$('[name=cycle]', body).value || 1,
      details: [...sel].map(k => { const [d, t] = k.split(':'); return { day_index: +d, timetable_id: +t }; }) };
    if (row) await PUT(`/api/shifts/${row.id}`, data); else await POST('/api/shifts', data);
    toast(T('تم الحفظ', 'Saved'), 'ok'); App.lookups = null; g.reload();
  } }] });
}
async function pageSchedules(c) {
  title(c, T('جدولة الموظفين', 'Employee schedules'));
  const lk = await lookups(true);
  const g = grid(c, {
    columns: [{ key: 'emp_code', label: T('الرقم', 'ID') }, { key: 'name', label: T('الاسم', 'Name') }, { key: 'department', label: T('القسم', 'Department') }, { key: 'shift', label: T('الوردية', 'Shift') }, { key: 'start_date', label: T('من', 'From'), cls: 'ltr' }, { key: 'end_date', label: T('إلى', 'To'), cls: 'ltr' }],
    fetch: serverFetch('/api/schedules'),
    toolbar: [{ label: T('جدولة', 'Assign shift'), icon: 'add', cls: 'primary', perm: 'attendance.edit', action: () => assignSchedule(lk, g) },
      { label: T('حذف', 'Delete'), icon: 'del', cls: 'danger', perm: 'attendance.edit', needSel: true, action: async (s) => { if (await confirmBox(T('حذف الجداول المحددة؟', 'Delete the selected schedules?'))) { for (const x of s) await DEL(`/api/schedules/${x.id}`); g.reload(); } } }],
  });
}
function assignSchedule(lk, g) {
  const chooser = employeeChooser();
  const f = [{ key: 'shift_id', label: T('الوردية', 'Shift'), type: 'select', options: opts(lk.shifts), required: true },
    { key: 'start_date', label: T('من تاريخ', 'From'), type: 'date', required: true, default: monthStart() },
    { key: 'end_date', label: T('إلى تاريخ', 'To'), type: 'date', required: true, default: `${new Date().getFullYear()}-12-31` },
    { key: 'department_ids', label: T('أو أقسام كاملة', 'Or whole departments'), type: 'multi', options: opts(lk.departments) }];
  const body = h(`<div>${formHtml(f)}</div>`); $('form', body).appendChild(chooser.el);
  dialog({ title: T('جدولة الموظفين', 'Assign shift'), body, buttons: [{ label: T('إلغاء', 'Cancel') }, { label: T('حفظ', 'Save'), cls: 'primary', action: async () => {
    const v = readForm(body, f); v.employee_ids = chooser.get().map(e => e.id);
    const r = await POST('/api/schedules', v); toast(T(`تمت جدولة ${r.employees} موظف`, `${r.employees} employee(s) scheduled`), 'ok'); g.reload();
  } }] });
}
async function pageDeptSchedules(c) {
  const lk = await lookups(true);
  c.appendChild(h(`<div class="alert info">${esc(T('جدول القسم يُطبق على موظفي القسم الذين ليس لهم جدول شخصي.', 'A department schedule applies to its employees who have no personal schedule.'))}</div>`));
  simplePage(c, T('جدولة الأقسام', 'Department schedules'), '/api/dept-schedules', 'attendance.edit',
    [{ key: 'department', label: T('القسم', 'Department') }, { key: 'shift', label: T('الوردية', 'Shift') }, { key: 'start_date', label: T('من', 'From'), cls: 'ltr' }, { key: 'end_date', label: T('إلى', 'To'), cls: 'ltr' }],
    [{ key: 'department_id', label: T('القسم', 'Department'), type: 'select', options: opts(lk.departments), required: true },
      { key: 'shift_id', label: T('الوردية', 'Shift'), type: 'select', options: opts(lk.shifts), required: true },
      { key: 'start_date', label: T('من', 'From'), type: 'date', required: true, default: monthStart() },
      { key: 'end_date', label: T('إلى', 'To'), type: 'date', required: true, default: `${new Date().getFullYear()}-12-31` }]);
}
async function pageTemp(c) {
  title(c, T('الجدول المؤقت (تبديل ورديات ليوم أو فترة)', 'Temporary schedule'));
  const lk = await lookups(true);
  const g = grid(c, {
    columns: [{ key: 'att_date', label: T('التاريخ', 'Date'), cls: 'ltr' }, { key: 'emp_code', label: T('الرقم', 'ID') }, { key: 'name', label: T('الاسم', 'Name') }, { key: 'timetable', label: T('وقت الدوام', 'Timetable') }],
    fetch: serverFetch('/api/temp-schedules'),
    filters: [{ key: 'start', label: T('من', 'From'), type: 'date', value: monthStart() }, { key: 'end', label: T('إلى', 'To'), type: 'date' }],
    toolbar: [{ label: T('إضافة', 'Add'), icon: 'add', cls: 'primary', perm: 'attendance.edit', action: () => {
      const chooser = employeeChooser();
      const f = [{ key: 'start_date', label: T('من', 'From'), type: 'date', required: true, default: today() }, { key: 'end_date', label: T('إلى', 'To'), type: 'date', required: true, default: today() },
        { key: 'timetable_ids', label: T('أوقات الدوام (بدون اختيار = يوم راحة)', 'Timetables (none = day off)'), type: 'multi', options: opts(lk.timetables) }];
      const body = h(`<div>${formHtml(f)}</div>`); $('form', body).appendChild(chooser.el);
      dialog({ title: T('جدول مؤقت', 'Temporary schedule'), body, buttons: [{ label: T('إلغاء', 'Cancel') }, { label: T('حفظ', 'Save'), cls: 'primary', action: async () => {
        const v = readForm(body, f); v.employee_ids = chooser.get().map(e => e.id); await POST('/api/temp-schedules', v); toast(T('تم الحفظ', 'Saved'), 'ok'); g.reload(); } }] });
    } }, { label: T('حذف', 'Delete'), icon: 'del', cls: 'danger', perm: 'attendance.edit', needSel: true, action: async (s) => { for (const x of s) await DEL(`/api/temp-schedules/${x.id}`); g.reload(); } }],
  });
}
async function pageHolidays(c) {
  const lk = await lookups();
  simplePage(c, T('العطل الرسمية', 'Holidays'), '/api/holidays', 'attendance.edit',
    [{ key: 'alias', label: T('الاسم', 'Name') }, { key: 'start_date', label: T('تاريخ البداية', 'Start date'), cls: 'ltr' }, { key: 'days', label: T('عدد الأيام', 'Days'), cls: 'num' },
      { key: 'department_id', label: T('القسم', 'Department'), render: r => esc(lk.departments.find(d => d.id === r.department_id)?.name || T('الكل', 'All')) }],
    [{ key: 'alias', label: T('الاسم', 'Name'), required: true }, { key: 'start_date', label: T('تاريخ البداية', 'Start date'), type: 'date', required: true },
      { key: 'days', label: T('عدد الأيام', 'Days'), type: 'number', min: 1, default: 1 }, { key: 'department_id', label: T('القسم (فارغ = الكل)', 'Department (empty = all)'), type: 'select', options: opts(lk.departments) }]);
}
function pageLeaveTypes(c) {
  simplePage(c, T('أنواع الإجازات', 'Leave types'), '/api/leave-types', 'attendance.edit',
    [{ key: 'code', label: T('الرمز', 'Code') }, { key: 'name', label: T('الاسم', 'Name'), render: r => `<span class="dot" style="background:${esc(r.color)}"></span>${esc(nm(r))}` }, { key: 'paid', label: T('مدفوعة', 'Paid'), render: r => r.paid ? '✓' : '—' }],
    [{ key: 'code', label: T('الرمز (يظهر في الكشف الشهري)', 'Code (shown in monthly sheet)'), required: true }, { key: 'name', label: T('الاسم (عربي)', 'Name (Arabic)'), required: true }, NAME_EN,
      { key: 'paid', label: T('مدفوعة', 'Paid'), type: 'checkbox', text: T('إجازة مدفوعة', 'Paid leave'), default: true }, { key: 'color', label: T('اللون', 'Color'), type: 'color', default: '#8e24aa' },
      { key: 'annual_days', label: T('الرصيد السنوي (أيام، 0 = بدون رصيد)', 'Yearly entitlement (days, 0 = not tracked)'), type: 'number', min: 0, default: 0 },
      { key: 'portal', label: T('في بوابة الموظف', 'In the employee portal'), type: 'checkbox', text: T('يمكن طلبها من البوابة', 'Can be requested from the portal'), default: true }]);
}
async function requestPage(c, { heading, endpoint, kind, columns, fields }) {
  title(c, heading);
  const g = grid(c, {
    columns: [{ key: 'emp_code', label: T('الرقم', 'ID') }, { key: 'name', label: T('الاسم', 'Name') }, { key: 'department', label: T('القسم', 'Department') }, ...columns,
      { key: 'reason', label: T('السبب', 'Reason') }, { key: 'status', label: T('الحالة', 'Status'), render: r => approvalBadge(r.status) }, { key: 'approver', label: T('المعتمد', 'Approver') }],
    fetch: serverFetch(endpoint),
    filters: [{ key: 'status', label: T('كل الحالات', 'All states'), type: 'select', options: Object.keys(APPROVAL).map(k => ({ value: k, label: T(...APPROVAL[k]) })) }],
    toolbar: [
      { label: T('إضافة', 'Add'), icon: 'add', cls: 'primary', perm: 'attendance.edit', action: () => requestForm(null) },
      { label: T('تعديل', 'Edit'), icon: 'edit', perm: 'attendance.edit', needSel: true, action: (s) => requestForm(s[0]) },
      { label: T('حذف', 'Delete'), icon: 'del', cls: 'danger', perm: 'attendance.edit', needSel: true, action: async (s) => { if (await confirmBox(T('حذف المحدد؟', 'Delete selected?'))) { for (const x of s) await DEL(`${endpoint}/${x.id}`); g.reload(); } } },
      { label: T('اعتماد', 'Approve'), icon: 'check', cls: 'success', perm: 'attendance.approve', needSel: true, action: async (s) => { await guard(() => POST(`/api/approvals/${kind}`, { ids: s.map(x => x.id), status: 'approved' }), T('تم الاعتماد', 'Approved')); g.reload(); } },
      { label: T('رفض', 'Reject'), icon: 'close', perm: 'attendance.approve', needSel: true, action: async (s) => { await guard(() => POST(`/api/approvals/${kind}`, { ids: s.map(x => x.id), status: 'rejected' }), T('تم الرفض', 'Rejected')); g.reload(); } },
    ],
  });
  const requestForm = async (row) => {
    const flds = await fields();
    const chooser = row ? null : employeeChooser([], { multiple: true });
    const body = h(`<div>${row ? `<p><b>${esc(row.emp_code)} — ${esc(row.name)}</b></p>` : ''}${formHtml(flds, row || {})}</div>`);
    if (chooser) $('form', body).prepend(chooser.el);
    dialog({ title: heading, body, buttons: [{ label: T('إلغاء', 'Cancel') }, { label: T('حفظ', 'Save'), cls: 'primary', action: async () => {
      const v = readForm(body, flds);
      if (row) await PUT(`${endpoint}/${row.id}`, v);
      else { const emps = chooser.get(); if (!emps.length) throw new Error(T('اختر موظفاً', 'Choose an employee')); for (const e of emps) await POST(endpoint, { ...v, employee_id: e.id }); }
      toast(T('تم الحفظ', 'Saved'), 'ok'); g.reload();
    } }] });
  };
}
const statusField = () => ({ key: 'status', label: T('الحالة', 'Status'), type: 'select', blank: false, readonly: !can('attendance.approve'), default: can('attendance.approve') ? 'approved' : 'pending', hint: can('attendance.approve') ? '' : T('تُحفظ الطلبات الجديدة بانتظار صاحب صلاحية الاعتماد', 'New requests await an authorized approver'), options: Object.keys(APPROVAL).map(k => ({ value: k, label: T(...APPROVAL[k]) })) });
function pageLeaves(c) {
  requestPage(c, { heading: T('الإجازات', 'Leave'), endpoint: '/api/leaves', kind: 'leaves',
    columns: [{ key: 'leave_type', label: T('النوع', 'Type') }, { key: 'start_time', label: T('من', 'From'), cls: 'ltr' }, { key: 'end_time', label: T('إلى', 'To'), cls: 'ltr' }],
    fields: async () => { const lk = await lookups(); return [
      { key: 'leave_type_id', label: T('نوع الإجازة', 'Leave type'), type: 'select', options: opts(lk.leave_types), required: true },
      statusField(),
      { key: 'start_time', label: T('من', 'From'), type: 'datetime', required: true, default: today() + ' 00:00' },
      { key: 'end_time', label: T('إلى', 'To'), type: 'datetime', required: true, default: today() + ' 23:59' },
      { key: 'reason', label: T('السبب', 'Reason'), type: 'textarea' }]; } });
}
function pageManual(c) {
  requestPage(c, { heading: T('البصمات اليدوية (نسيان البصمة)', 'Manual punches'), endpoint: '/api/manual-logs', kind: 'manual-logs',
    columns: [{ key: 'punch_time', label: T('الوقت', 'Time'), cls: 'ltr' }, { key: 'punch_state', label: T('الحالة', 'State'), render: r => esc(stateName(r.punch_state)) }],
    fields: async () => [{ key: 'punch_time', label: T('وقت البصمة', 'Punch time'), type: 'datetime', required: true, default: today() + ' 08:00' },
      { key: 'punch_state', label: T('نوع البصمة', 'Punch state'), type: 'select', blank: false, options: STATES().map(([v, l]) => ({ value: v, label: l })) }, statusField(),
      { key: 'reason', label: T('السبب', 'Reason'), type: 'textarea' }] });
}
function pageOvertime(c) {
  requestPage(c, { heading: T('طلبات العمل الإضافي', 'Overtime'), endpoint: '/api/overtimes', kind: 'overtimes',
    columns: [{ key: 'start_time', label: T('من', 'From'), cls: 'ltr' }, { key: 'end_time', label: T('إلى', 'To'), cls: 'ltr' }],
    fields: async () => [{ key: 'start_time', label: T('من', 'From'), type: 'datetime', required: true, default: today() + ' 16:00' },
      { key: 'end_time', label: T('إلى', 'To'), type: 'datetime', required: true, default: today() + ' 18:00' }, statusField(),
      { key: 'reason', label: T('السبب', 'Reason'), type: 'textarea' }] });
}

// ============================================================== calculated attendance
async function pageCalc(c) {
  title(c, T('نتائج الحضور', 'Attendance results'));
  const lk = await lookups();
  grid(c, {
    columns: [
      { key: 'date', label: T('التاريخ', 'Date'), cls: 'ltr' }, { key: 'emp_code', label: T('الرقم', 'ID') }, { key: 'name', label: T('الاسم', 'Name') }, { key: 'department', label: T('القسم', 'Department') },
      { key: 'timetable', label: T('الدوام', 'Timetable') },
      { key: 'clock_in', label: T('الدخول', 'In'), cls: 'ltr', render: r => esc(r.clock_in.slice(11)) }, { key: 'clock_out', label: T('الخروج', 'Out'), cls: 'ltr', render: r => esc(r.clock_out.slice(11)) },
      { key: 'late', label: T('تأخير', 'Late'), cls: 'num', render: r => hm(r.late) }, { key: 'early', label: T('مبكر', 'Early'), cls: 'num', render: r => hm(r.early) },
      { key: 'absent', label: T('مدة الغياب', 'Absent duration'), cls: 'num', render: r => hm(r.absent) },
      { key: 'worked', label: T('عمل', 'Worked'), cls: 'num', render: r => hm(r.worked) }, { key: 'ot', label: T('إضافي', 'OT'), cls: 'num', render: r => hm(r.ot) },
      { key: 'status', label: T('الحالة', 'Status'), render: r => statusBadge(r.status) + (r.holiday ? ` <small class="muted">${esc(r.holiday)}</small>` : '') + (r.leave_codes.length ? ` <small class="muted">${esc(r.leave_codes.join(','))}</small>` : '') },
      { key: 'punches', label: T('كل البصمات', 'Punches'), cls: 'ltr', render: r => esc(r.punches.join(' ')) },
      { key: 'explain', label: T('شرح الحساب', 'Calculation'), render: r => `<button class="btn small" data-act="explain">${esc(T('لماذا؟', 'Why?'))}</button>` },
    ],
    select: false, pageSize: 100,
    actions: { explain: r => attendanceExplanation(r.employee_id, r.date) },
    fetch: async ({ offset, limit, q, start, end, department_ids, status }) => {
      return GET('/api/attendance/daily?' + qs({ start: start || today(), end: end || start || today(), department_ids, status, q, offset, limit }));
    },
    filters: [{ key: 'start', label: T('من', 'From'), type: 'date', value: today() }, { key: 'end', label: T('إلى', 'To'), type: 'date', value: today() },
      { key: 'department_ids', label: T('كل الأقسام', 'All departments'), type: 'select', options: opts(lk.departments) },
      { key: 'status', label: T('كل الحالات', 'All states'), type: 'select', options: Object.keys(STATUS).map(k => ({ value: k, label: T(...STATUS[k]) })) }],
  });
}
async function renderEmpCalendar(el, empId, month) {
  const r = await GET(`/api/attendance/calendar/${empId}?month=${month}`);
  const first = new Date(r.month + '-01T00:00:00');
  const lead = (first.getDay() + 6) % 7;
  const s = r.summary || {};
  el.innerHTML = `<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px"><button class="btn small prev">‹</button><b class="ltr">${esc(r.month)}</b><button class="btn small next">›</button>
    <span class="muted" style="margin-inline-start:12px">${esc(T('حضور', 'Present'))}: ${s.present_days ?? 0} · ${esc(T('غياب كامل', 'Full absence'))}: ${s.absent_days ?? 0} · ${esc(T('غياب جزئي', 'Partial absence'))}: ${s.partial_absent_days ?? 0} · ${esc(T('مدة الغياب', 'Absent duration'))}: ${hm(s.absent_minutes) || '0:00'} · ${esc(T('تأخير', 'Late'))}: ${s.late_count ?? 0} (${hm(s.late)}) · ${esc(T('إضافي', 'OT'))}: ${hm(s.ot)}</span></div>
    <div class="calendar">${WEEKDAYS().map(w => `<div class="h">${esc(w)}</div>`).join('')}${'<div></div>'.repeat(lead)}
    ${r.days.map(d => `<div class="d ${['off', 'holiday'].includes(d.status) ? 'off' : ''}"><button class="btn link n" data-explain="${esc(d.date)}" title="${esc(T('شرح حساب هذا اليوم', 'Explain this day’s calculation'))}">${+d.date.slice(8)}</button>
      <div class="st-${d.status}">${esc(STATUS[d.status] ? T(...STATUS[d.status]) : '')}</div>
      ${d.timetable ? `<div class="muted">${esc(d.timetable)}</div>` : ''}<div class="ltr">${esc(d.punches.join(' '))}</div>
      ${d.absent ? `<div class="st-absent">${esc(T('مدة الغياب', 'Absent duration'))} ${hm(d.absent)}</div>` : ''}${d.late ? `<div class="st-late">${esc(T('تأخير', 'Late'))} ${hm(d.late)}</div>` : ''}${d.ot ? `<div class="st-holiday">${esc(T('إضافي', 'OT'))} ${hm(d.ot)}</div>` : ''}</div>`).join('')}</div>`;
  const shift = (n) => { const d = new Date(first); d.setMonth(d.getMonth() + n); renderEmpCalendar(el, empId, iso(d).slice(0, 7)); };
  $('.prev', el).onclick = () => shift(-1); $('.next', el).onclick = () => shift(1);
  $$('[data-explain]', el).forEach(b => b.onclick = () => attendanceExplanation(empId, b.dataset.explain).catch(e => toast(e.message, 'bad')));
}
function pageCalendar(c) {
  title(c, T('تقويم حضور الموظف', 'Employee attendance calendar'));
  const p = h(`<div class="panel"><div class="toolbar"><button class="btn primary">${icon('people')}${esc(T('اختر موظفاً', 'Choose employee'))}</button><b class="who"></b></div><div class="panel-body cal"><span class="muted">${esc(T('اختر موظفاً لعرض تقويمه الشهري', 'Choose an employee to see the month'))}</span></div></div>`);
  c.appendChild(p);
  $('button', p).onclick = async () => { const r = await pickEmployees({ multiple: false }); if (r && r[0]) { $('.who', p).textContent = `${r[0].emp_code} — ${r[0].name}`; renderEmpCalendar($('.cal', p), r[0].id, today().slice(0, 7)); } };
}
async function pageRules(c) {
  title(c, T('قواعد احتساب الحضور', 'Attendance rules'));
  const policies = await GET('/api/attendance/policies?limit=1');
  const s = policies.current.rules.snapshot;
  const f = [
    { section: T('البصمات', 'Punches') },
    { key: 'att.dup_punch_minutes', label: T('تجاهل البصمات المكررة خلال (دقيقة)', 'Ignore repeated punches within (min)'), type: 'number', min: 0 },
    { key: 'att.no_in', label: T('عند عدم وجود بصمة دخول', 'When there is no check-in'), type: 'select', blank: false, options: [{ value: 'incomplete', label: T('بصمة ناقصة (استثناء)', 'Missed punch (exception)') }, { value: 'absent', label: T('غياب', 'Absent') }, { value: 'late', label: T('تأخير بعدد دقائق', 'Late by N minutes') }] },
    { key: 'att.no_in_minutes', label: T('دقائق التأخير عند عدم وجود دخول', 'Late minutes for no check-in'), type: 'number', min: 0 },
    { key: 'att.no_out', label: T('عند عدم وجود بصمة خروج', 'When there is no check-out'), type: 'select', blank: false, options: [{ value: 'incomplete', label: T('بصمة ناقصة (استثناء)', 'Missed punch (exception)') }, { value: 'absent', label: T('غياب', 'Absent') }, { value: 'early', label: T('خروج مبكر بعدد دقائق', 'Early leave by N minutes') }] },
    { key: 'att.no_out_minutes', label: T('دقائق الخروج المبكر عند عدم وجود خروج', 'Early minutes for no check-out'), type: 'number', min: 0 },
    { key: 'att.late_full', label: T('بعد تجاوز فترة السماح', 'After the allowance is exceeded'), type: 'checkbox', text: T('احتساب كامل مدة التأخير (وليس ما زاد عن السماح فقط)', 'Count the full lateness (not only the excess)') },
    { section: T('العمل الإضافي', 'Overtime') },
    { key: 'att.ot_mode', label: T('طريقة احتساب الإضافي', 'Overtime mode'), type: 'select', blank: false, options: [{ value: 'auto', label: T('تلقائي من البصمات', 'Automatic from punches') }, { value: 'approval', label: T('بطلبات معتمدة فقط', 'Approved requests only') }, { value: 'both', label: T('الأكبر من الاثنين', 'Greater of both') }] },
    { key: 'att.ot_min_minutes', label: T('أقل مدة تُحتسب إضافي (دقيقة)', 'Minimum overtime (min)'), type: 'number', min: 0 },
    { key: 'att.ot_before_work', label: T('الحضور المبكر', 'Early arrival'), type: 'checkbox', text: T('يُحتسب عملاً إضافياً', 'Counts as overtime') },
    { key: 'att.dayoff_ot', label: T('العمل في الراحة والعطل', 'Work on days off / holidays'), type: 'checkbox', text: T('يُحتسب عملاً إضافياً', 'Counts as overtime') },
    { key: 'att.round_minutes', label: T('تقريب ساعات العمل والإضافي لأقل مضاعف لـ (دقيقة، 0 = بدون)', 'Round worked/OT down to (min, 0 = off)'), type: 'number', min: 0 },
    { section: T('الموظفون بدون جدول', 'Employees without a schedule') },
    { key: 'att.weekend', label: T('أيام الراحة', 'Days off'), type: 'multi', options: WEEKDAYS().map((w, i) => ({ value: i, label: w })) },
  ];
  const p = h('<div class="panel"><div class="panel-body policy-editor"></div></div>');
  c.appendChild(p);
  await renderPolicyEditor($('.policy-editor', p), 'rules', null, f, s, () => pageRulesReload());
  function pageRulesReload() { App.lookups = null; }
}

// ============================================================== reports
async function pageReports(c) {
  title(c, T('التقارير', 'Reports'));
  const list = await GET('/api/reports');
  c.appendChild(h(`<div class="report-cards">${list.map(r => `<a href="#/reports/${r.key}">${icon(r.kind === 'matrix' ? 'cal' : r.kind === 'summary' ? 'report' : 'list')}<div><b>${esc(App.lang === 'ar' ? r.title_ar : r.title_en)}</b></div></a>`).join('')}</div>`));
}
async function pageReport(c, key) {
  const list = await GET('/api/reports');
  const meta = list.find(r => r.key === key);
  if (!meta) { c.innerHTML = `<div class="alert">${esc(T('تقرير غير معروف', 'Unknown report'))}</div>`; return; }
  const lk = await lookups();
  title(c, App.lang === 'ar' ? meta.title_ar : meta.title_en);
  const p = h(`<div class="panel"><div class="filters">
    <div class="field"><label>${esc(T('من', 'From'))}</label><input class="inp" type="date" name="start" value="${meta.kind === 'punch' ? today() : monthStart()}"></div>
    <div class="field"><label>${esc(T('إلى', 'To'))}</label><input class="inp" type="date" name="end" value="${today()}"></div>
    <div class="field"><label>${esc(T('القسم', 'Department'))}</label><select class="inp" name="dep"><option value="">${esc(T('الكل', 'All'))}</option>${lk.departments.map(d => `<option value="${d.id}">${esc(d.name)}</option>`).join('')}</select></div>
    ${meta.kind === 'punch' ? `<div class="field"><label>${esc(T('الجهاز', 'Device'))}</label><select class="inp" name="dev"><option value="">${esc(T('الكل', 'All'))}</option>${lk.devices.map(d => `<option value="${esc(d.sn)}">${esc(d.name)}</option>`).join('')}</select></div>` : ''}
    <div class="field"><label>${esc(T('الموظفون', 'Employees'))}</label><button class="btn emp">${esc(T('الكل', 'All'))}</button></div>
    <div class="field"><label>${esc(T('بحث برقم أو اسم الموظف', 'Employee ID or name'))}</label><input class="inp" type="search" name="q"></div>
    ${['daily', 'late', 'absent'].includes(key) ? `<div class="field"><label>${esc(T('الحالة', 'Status'))}</label><select class="inp" name="status"><option value="">${esc(T('الكل', 'All'))}</option>${Object.keys(STATUS).map(k => `<option value="${k}">${esc(T(...STATUS[k]))}</option>`).join('')}</select></div>` : ''}
    <button class="btn primary run">${icon('refresh')}${esc(T('عرض', 'Show'))}</button>
    <span style="flex:1"></span>
    <button class="btn xl">${icon('download')}Excel</button><button class="btn csv">${icon('download')}CSV</button><select class="inp print-orientation" aria-label="${esc(T('اتجاه الطباعة', 'Print orientation'))}"><option value="auto">${esc(T('اتجاه تلقائي', 'Automatic orientation'))}</option><option value="portrait">${esc(T('عمودي', 'Portrait'))}</option><option value="landscape">${esc(T('أفقي', 'Landscape'))}</option></select><button class="btn prn" disabled>${icon('print')}${esc(T('طباعة التقرير كاملاً', 'Print full report'))}</button>
  </div><div class="report-task"></div><div class="out"><div class="empty" style="padding:30px">${esc(T('اختر الفترة ثم اضغط عرض', 'Choose a period and press Show'))}</div></div></div>`);
  c.appendChild(p);
  let emps = [], reportOffset = 0, reportLimit = 200, lastQuery = '', requestId = 0;
  $('.emp', p).onclick = async () => { const r = await pickEmployees({ preselect: emps }); if (r) { emps = r; $('.emp', p).textContent = emps.length ? `${emps.length} ${T('موظف', 'selected')}` : T('الكل', 'All'); } };
  const params = (fmt, paged = false) => qs({ start: $('[name=start]', p).value, end: $('[name=end]', p).value, department_ids: $('[name=dep]', p).value, device: $('[name=dev]', p)?.value, employee_ids: emps.map(e => e.id).join(','), q: $('[name=q]', p).value.trim(), status: $('[name=status]', p)?.value, lang: App.lang, fmt, ...(paged ? { offset: reportOffset, limit: reportLimit } : {}) });
  const run = async () => {
    const query = params('json'); if (query !== lastQuery) reportOffset = 0; lastQuery = query;
    const id = ++requestId;
    $('.prn', p).disabled = true;
    const out = $('.out', p); out.innerHTML = `<div class="empty" style="padding:30px">${esc(T('جارٍ الحساب...', 'Calculating...'))}</div>`;
    try {
      const t0 = performance.now();
      const r = await GET(`/api/reports/${key}?${params('json', true)}`);
      if (id !== requestId || !p.isConnected) return;
      const total = r.total ?? r.rows.length, from = total ? reportOffset + 1 : 0, to = Math.min(reportOffset + r.rows.length, total);
      const matrix = r.kind === 'matrix';
      const cell = (row) => r.columns.map(col => { const v = row[col.key] ?? ''; return `<td class="${matrix ? 'm-' + esc(v) : ''}">${col.key === 'status_label' ? `<span class="badge ${STATUS[row.status]?.[2] || ''}">${esc(v)}</span>` : esc(Array.isArray(v) ? v.join(' ') : v)}</td>`; }).join('');
      out.innerHTML = `<div class="muted rep-meta" style="padding:8px 12px">${esc(r.title)} · <span class="ltr">${esc(r.start)} → ${esc(r.end)}</span> · <b>${from}–${to} / ${total}</b> ${esc(T('سجل', 'rows'))} · <span class="ltr">${((performance.now() - t0) / 1000).toFixed(1)}s</span></div>
        <div class="muted no-print rep-note">${esc(T('الطباعة والتصدير يشملان كل النتائج المطابقة للفلاتر. التصدير يعمل في الخلفية؛ والكشوف العريضة تُقسّم للطباعة مع تكرار هوية الموظف.', 'Printing and export include all matching results. Export runs in the background; wide print tables are split into column sections with employee identity repeated.'))}</div>
        ${matrix ? `<div class="muted" style="padding:0 12px 8px">${Object.entries(r.legend || {}).map(([s, l]) => `<b>${esc(s)}</b>=${esc(l)}`).join(' · ')}</div>` : ''}
        <div class="grid-wrap" style="max-height:calc(100vh - 300px)"><table class="grid ${matrix ? 'matrix' : ''}"><thead><tr>${r.columns.map(col => `<th>${esc(col.label)}</th>`).join('')}</tr></thead>
        <tbody>${r.rows.length ? r.rows.map(row => `<tr>${cell(row)}</tr>`).join('') : `<tr><td class="empty" colspan="${r.columns.length}">${esc(T('لا توجد بيانات', 'No data'))}</td></tr>`}</tbody></table></div>
        <div class="pager"><span>${from}–${to} / ${total}</span><span class="btns"><button class="btn small prev" aria-label="${esc(T('السابق', 'Previous'))}" ${reportOffset ? '' : 'disabled'}>‹</button>
        <select class="inp size" aria-label="${esc(T('سجلات في الصفحة', 'Rows per page'))}">${[100, 200, 500, 1000].map(n => `<option value="${n}" ${n === reportLimit ? 'selected' : ''}>${n}</option>`).join('')}</select>
        <button class="btn small next" aria-label="${esc(T('التالي', 'Next'))}" ${to < total ? '' : 'disabled'}>›</button></span></div>`;
      $('.prev', out).onclick = () => { reportOffset = Math.max(0, reportOffset - reportLimit); run(); };
      $('.next', out).onclick = () => { reportOffset += reportLimit; run(); };
      $('.size', out).onchange = (e) => { reportLimit = +e.target.value; reportOffset = 0; run(); };
      $('.prn', p).disabled = false;
    } catch (e) { if (id === requestId) out.innerHTML = `<div class="alert">${esc(e.message)}</div>`; }
  };
  $('.run', p).onclick = () => { reportOffset = 0; run(); };
  $('[name=q]', p).onkeydown = (e) => { if (e.key === 'Enter') { reportOffset = 0; run(); } };
  const queueExport = (fmt) => reportExportTask($('.report-task', p), { key, ...Object.fromEntries(new URLSearchParams(params(fmt))) });
  $('.xl', p).onclick = () => queueExport('xlsx');
  $('.csv', p).onclick = () => queueExport('csv');
  $('.prn', p).onclick = () => { const w = window.open(`/api/reports/${key}/print?${params()}&orientation=${$('.print-orientation', p).value}`, '_blank'); if (w) w.opener = null; else toast(T('اسمح بالنوافذ المنبثقة لفتح الطباعة', 'Allow pop-up windows to open print preview'), 'bad'); };
  run();
}

// ============================================================== system
async function pageSettings(c) {
  title(c, T('إعدادات النظام', 'System settings'));
  const s = await GET('/api/settings');
  const lk = await lookups(true);
  const f = [
    { section: T('الشركة', 'Company') },
    { key: 'company.name_ar', label: T('اسم الشركة (عربي)', 'Company name (Arabic)') },
    { key: 'company.name', label: T('اسم الشركة (إنجليزي)', 'Company name (English)') },
    { section: T('اتصال الأجهزة (ADMS)', 'Device communication (ADMS)') },
    { key: 'adms.auto_add', label: T('الأجهزة الجديدة', 'New devices'), type: 'checkbox', text: T('إضافة أي جهاز يتصل تلقائياً', 'Automatically add any device that connects') },
    { key: 'adms.default_area', label: T('المنطقة الافتراضية للأجهزة الجديدة', 'Default area for new devices'), type: 'select', options: opts(lk.areas) },
    { key: 'adms.timezone', label: T('المنطقة الزمنية للأجهزة (ساعات، فارغ = توقيت هذا الحاسوب)', 'Device time zone (hours, empty = this PC)'), type: 'number', min: -12, max: 14 },
    { key: 'adms.sync_bio', label: T('توزيع القوالب', 'Template distribution'), type: 'checkbox', text: T('إرسال الوجه/البصمة المسجلة على جهاز إلى باقي أجهزة المنطقة', 'Send faces/fingerprints enrolled on one device to the other devices of the area') },
    { key: 'adms.read_bio_on_punch', label: T('استكمال القوالب عند استقبال حركة جديدة', 'Fetch missing templates on new movements'), type: 'checkbox', text: T('طلب القوالب والصور الناقصة تلقائياً؛ يمكن تركه مطفأ للحضور فقط. تُطلب أسماء الموظفين المجهولين عند الحاجة.', 'Automatically request missing templates and photos; leave off for attendance only. Unknown employee names are requested when needed.') },
    { key: 'adms.upload_photos', label: T('صور الحضور', 'Attendance photos'), type: 'checkbox', text: T('طلب صور البصمة من الأجهزة', 'Ask devices to upload punch photos') },
    { section: T('الاكتشاف التلقائي والاتصال المباشر (المنفذ 4370)', 'Auto-discovery and direct link (port 4370)') },
    { key: 'discovery.enabled', label: T('اكتشاف الأجهزة', 'Discover terminals'), type: 'checkbox', text: T('البحث دورياً عن أجهزة جديدة في الشبكة وإضافتها تلقائياً', 'Periodically search the network for new terminals and add them') },
    { key: 'discovery.networks', label: T('الشبكات (فارغ = شبكة هذا الحاسوب)', 'Networks (empty = this PC\'s network)'), placeholder: '10.0.0.0/24, 10.28.65.200-10.28.65.254' },
    { key: 'discovery.minutes', label: T('البحث كل (دقيقة)', 'Search every (minutes)'), type: 'number', min: 5 },
    { key: 'discovery.comm_keys', label: T('مفاتيح الاتصال المجربة', 'Comm keys to try'), placeholder: '0, 123456' },
    { key: 'tcp.poll_minutes', label: T('إعادة قراءة كاملة كل (دقيقة) — الحالة والأعداد والحركات الجديدة تتحدث كل دقيقة', 'Full re-read every (minutes) — state, counters and new punches update every minute'), type: 'number', min: 1 },
    { key: 'tcp.read_bio', label: T('القوالب الحيوية', 'Biometric templates'), type: 'checkbox', text: T('قراءة البصمات والوجوه مع كل قراءة', 'Read fingerprints and faces on every read') },
    { key: 'tcp.write_back', label: T('إذن الكتابة ومزامنة الأجهزة', 'Device write and synchronization permission'), type: 'checkbox', text: T('السماح بتعديل الأجهزة وإرسال الموظفين والقوالب عبر ADMS والاتصال المباشر', 'Allow device changes and employee/template synchronization over ADMS and direct links'), hint: T('يبدأ التشغيل بالقراءة فقط. فعّل الإذن بعد التحقق من سعة كل جهاز والمناطق.', 'New installations start read-only. Enable writing after verifying each terminal’s capacity and areas.') },
    { section: T('بوابة الموظف', 'Employee portal') },
    { key: 'portal.enabled', label: T('البوابة', 'Portal'), type: 'checkbox', text: T('يدخل الموظفون من جوالاتهم على /me', 'Employees sign in from their phones at /me') },
    { key: 'portal.requests', label: T('الطلبات', 'Requests'), type: 'checkbox', text: T('السماح بطلبات الإجازة وتصحيح البصمة والعمل الإضافي', 'Allow leave, punch-correction and overtime requests') },
    { key: 'portal.correction_days', label: T('أقصى عمر لتصحيح البصمة (يوم)', 'Oldest punch that can be corrected (days)'), type: 'number', min: 1 },
    { section: T('النسخ الاحتياطي التلقائي', 'Automatic backup') },
    { key: 'backup.hour', label: T('ساعة النسخ اليومي (0-23)', 'Daily backup hour (0-23)'), type: 'number', min: 0, max: 23 },
    { key: 'backup.keep', label: T('عدد النسخ المحفوظة', 'Backups to keep'), type: 'number', min: 1 },
    { key: 'backup.offsite_path', label: T('مجلد النسخ خارج الجهاز أو على الشبكة', 'External or network backup folder'), placeholder: T('مثال: E:\\AttendanceBackups أو مسار شبكة', 'e.g. E:\\AttendanceBackups or a network path'), hint: T('مجلد متاح لحساب تشغيل البرنامج. اتركه فارغاً لإيقاف النسخة الخارجية.', 'The folder must be accessible to the account running the server. Blank disables the external copy.'), full: true },
    { key: 'backup.verify_after', label: T('التحقق من سلامة النسخ', 'Verify backup integrity'), type: 'checkbox', text: T('اختبار كل نسخة جديدة باستعادة مستقلة دون تغيير سجلات التشغيل', 'Test each new backup with an isolated restore that preserves live records') },
    { section: T('التوقيت والدقة', 'Time and accuracy') },
    { key: 'time.zone', label: T('المنطقة الزمنية المعتمدة', 'Attendance time zone'), placeholder: 'Asia/Hebron', hint: T('اسم منطقة IANA مثل Asia/Hebron لمعالجة التوقيت الصيفي. فارغ = توقيت نظام التشغيل.', 'An IANA name such as Asia/Hebron accounts for daylight-saving changes. Blank uses the operating system time zone.') },
    { key: 'storage.synchronous', label: T('حماية كتابة قاعدة البيانات', 'Database durability'), type: 'select', blank: false, options: [{ value: 'NORMAL', label: T('NORMAL — الأداء الافتراضي', 'NORMAL — default performance') }, { value: 'FULL', label: T('FULL — حماية أعلى عند انقطاع الكهرباء', 'FULL — stronger power-loss protection') }], hint: T('تغيير هذا الخيار يتطلب إعادة تشغيل البرنامج حتى يطبق على كل الاتصالات.', 'Restart the application after changing this option so every connection uses it.') },
    { section: T('حفظ دفعات الاستقبال والمرفوضات', 'Intake retention and rejected records') },
    { key: 'intake.retention_days', label: T('مدة حفظ الدفعات (يوم)', 'Batch retention (days)'), type: 'number', min: 1, max: 3650 },
    { key: 'intake.max_batches', label: T('أقصى عدد دفعات محفوظة', 'Maximum retained batches'), type: 'number', min: 1, max: 1000000 },
    { key: 'intake.max_bytes', label: T('أقصى مساحة نصوص الدفعات (بايت)', 'Maximum raw batch storage (bytes)'), type: 'number', min: 1048576, hint: T('الافتراضي 134217728 بايت (128 ميغابايت). هذه حدود للأرشيف الخام؛ سجلات الحضور المقبولة تبقى محفوظة.', 'Default: 134217728 bytes (128 MB). These limits apply to the raw intake archive; accepted attendance records remain stored.') },
  ];
  const srv = s._server;
  const net = await GET('/api/system/network');
  const addrLabel = (ip) => ip === '0.0.0.0' ? T('كل الشبكات (مستحسن)', 'All networks (recommended)') : ip === '127.0.0.1' ? T('هذا الحاسوب فقط', 'This PC only') : ip;
  const adminMode = !s['security.admin_from'] ? 'any' : s['security.admin_from'] === 'local' ? 'local' : 'list';
  const p = h(`<div><div class="panel"><div class="panel-head"><h3>${esc(T('عنوان الخادم والمنافذ', 'Server address and ports'))}</h3></div><div class="panel-body">
      ${net.fixed.length ? `<div class="alert info">${esc(T('بعض القيم مثبّتة من سطر تشغيل البرنامج (run.py --web/--adms/--portal/--host) وتتقدّم على ما يُحفظ هنا: ', 'Some values are fixed on the command line (run.py --web/--adms/--portal/--host) and win over what is saved here: '))}<bdi>${esc(net.fixed.join(', '))}</bdi></div>` : ''}
      ${net.error ? `<div class="alert">${esc(T('لم يمكن فتح العنوان/المنفذ الجديد فأُعيدت القيم السابقة: ', 'The new address/port could not be opened, so the previous ones were kept: '))}<bdi>${esc(net.error)}</bdi></div>` : ''}
      <div class="form net-form">
        <div class="field"><label>${esc(T('عنوان IP للخادم', 'Server IP address'))}</label><select class="inp" name="host">${net.addresses.map(ip => `<option value="${esc(ip)}" ${ip === net.host ? 'selected' : ''}>${esc(addrLabel(ip))}</option>`).join('')}</select></div>
        <div class="field"><label>${esc(T('منفذ واجهة البرنامج', 'Program (web) port'))}</label><input class="inp ltr" type="number" min="1" max="65535" name="web_port" value="${net.web_port}"></div>
        <div class="field"><label>${esc(T('منفذ بوابة الموظف (0 = إيقاف)', 'Employee portal port (0 = off)'))}</label><input class="inp ltr" type="number" min="0" max="65535" name="portal_port" value="${net.portal_port}"></div>
        <div class="field"><label>${esc(T('منافذ استقبال الأجهزة', 'Terminal (ADMS) ports'))}</label><input class="inp ltr" name="adms_ports" value="${esc(net.adms_ports.join(', '))}" placeholder="90, 8081"></div>
      </div>
      <p class="muted small">${esc(T('عند الحفظ يعيد البرنامج تشغيل نفسه على القيم الجديدة خلال ثوانٍ (دون إغلاقه)، وإن تعذّر فتحها يعود للقيم السابقة تلقائياً. منفذ الأجهزة يجب أن يطابق ما في إعداد «الخادم السحابي» على الأجهزة.', 'Saving restarts the server on the new values within seconds (without closing it); if they cannot be opened it goes back to the previous ones. The terminal port must match the “Cloud server” setting on the terminals.'))}</p>
      <div class="kv small"><div>${esc(T('الإصدار', 'Version'))}</div><div>${esc(BRAND().name)} ${esc(srv.version)}</div><div>${esc(T('مجلد البيانات', 'Data folder'))}</div><div><bdi>${esc(srv.data_dir)}</bdi></div></div>
    </div><div class="toolbar" style="border-top:1px solid var(--line);border-bottom:0"><button class="btn primary net-save">${icon('check')}${esc(T('حفظ وإعادة التشغيل', 'Save and restart'))}</button><span class="net-msg muted"></span></div></div>
    <div class="panel"><div class="panel-head"><h3>${esc(T('من يستطيع فتح واجهة الإدارة؟', 'Who can open the admin interface?'))}</h3></div><div class="panel-body">
      <div class="seg" role="radiogroup">
        <label><input type="radio" name="adm" value="any" ${adminMode === 'any' ? 'checked' : ''}> ${esc(T('أي جهاز في الشبكة', 'Any device on the network'))}</label>
        <label><input type="radio" name="adm" value="list" ${adminMode === 'list' ? 'checked' : ''}> ${esc(T('أجهزة محددة فقط', 'Only chosen devices'))}</label>
        <label><input type="radio" name="adm" value="local" ${adminMode === 'local' ? 'checked' : ''}> ${esc(T('هذا الحاسوب فقط', 'This PC only'))}</label>
      </div>
      <textarea class="inp ltr adm-list" rows="2" placeholder="10.0.0.15, 10.0.0.20-10.0.0.30, 10.0.5.0/24" ${adminMode === 'list' ? '' : 'hidden'}>${esc(adminMode === 'list' ? s['security.admin_from'] : '')}</textarea>
      <p class="muted small">${esc(T('الموظفون لا يحتاجون واجهة الإدارة: يدخلون من بوابة الموظف فقط. الأجهزة وبوابة الموظف تعمل دائماً، وهذا الحاسوب مسموح دائماً حتى لا تُقفل على نفسك.', 'Employees never need the admin interface: they use the employee portal only. Terminals and the portal always work, and this PC is always allowed so you cannot lock yourself out.'))} ${esc(T('جهازك الآن:', 'Your device now:'))} <bdi class="adm-me"></bdi></p>
    </div><div class="toolbar" style="border-top:1px solid var(--line);border-bottom:0"><button class="btn primary adm-save">${icon('shield')}${esc(T('حفظ', 'Save'))}</button></div></div>
    <div class="panel"><div class="panel-body settings-form">${formHtml(f, s)}</div><div class="toolbar" style="border-top:1px solid var(--line);border-bottom:0"><button class="btn primary settings-save">${icon('check')}${esc(T('حفظ', 'Save'))}</button><span class="relay-res muted"></span></div></div></div>`);
  c.appendChild(p);
  enhanceSettings(p, f, s);
  $('.settings-save', p).onclick = async () => {
    const button = $('.settings-save', p), msg = $('.relay-res', p); button.disabled = true;
    try {
      const v = readForm($('.settings-form', p), f);
      if (v['tcp.write_back'] && !s['tcp.write_back'] && !await confirmBox(T('سيُسمح للخادم بتعديل الأجهزة وإرسال بيانات تسجيل الموظفين والقوالب. هل تريد تفعيل الكتابة؟', 'The server will be allowed to change terminals and send employee enrollment data and templates. Enable device writing?'), { danger: false, okLabel: T('تفعيل الكتابة', 'Enable device writing') })) return;
      if (v['adms.timezone'] === '') v['adms.timezone'] = null; if (v['adms.default_area'] !== '') v['adms.default_area'] = +v['adms.default_area'];
      await PUT('/api/settings', v); Object.assign(s, v); App.settings = s;
      msg.textContent = '✓ ' + T('حُفظت الإعدادات', 'Settings saved') + ' · ' + new Date().toLocaleTimeString(); msg.setAttribute('role', 'status'); toast(T('تم الحفظ', 'Saved'), 'ok');
      updateSettingsSummary(p, s);
    } catch (e) { msg.textContent = T('لم تُحفظ الإعدادات: ', 'Settings were not saved: ') + e.message; toast(e.message, 'bad'); } finally { button.disabled = false; }
  };
  // server address and ports: save, wait for the restart, then continue on the new address
  $('.net-save', p).onclick = async () => {
    const form = $('.net-form', p), msg = $('.net-msg', p);
    const v = { host: $('[name=host]', form).value, web_port: +$('[name=web_port]', form).value, portal_port: +$('[name=portal_port]', form).value, adms_ports: $('[name=adms_ports]', form).value };
    if (!await confirmBox(T('سيعيد البرنامج تشغيل نفسه على هذه القيم. الأجهزة المتصلة تعود خلال ثوانٍ. متابعة؟', 'The server restarts on these values; connected terminals come back within seconds. Continue?'), { danger: false })) return;
    try { await PUT('/api/system/network', v); } catch (e) { msg.textContent = '✗ ' + e.message; return; }
    const host = v.host === '0.0.0.0' ? location.hostname : (v.host === '127.0.0.1' ? '127.0.0.1' : v.host);
    const base = `${location.protocol}//${host.includes(':') ? `[${host}]` : host}:${v.web_port}`;
    msg.textContent = T('جارٍ إعادة التشغيل…', 'Restarting…');
    for (let i = 0; i < 40; i++) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const r = await fetch(base + '/api/ping', { cache: 'no-store' });
        if (r.ok) { msg.textContent = '✓ ' + T('يعمل على العنوان الجديد', 'Running on the new address'); setTimeout(() => { location.href = base + '/#/system/settings'; }, 600); return; }
      } catch (e) { /* not up yet */ }
    }
    msg.textContent = T('لم يرد الخادم على العنوان الجديد؛ افتح الصفحة على العنوان السابق — إن تعذّر الفتح يعود البرنامج للقيم السابقة تلقائياً.', 'No answer on the new address; open the previous one — the server goes back to the previous values if the new ones fail.');
  };
  // admin access
  const admUpdate = () => { $('.adm-list', p).hidden = $('[name=adm]:checked', p).value !== 'list'; };
  $$('[name=adm]', p).forEach(r => r.onchange = admUpdate);
  GET('/api/system/whoami').then(r => { $('.adm-me', p).textContent = r.ip; }).catch(() => {});
  $('.adm-save', p).onclick = () => {
    const mode = $('[name=adm]:checked', p).value;
    const rule = mode === 'any' ? '' : mode === 'local' ? 'local' : $('.adm-list', p).value.trim();
    return guard(() => PUT('/api/settings', { 'security.admin_from': rule }), T('تم الحفظ', 'Saved'));
  };
}
// ============================================================== alerts
const ALERT_RULES = () => ({
  late: { icon: 'clock', color: '#fab219', t: T('التأخير', 'Late arrival'), d: T('عند أول بصمة متأخرة للموظف', 'When an employee\'s first punch is late') },
  absent: { icon: 'exit', color: '#d03b3b', t: T('الغياب', 'Absence'), d: T('لا توجد بصمة حتى الساعة المحددة', 'No punch by the set time') },
  missing_out: { icon: 'hand', color: '#eb6834', t: T('بصمة خروج ناقصة', 'Missing check-out'), d: T('دخل ولم يبصم خروجاً حتى الساعة المحددة', 'Checked in but no check-out by the set time') },
  device_offline: { icon: 'device', color: '#d03b3b', t: T('انقطاع جهاز', 'Terminal offline'), d: T('جهاز لم يتصل منذ مدة', 'A terminal has been silent for a while') },
  device_online: { icon: 'device', color: '#0ca30c', t: T('عودة جهاز', 'Terminal back online'), d: T('عاد جهاز منقطع للاتصال', 'A silent terminal is talking again') },
  request_new: { icon: 'leave', color: '#2a78d6', t: T('طلب جديد', 'New request'), d: T('طلب إجازة أو تصحيح أو عمل إضافي من بوابة الموظف', 'Leave, correction or overtime request from the portal') },
  request_decided: { icon: 'check', color: '#4a3aa7', t: T('الرد على الطلب', 'Request answered'), d: T('إبلاغ الموظف بالموافقة أو الرفض', 'Tell the employee it was approved or rejected') },
  daily_summary: { icon: 'dashboard', color: '#0e8f86', t: T('ملخص يومي', 'Daily summary'), d: T('أرقام اليوم للمدراء في وقت محدد', 'Today\'s numbers to managers at a set time') },
  weekly_report: { icon: 'report', color: '#2a78d6', t: T('تقرير أسبوعي', 'Weekly report'), d: T('ملف Excel لملخص الأسبوع بالبريد', 'An Excel summary of the week by e-mail') },
});
const CHANNEL_NAMES = () => ({ inapp: T('داخل البرنامج', 'In-app'), email: T('البريد', 'E-mail'), telegram: 'Telegram', webhook: 'Webhook' });

function toggle(on, attrs = '') { return `<label class="switch" ${attrs}><input type="checkbox" ${on ? 'checked' : ''}><i></i></label>`; }

async function pageAlerts(c) {
  let cfg = await GET('/api/alerts/config');
  const S = () => cfg.settings;
  const head = h(`<div class="al-hero"><div class="al-hero-ic">${icon('bell')}</div>
      <div class="grow"><h1>${esc(T('التنبيهات', 'Alerts'))}</h1><p class="muted"></p></div>
      <span class="al-saved muted"></span>${toggle(S()['alerts.enabled'], 'id="alOn"')}</div>`);
  c.appendChild(head);
  const saved = $('.al-saved', head);
  const flash = () => { saved.textContent = '✓ ' + T('تم الحفظ', 'Saved'); saved.classList.add('show'); clearTimeout(flash.t); flash.t = setTimeout(() => saved.classList.remove('show'), 1600); };
  const save = async (body) => { cfg = await guard(() => PUT('/api/alerts/config', body)); flash(); paintChannels(); paintHero(); };
  const paintHero = () => {
    const on = S()['alerts.enabled'];
    $('p', head).textContent = on ? T('تصل التنبيهات للموظفين في البوابة وللمدراء هنا وعلى القنوات المفعّلة.', 'Employees get alerts in the portal, managers here and on the channels you turn on.')
      : T('التنبيهات متوقفة. فعّلها من المفتاح.', 'Alerts are off. Turn them on with the switch.');
    c.classList.toggle('al-off', !on);
  };
  $('#alOn input', head).onchange = (e) => save({ settings: { 'alerts.enabled': e.target.checked } });

  // ---- channels
  c.appendChild(h(`<h3 class="al-h">${esc(T('القنوات', 'Channels'))}</h3>`));
  const ch = h('<div class="al-channels"></div>'); c.appendChild(ch);
  const CH = [
    { key: 'inapp', icon: 'bell', color: '#0e8f86', name: T('داخل البرنامج والبوابة', 'In-app & portal'), ready: () => true,
      sub: () => T('دائماً مفعّل — الجرس أعلى الشاشة وصندوق الموظف', 'Always on — the bell up top and the employee inbox') },
    { key: 'email', icon: 'mail', color: '#2a78d6', name: T('البريد الإلكتروني', 'E-mail'), ready: () => !!S()['alerts.smtp_host'],
      sub: () => S()['alerts.smtp_host'] ? `${S()['alerts.smtp_from'] || S()['alerts.smtp_user'] || S()['alerts.smtp_host']}` : T('غير مُعد — اضغط للإعداد', 'Not set up — tap to set up') },
    { key: 'telegram', icon: 'send', color: '#229ed9', name: 'Telegram', ready: () => !!S()['alerts.telegram_token'],
      sub: () => S()['alerts.telegram_token'] ? (App.user.telegram_chat_id ? T('مربوط بحسابك', 'Linked to your account') : T('البوت جاهز — اربط حسابك', 'Bot ready — link your account')) : T('غير مُعد — اضغط للإعداد', 'Not set up — tap to set up') },
    { key: 'webhook', icon: 'link', color: '#4a3aa7', name: T('Webhook (واتساب / SMS)', 'Webhook (WhatsApp / SMS)'), ready: () => !!S()['alerts.webhook_url'],
      sub: () => S()['alerts.webhook_url'] ? S()['alerts.webhook_url'] : T('غير مُعد — لربط بوابة رسائل', 'Not set up — to plug in a messaging gateway') },
    { key: 'quiet', icon: 'moon', color: '#475569', name: T('ساعات الهدوء', 'Quiet hours'), ready: () => !!(S()['alerts.quiet_from'] && S()['alerts.quiet_to']),
      sub: () => S()['alerts.quiet_from'] && S()['alerts.quiet_to'] ? `${S()['alerts.quiet_from']} – ${S()['alerts.quiet_to']} · ${T('داخل البرنامج فقط', 'in-app only')}` : T('بدون — الرسائل تُرسل في أي وقت', 'None — messages go out any time') },
  ];
  const paintChannels = () => {
    ch.innerHTML = CH.map(x => `<button class="al-ch ${x.ready() ? 'on' : ''}" data-k="${x.key}">
        <span class="al-ic" style="--c:${x.color}">${icon(x.icon)}</span>
        <span class="grow"><b>${esc(x.name)}</b><small>${esc(x.sub())}</small></span>
        <span class="al-state">${x.ready() ? '●' : '○'}</span></button>`).join('');
    $$('.al-ch', ch).forEach(b => b.onclick = () => b.dataset.k !== 'inapp' && channelSheet(b.dataset.k));
  };
  async function testIt(channel, target, out) {
    out.className = 'al-test muted'; out.textContent = T('جارٍ الإرسال...', 'Sending...');
    const r = await POST('/api/alerts/test', { channel, target });
    out.className = 'al-test ' + (r.ok ? 'ok' : 'bad');
    out.textContent = r.ok ? T('✓ وصلت رسالة التجربة', '✓ Test message sent') : '✗ ' + r.error;
  }
  function channelSheet(key) {
    const s = S();
    let f = [], extra = '';
    if (key === 'email') f = [
      { key: 'alerts.smtp_host', label: T('خادم SMTP', 'SMTP server'), placeholder: 'smtp.gmail.com' },
      { key: 'alerts.smtp_port', label: T('المنفذ', 'Port'), type: 'number' },
      { key: 'alerts.smtp_security', label: T('التشفير', 'Security'), type: 'select', blank: false, options: [{ value: 'starttls', label: 'STARTTLS (587)' }, { value: 'ssl', label: 'SSL (465)' }, { value: 'none', label: T('بدون', 'None') }] },
      { key: 'alerts.smtp_user', label: T('اسم المستخدم', 'User name'), placeholder: 'hr@company.com' },
      { key: 'alerts.smtp_password', label: T('كلمة المرور', 'Password'), type: 'password', hint: T('لـ Gmail استخدم «كلمة مرور التطبيقات»', 'For Gmail use an app password') },
      { key: 'alerts.smtp_from', label: T('المرسل', 'From'), placeholder: 'Attendance <hr@company.com>' },
      { key: 'alerts.email_employees', label: T('الموظفون', 'Employees'), type: 'checkbox', text: T('إرسال تنبيهات الموظف لبريده أيضاً', 'Also e-mail employees their own alerts') },
      { key: 'alerts.language', label: T('لغة القوائم البريدية', 'Language for e-mail lists'), type: 'select', blank: false, options: [{ value: 'ar', label: 'العربية' }, { value: 'en', label: 'English' }] },
    ];
    if (key === 'telegram') {
      f = [{ key: 'alerts.telegram_token', label: T('رمز البوت (Bot token)', 'Bot token'), type: 'password', placeholder: '123456:ABC...', full: true,
        hint: T('أنشئ بوتاً من @BotFather في تيليجرام وانسخ الرمز هنا.', 'Create a bot with @BotFather in Telegram and paste its token here.') },
      { key: 'chat', label: T('معرّف محادثتك (chat id)', 'Your chat id'), placeholder: '123456789', full: true,
        hint: T('افتح البوت واضغط Start، ثم أرسل أي رسالة إلى @userinfobot ليعطيك رقمك. كل مدير يربط رقمه من هنا.', 'Open your bot and press Start, then message @userinfobot to get your number. Each manager links their own number here.') }];
    }
    if (key === 'webhook') {
      f = [{ key: 'alerts.webhook_url', label: T('الرابط', 'URL'), placeholder: 'https://gateway.example.com/hook', full: true,
        hint: T('يُرسل POST بصيغة JSON: rule, title, body, key — لربط واتساب أو SMS عبر أي مزود.', 'Sends a JSON POST: rule, title, body, key — to plug WhatsApp or SMS through any provider.') }];
    }
    if (key === 'quiet') {
      f = [{ key: 'alerts.quiet_from', label: T('من', 'From'), type: 'time' }, { key: 'alerts.quiet_to', label: T('إلى', 'To'), type: 'time' }];
      extra = `<p class="muted">${esc(T('خلالها تُحفظ التنبيهات داخل البرنامج والبوابة فقط ولا تُرسل رسائل بريد أو تيليجرام.', 'During these hours alerts stay in-app only; no e-mail or Telegram goes out.'))}</p>`;
    }
    const data = { ...s, chat: App.user.telegram_chat_id || '', id: 1 };
    const testable = key !== 'quiet';
    const body = h(`<div>${formHtml(f, data)}${extra}${testable ? `<div class="al-testrow">
        ${key === 'email' ? `<input class="inp grow" data-target placeholder="${esc(T('بريد للتجربة', 'Address to test'))}" value="${esc(App.user.email || '')}">` : ''}
        <button class="btn" data-test>${icon('send')}${esc(T('حفظ وإرسال تجربة', 'Save & send a test'))}</button></div><div class="al-test"></div>` : ''}</div>`);
    const store = async () => {
      const v = readForm(body, f);
      if (key === 'telegram') { await PUT('/api/me-user/telegram', { chat_id: v.chat }); App.user.telegram_chat_id = v.chat; delete v.chat; }
      await save({ settings: v });
    };
    const d = dialog({ title: CH.find(x => x.key === key).name, body, buttons: [
      ...(key !== 'telegram' && f.length ? [{ label: T('إيقاف', 'Turn off'), cls: 'danger', action: async () => { const v = {}; f.forEach(x => { if (x.type !== 'checkbox' && x.key !== 'alerts.language' && x.key !== 'alerts.smtp_port' && x.key !== 'alerts.smtp_security') v[x.key] = ''; }); await save({ settings: v }); } }] : []),
      { label: T('إلغاء', 'Cancel') }, { label: T('حفظ', 'Save'), cls: 'primary', icon: 'check', action: store }] });
    const tb = $('[data-test]', body);
    if (tb) tb.onclick = async () => {
      tb.disabled = true;
      try {
        await store();
        const target = key === 'email' ? $('[data-target]', body).value.trim() : key === 'telegram' ? (App.user.telegram_chat_id || '') : '';
        if (key === 'email' && !target) throw new Error(T('اكتب بريداً للتجربة', 'Type an address to test'));
        if (key === 'telegram' && !target) throw new Error(T('اكتب معرّف محادثتك أولاً', 'Type your chat id first'));
        await testIt(key, target, $('.al-test', body));
      } catch (e) { const o = $('.al-test', body); o.className = 'al-test bad'; o.textContent = '✗ ' + e.message; }
      finally { tb.disabled = false; }
    };
    return d;
  }

  // ---- rules
  c.appendChild(h(`<h3 class="al-h">${esc(T('متى ننبّه؟', 'When to alert'))}</h3>`));
  const rl = h('<div class="al-rules"></div>'); c.appendChild(rl);
  const RU = ALERT_RULES();
  const days = WEEKDAYS();
  const queue = debounce((k, v) => save({ rules: { [k]: v } }), 500);
  for (const [k, meta] of Object.entries(RU)) {
    const r = cfg.rules[k];
    const opts = [];
    if ('min_minutes' in r) opts.push(`<label class="al-opt">${esc(T('بعد', 'After'))} <input class="inp" type="number" min="0" data-f="min_minutes" value="${r.min_minutes}"> ${esc(T('دقيقة تأخير', 'min late'))}</label>`);
    if ('after_minutes' in r) opts.push(`<label class="al-opt">${esc(T('بعد', 'After'))} <input class="inp" type="number" min="1" data-f="after_minutes" value="${r.after_minutes}"> ${esc(T('دقيقة صمت', 'silent min'))}</label>`);
    if ('weekday' in r) opts.push(`<label class="al-opt">${esc(T('يوم', 'Day'))} <select class="inp" data-f="weekday">${days.map((d, i) => `<option value="${i}" ${+r.weekday === i ? 'selected' : ''}>${esc(d)}</option>`).join('')}</select></label>`);
    if ('at' in r) opts.push(`<label class="al-opt">${esc(T('الساعة', 'At'))} <input class="inp" type="time" data-f="at" value="${esc(r.at)}"></label>`);
    if ('to_employee' in r) opts.push(`<button class="al-who ${r.to_employee ? 'on' : ''}" data-w="to_employee">${icon('people')}${esc(T('الموظف', 'Employee'))}</button>`);
    if ('to_managers' in r) opts.push(`<button class="al-who ${r.to_managers ? 'on' : ''}" data-w="to_managers">${icon('shield')}${esc(T('المدراء', 'Managers'))}</button>`);
    if ('emails' in r) opts.push(`<label class="al-opt wide">${icon('mail')}<input class="inp" data-f="emails" placeholder="${esc(T('بريد إضافي، افصل بفاصلة', 'Extra e-mails, comma separated'))}" value="${esc(r.emails || '')}"></label>`);
    const card = h(`<div class="al-rule ${r.on ? 'on' : ''}" data-k="${k}">
        <div class="al-rule-top"><span class="al-ic" style="--c:${meta.color}">${icon(meta.icon)}</span>
          <span class="grow"><b>${esc(meta.t)}</b><small>${esc(meta.d)}</small></span>${toggle(r.on)}</div>
        <div class="al-rule-opts">${opts.join('')}</div></div>`);
    const val = () => {
      const v = { on: $('.switch input', card).checked };
      $$('[data-f]', card).forEach(i => { v[i.dataset.f] = i.type === 'number' || i.tagName === 'SELECT' ? +i.value : i.value; });
      $$('[data-w]', card).forEach(b => { v[b.dataset.w] = b.classList.contains('on'); });
      return v;
    };
    $('.switch input', card).onchange = () => { card.classList.toggle('on', $('.switch input', card).checked); queue(k, val()); };
    $$('[data-f]', card).forEach(i => i.onchange = () => queue(k, val()));
    $$('[data-w]', card).forEach(b => b.onclick = () => { b.classList.toggle('on'); queue(k, val()); });
    rl.appendChild(card);
  }

  // ---- log
  c.appendChild(h(`<h3 class="al-h">${esc(T('سجل الإرسال', 'Delivery log'))}</h3>`));
  const st = { sent: ['ok', T('أُرسل', 'Sent')], failed: ['bad', T('فشل', 'Failed')] };
  const CN = CHANNEL_NAMES();
  const g = grid(c, {
    columns: [{ key: 'created_at', label: T('الوقت', 'Time'), cls: 'ltr' },
      { key: 'rule', label: T('التنبيه', 'Alert'), render: r => esc(RU[r.rule]?.t || r.rule) },
      { key: 'title', label: T('العنوان', 'Title') },
      { key: 'channel', label: T('القناة', 'Channel'), render: r => esc(CN[r.channel] || r.channel) },
      { key: 'target', label: T('إلى', 'To'), render: r => { const m = /^(\d+) users, (\d+) employees$/.exec(r.target || ''); return m ? esc([+m[1] ? T(`${m[1]} مستخدم`, `${m[1]} users`) : '', +m[2] ? T(`${m[2]} موظف`, `${m[2]} employees`) : ''].filter(Boolean).join(' · ') || '—') : `<bdi>${esc(r.target)}</bdi>`; } },
      { key: 'status', label: T('الحالة', 'Status'), render: r => `<span class="badge ${st[r.status]?.[0] || ''}">${esc(st[r.status]?.[1] || r.status)}</span>` },
      { key: 'error', label: T('السبب', 'Reason'), cls: 'wrap' }],
    fetch: localFetch('/api/alerts/log'), select: false, pageSize: 20,
    filters: [{ key: 'rule', label: T('كل التنبيهات', 'All alerts'), type: 'select', options: Object.entries(RU).map(([k, v]) => ({ value: k, label: v.t })) },
      { key: 'status', label: T('كل الحالات', 'Any status'), type: 'select', options: Object.entries(st).map(([k, v]) => ({ value: k, label: v[1] })) }],
  });
  Live.on('notify', () => g.reload(), 2000);
  paintChannels(); paintHero();
}
async function pageUsers(c) {
  const lk = await lookups(true);
  simplePage(c, T('المستخدمون', 'Users'), '/api/users', 'system.admin',
    [{ key: 'username', label: T('اسم المستخدم', 'Username') }, { key: 'full_name', label: T('الاسم', 'Name') }, { key: 'role', label: T('الدور', 'Role'), render: r => r.is_superuser ? `<span class="badge info">${esc(T('مدير عام', 'Superuser'))}</span>` : esc(r.role) },
      { key: 'active', label: T('نشط', 'Active'), render: r => r.active ? '✓' : '—' }, { key: 'last_login', label: T('آخر دخول', 'Last login'), cls: 'ltr' }],
    [{ key: 'username', label: T('اسم المستخدم', 'Username'), required: true }, { key: 'full_name', label: T('الاسم', 'Name') },
      { key: 'password', label: T('كلمة المرور (اتركها فارغة لعدم التغيير)', 'Password (blank = unchanged)'), type: 'password' }, { key: 'email', label: T('البريد', 'Email') },
      { key: 'role_id', label: T('الدور', 'Role'), type: 'select', options: opts(lk.roles) }, { key: 'is_superuser', label: T('مدير عام', 'Superuser'), type: 'checkbox', text: T('كل الصلاحيات', 'All permissions') },
      { key: 'active', label: T('نشط', 'Active'), type: 'checkbox', text: T('يمكنه الدخول', 'Can sign in'), default: true }]);
}
async function pageRoles(c) {
  const perms = await GET('/api/permissions');
  const names = { 'personnel.view': T('عرض الموظفين', 'View personnel'), 'personnel.edit': T('تعديل الموظفين', 'Edit personnel'), 'device.view': T('عرض الأجهزة', 'View devices'), 'device.control': T('التحكم بالأجهزة', 'Control devices'), 'attendance.view': T('عرض الحضور', 'View attendance'), 'attendance.edit': T('تعديل الحضور والجداول', 'Edit attendance & schedules'), 'attendance.approve': T('اعتماد الطلبات', 'Approve requests'), 'reports.view': T('التقارير', 'Reports'), 'system.admin': T('إدارة النظام', 'System administration') };
  simplePage(c, T('الأدوار والصلاحيات', 'Roles'), '/api/roles', 'system.admin',
    [{ key: 'name', label: T('الاسم', 'Name') }, { key: 'permissions', label: T('الصلاحيات', 'Permissions'), cls: 'wrap', render: r => r.permissions.map(p => `<span class="badge">${esc(names[p] || p)}</span>`).join(' ') }],
    [{ key: 'name', label: T('الاسم', 'Name'), required: true }, { key: 'permissions', label: T('الصلاحيات', 'Permissions'), type: 'multi', options: perms.map(p => ({ value: p, label: names[p] || p })) }]);
}
function pageBackup(c) {
  title(c, T('النسخ الاحتياطي', 'Backup'));
  c.appendChild(h(`<div class="alert info">${esc(T('يُنشأ نسخة احتياطية تلقائية كل يوم. احفظ نسخة خارج الجهاز بانتظام (زر تنزيل).', 'A backup is made automatically every day. Keep a copy outside this PC (Download).'))}</div>`));
  const g = grid(c, {
    columns: [{ key: 'name', label: T('الملف', 'File'), cls: 'ltr' }, { key: 'time', label: T('الوقت', 'Time'), cls: 'ltr' }, { key: 'size', label: T('الحجم', 'Size'), render: r => (r.size / 1048576).toFixed(2) + ' MB' },
      { key: 'x', label: '', render: r => `<button class="btn small" data-act="dl">${icon('download')}${esc(T('تنزيل', 'Download'))}</button> <button class="btn small danger" data-act="restore">${esc(T('استعادة', 'Restore'))}</button>` }],
    idKey: 'name', select: false, search: false,
    fetch: localFetch('/api/backups'),
    actions: { dl: (r) => download(`/api/backups/${encodeURIComponent(r.name)}`), restore: async (r) => {
      if (!await confirmBox(T('استعادة هذه النسخة؟ ستُستبدل كل البيانات الحالية (تُحفظ نسخة منها أولاً).', 'Restore this backup? All current data is replaced (a copy is saved first).'))) return;
      await guard(() => POST(`/api/backups/${encodeURIComponent(r.name)}/restore`), T('تمت الاستعادة', 'Restored')); setTimeout(() => location.reload(), 800); } },
    toolbar: [{ label: T('نسخة احتياطية الآن', 'Back up now'), icon: 'db', cls: 'primary', action: async () => { await guard(() => POST('/api/backups'), T('تم إنشاء النسخة', 'Backup created')); g.reload(); } }],
  });
}
function pageAudit(c) {
  title(c, T('سجل التدقيق', 'Audit log'));
  grid(c, { columns: [{ key: 'created_at', label: T('الوقت', 'Time'), cls: 'ltr' }, { key: 'username', label: T('المستخدم', 'User') }, { key: 'action', label: T('العملية', 'Action') }, { key: 'target', label: T('الهدف', 'Target') }, { key: 'detail', label: T('التفاصيل', 'Detail'), cls: 'wrap' }, { key: 'ip', label: 'IP', cls: 'ltr' }],
    fetch: serverFetch('/api/audit'), select: false });
}
function pageExperiments(c) { return ExperimentsPage.render(c); }
async function pageAbout(c) {
  const a = await GET('/api/about');
  title(c, T('حول البرنامج', 'About'));
  c.appendChild(h(`<div class="panel"><div class="panel-body"><h2 style="margin-top:0">${esc(BRAND().name)} <span class="badge info">${esc(a.version)}</span></h2>
    <p>${esc(T('نظام حضور وانصراف لأجهزة البصمة (SpeedFace، ProFace، MB، UFace...) عبر بروتوكول ADMS/Push، مع دعم الوجه والبصمة والكف والبطاقة.', 'time & attendance for biometric terminals (SpeedFace, ProFace, MB, UFace...) over ADMS/Push, with face, fingerprint, palm and card.'))}</p>
    <ul><li>${esc(T('الأجهزة، المناطق، توزيع القوالب تلقائياً', 'Devices, areas, automatic template distribution'))}</li><li>${esc(T('أوقات الدوام، الورديات الدورية، الجداول المؤقتة، الدوام الليلي والمرن', 'Timetables, cyclic shifts, temporary schedules, night & flexible shifts'))}</li>
    <li>${esc(T('الإجازات، البصمات اليدوية، العمل الإضافي مع الاعتماد', 'Leave, manual punches, overtime with approval'))}</li><li>${esc(T('13 تقريراً مع التصدير إلى Excel وCSV والطباعة', '13 reports with Excel/CSV export and printing'))}</li></ul>
    <p class="muted"><a href="/api/docs" target="_blank">API</a></p></div></div>`));
}

// ============================================================== boot
(async function boot() {
  try { App.demoInfo = await api('GET', '/api/demo/info', undefined, { noAuthRedirect: true }); } catch(e) { App.demoInfo = {is_demo:false}; }
  try {
    let u;
    try { u = await api('GET', '/api/auth/me', undefined, { noAuthRedirect: true }); }
    catch(e) {
      const token = location.hash.startsWith('#demo-access=') ? location.hash.slice(13) : '';
      if (!App.demoInfo.is_demo || !token) throw e;
      u = (await api('POST', '/api/demo/access', {token}, { noAuthRedirect: true })).user;
    }
    if (location.hash.startsWith('#demo-access=')) history.replaceState(null, '', '/#/dashboard');
    await startApp(u);
  }
  catch (e) { showLogin(); }
})();
