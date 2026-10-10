/* Employee portal — no libraries. Screens: Today, Calendar, Requests, Inbox, Account. */
'use strict';
const S = { lang: 'ar', me: null, tab: 'home', month: null, cache: {}, passwordOpen: false };
try { S.lang = localStorage.getItem('me_lang') || 'ar'; } catch (e) { /* private mode */ }
const T = (ar, en) => (S.lang === 'en' ? (en ?? ar) : ar);
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const h = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const app = () => $('#app');
function setLang(l) {
  S.lang = l === 'en' ? 'en' : 'ar';
  try { localStorage.setItem('me_lang', S.lang); } catch (e) { /* ignore */ }
  document.documentElement.lang = S.lang; document.documentElement.dir = S.lang === 'ar' ? 'rtl' : 'ltr';
  document.title = S.lang === 'ar' ? 'حضوري' : 'My attendance';
}
setLang(S.lang);

async function api(method, url, body) {
  const init = { method, headers: { 'X-Lang': S.lang }, credentials: 'same-origin' };
  if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  const r = await fetch(url, init);
  if (r.status === 401 && !url.endsWith('/login')) { S.me = null; showLogin(); throw new Error(T('انتهت الجلسة', 'Signed out')); }
  if (!r.ok) {
    let msg = r.statusText, detail; try { const j = await r.json(); detail = j.detail; msg = typeof detail === 'string' ? detail : (detail?.message || JSON.stringify(detail)); } catch (e) { /* not json */ }
    if (r.status === 403 && detail?.code === 'password_change_required') {
      if (S.me) S.me.must_change = true;
      changePassword(true);
      msg = T('غيّر كلمة المرور للمتابعة', 'Change your password to continue');
    }
    // server messages are "Arabic / English": show the half in the page language
    if (msg.includes(' / ')) { const [a, b] = msg.split(' / '); msg = S.lang === 'en' ? b : a; }
    throw new Error(msg);
  }
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text();
}
const GET = (u) => api('GET', u), POST = (u, b) => api('POST', u, b ?? {}), DEL = (u) => api('DELETE', u);
function toast(msg, kind = '') { const t = h(`<div class="toast ${kind}">${esc(msg)}</div>`); document.body.appendChild(t); setTimeout(() => t.remove(), kind === 'bad' ? 5000 : 2600); }

const IC = {
  home: '<path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>',
  cal: '<path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.11 0-1.99.9-1.99 2L3 20a2 2 0 002 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 16H5V9h14v11zM7 11h5v5H7z"/>',
  req: '<path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/>',
  bell: '<path d="M12 22c1.1 0 2-.9 2-2h-4a2 2 0 002 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4a1.5 1.5 0 00-3 0v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/>',
  user: '<path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>',
  plus: '<path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z"/>',
  leave: '<path d="M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/>',
  fix: '<path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"/>',
  ot: '<path d="M13 3a9 9 0 00-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0013 21a9 9 0 000-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/>',
  prev: '<path d="M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z"/>',
  next: '<path d="M10 6 8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z"/>',
  out: '<path d="M10.09 15.59 11.5 17l5-5-5-5-1.41 1.41L12.67 11H3v2h9.67l-2.58 2.59zM19 3H5a2 2 0 00-2 2v4h2V5h14v14H5v-4H3v4a2 2 0 002 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2z"/>',
  key: '<path d="M12.65 10A5.99 5.99 0 007 6c-3.31 0-6 2.69-6 6s2.69 6 6 6a5.99 5.99 0 005.65-4H17v4h4v-4h2v-4H12.65zM7 14c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z"/>',
  lang: '<path d="m12.87 15.07-2.54-2.51.03-.03A17.52 17.52 0 0014.07 6H17V4h-7V2H8v2H1v1.99h11.17C11.5 7.92 10.44 9.75 9 11.35 8.07 10.32 7.3 9.19 6.69 8h-2c.73 1.63 1.73 3.17 2.98 4.56l-5.09 5.02L4 19l5-5 3.11 3.11.76-2.04zM18.5 10h-2L12 22h2l1.12-3h4.75L21 22h2l-4.5-12zm-2.62 7 1.62-4.33L19.12 17h-3.24z"/>',
};
const icon = (n) => `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">${IC[n] || ''}</svg>`;

const ST = {  // engine status -> colour + label
  present: ['var(--good)', 'حاضر', 'Present'], late: ['var(--warn)', 'متأخر', 'Late'], early: ['var(--warn)', 'خروج مبكر', 'Left early'],
  late_early: ['var(--warn)', 'متأخر وخروج مبكر', 'Late & early'], incomplete: ['var(--info)', 'بصمة ناقصة', 'Missing punch'],
  absent: ['var(--bad)', 'غائب', 'Absent'], partial_absent: ['var(--bad)', 'غياب جزئي', 'Partial absence'], leave: ['var(--leave)', 'إجازة', 'Leave'], off: ['var(--idle)', 'عطلة', 'Day off'],
  holiday: ['var(--idle)', 'عطلة رسمية', 'Holiday'], pending: ['var(--idle)', 'لم يحضر بعد', 'Not in yet'],
  unscheduled: ['var(--info)', 'بدون دوام', 'No schedule'],
};
const stColor = (s) => (ST[s] || ['var(--idle)'])[0];
const stName = (s) => { const x = ST[s]; return x ? T(x[1], x[2]) : (s || '—'); };
const hm = (mins) => { mins = Math.round(mins || 0); return `${Math.floor(mins / 60)}:${String(mins % 60).padStart(2, '0')}`; };
const tm = (s) => (s ? s.slice(11, 16) : '—');
const fmtDate = (iso) => new Date(iso + 'T00:00:00').toLocaleDateString(S.lang === 'ar' ? 'ar' : 'en-GB', { weekday: 'long', day: 'numeric', month: 'long' });

// ------------------------------------------------------------------ sign in
function showLogin() {
  $$('.scrim').forEach(el => el.remove()); S.passwordOpen = false;
  app().innerHTML = '';
  const el = h(`<div class="login"><div class="login-box">
      <div class="logo">${S.lang === 'ar' ? 'ح' : 'H'}</div><h1>${esc(T('حضوري', 'My attendance'))}</h1>
      <p class="sub">${esc(T('ادخل برقمك الوظيفي وكلمة مرور البوابة', 'Sign in with your employee number and portal password'))}</p>
      <form class="card">
        <div class="field"><label>${esc(T('الرقم الوظيفي', 'Employee number'))}</label><input class="inp ltr" name="code" inputmode="numeric" autocomplete="username" required></div>
        <div class="field"><label>${esc(T('كلمة المرور', 'Password'))}</label><input class="inp ltr" name="pw" type="password" autocomplete="current-password" required></div>
        <button class="btn primary block">${esc(T('دخول', 'Sign in'))}</button>
      </form>
      <p class="sub"><button class="btn text lang">${icon('lang')} ${S.lang === 'ar' ? 'English' : 'العربية'}</button></p>
      <p class="sub" style="font-size:13px">${esc(T('لا تعرف كلمة المرور؟ اطلبها من قسم الموارد البشرية.', 'No password yet? Ask HR for one.'))}</p>
    </div></div>`);
  app().appendChild(el);
  $('.lang', el).onclick = () => { setLang(S.lang === 'ar' ? 'en' : 'ar'); showLogin(); };
  $('form', el).onsubmit = async (ev) => {
    ev.preventDefault();
    const b = $('button.primary', el); b.disabled = true;
    try {
      const r = await POST('/api/me/login', { code: $('[name=code]', el).value.trim(), password: $('[name=pw]', el).value });
      await boot(r.must_change);
    } catch (e) { toast(e.message, 'bad'); } finally { b.disabled = false; }
  };
}

// ------------------------------------------------------------------ shell
async function boot(forceChange) {
  try { S.me = await GET('/api/me'); } catch (e) { return showLogin(); }
  if (S.me.lang && S.me.lang !== S.lang && !localStorage.getItem('me_lang')) setLang(S.me.lang);
  if (forceChange || S.me.must_change) {
    app().innerHTML = `<main class="page"><section class="card"><h1>${esc(T('اختر كلمة مرور خاصة بك', 'Choose your own password'))}</h1><p>${esc(T('غيّر كلمة المرور الأولى لفتح سجل الحضور والطلبات.', 'Change your initial password to open your attendance and requests.'))}</p></section></main>`;
    changePassword(true);
  } else render();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/me/sw.js', { scope: '/me' }).catch(() => {});
}
const TABS = [['home', 'home', 'اليوم', 'Today'], ['cal', 'cal', 'سجلي', 'My days'], ['req', 'req', 'طلباتي', 'Requests'],
  ['bell', 'bell', 'الإشعارات', 'Inbox'], ['me', 'user', 'حسابي', 'Account']];
function render() {
  app().innerHTML = `<main class="page" id="page"></main>
    <nav class="tabs"><div class="tabs-in">${TABS.map(([k, ic, ar, en]) => `<button class="tab ${S.tab === k ? 'on' : ''}" data-t="${k}"><span class="pill">${icon(ic)}</span>${esc(T(ar, en))}${k === 'bell' && S.me.unread ? `<span class="dot-badge">${S.me.unread}</span>` : ''}</button>`).join('')}</div></nav>`;
  $$('.tab').forEach(b => b.onclick = () => { S.tab = b.dataset.t; render(); });
  ({ home: pageHome, cal: pageCal, req: pageReq, bell: pageBell, me: pageMe })[S.tab]($('#page'));
}
function greet() { const hr = new Date().getHours(); return hr < 12 ? T('صباح الخير', 'Good morning') : hr < 18 ? T('مساء الخير', 'Good afternoon') : T('مساء الخير', 'Good evening'); }
function avatarHtml() { const n = (S.me.display_name || S.me.name || '?').trim(); return `<div class="avatar">${S.me.has_photo ? `<img src="/api/me/photo" alt="">` : esc(n.charAt(0))}</div>`; }

// ------------------------------------------------------------------ Today
async function pageHome(p) {
  p.innerHTML = `<div class="top">${avatarHtml()}<h1>${esc(S.me.company || T('حضوري', 'My attendance'))}</h1>
    <button class="icon-btn" data-go="bell" aria-label="inbox">${icon('bell')}${S.me.unread ? `<span class="dot-badge">${S.me.unread}</span>` : ''}</button></div>
    <div class="skeleton"></div>`;
  $('[data-go]', p).onclick = () => { S.tab = 'bell'; render(); };
  let me = S.me, month;
  try { [me, month] = await Promise.all([GET('/api/me'), GET('/api/me/month')]); S.me = me; } catch (e) { return; }
  const t = me.today || {};
  const req = t.required || 0;
  // still at work: count the time since check-in, live
  const since = t.clock_in && !t.clock_out ? (Date.now() - new Date(String(t.clock_in).replace(' ', 'T'))) / 60000 : 0;
  const worked = t.worked || (since > 0 && since < 20 * 60 ? since : 0);
  const pct = req ? Math.min(1, worked / req) : (t.clock_in ? 1 : 0);
  const C = 2 * Math.PI * 46;
  const first = (me.display_name || me.name || '').split(' ')[0];
  const tot = month.totals;
  p.querySelector('.skeleton').replaceWith(h(`<div>
    <section class="hero">
      <div class="hi">${esc(greet())}</div><div class="name">${esc(first)}</div>
      <div class="hero-row">
        <svg class="ring" viewBox="0 0 112 112"><circle class="bg" cx="56" cy="56" r="46"/><circle class="fg" cx="56" cy="56" r="46" transform="rotate(-90 56 56)" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - pct)}"/>
          <text x="56" y="54" text-anchor="middle" font-size="22">${hm(worked)}</text><text x="56" y="74" text-anchor="middle" font-size="11" opacity=".8">${req ? esc(T(`من ${hm(req)}`, `of ${hm(req)}`)) : ''}</text></svg>
        <div class="hero-facts">
          <span class="chip" style="color:#fff"><i style="background:${stColor(t.status)}"></i>${esc(stName(t.status))}</span>
          <div class="fact"><span>${esc(T('الدخول', 'In'))}</span><b class="ltr">${tm(t.clock_in)}</b></div>
          <div class="fact"><span>${esc(T('الخروج', 'Out'))}</span><b class="ltr">${tm(t.clock_out)}</b></div>
          <div class="fact"><span>${esc(T('الدوام', 'Shift'))}</span><b class="ltr">${t.sched_in ? `${tm(t.sched_in)}–${tm(t.sched_out)}` : '—'}</b></div>
        </div>
      </div>
    </section>
    ${me.requests_on ? `<div class="actions">
      <button class="action" data-k="leave"><span class="ic" style="background:var(--leave)">${icon('leave')}</span>${esc(T('طلب إجازة', 'Leave'))}</button>
      <button class="action" data-k="manual"><span class="ic" style="background:var(--info)">${icon('fix')}</span>${esc(T('تصحيح بصمة', 'Fix a punch'))}</button>
      <button class="action" data-k="overtime"><span class="ic" style="background:var(--warn)">${icon('ot')}</span>${esc(T('عمل إضافي', 'Overtime'))}</button>
    </div>` : ''}
    <section class="card"><h3>${esc(T('هذا الشهر', 'This month'))}</h3>
      <div class="stats">
        <div class="stat"><b>${tot.present_days}</b><span>${esc(T('أيام حضور', 'Days in'))}</span></div>
        <div class="stat"><b class="ltr">${hm(tot.late)}</b><span>${esc(T('تأخير', 'Late'))}</span></div>
        <div class="stat"><b>${tot.absent_days}</b><span>${esc(T('غياب', 'Absent'))}</span></div>
        <div class="stat"><b>${tot.partial_absent_days ?? 0}</b><span>${esc(T('غياب جزئي', 'Partial absence'))}</span></div>
        <div class="stat"><b class="ltr">${hm(tot.absent_minutes)}</b><span>${esc(T('مدة الغياب', 'Absent duration'))}</span></div>
        <div class="stat"><b class="ltr">${hm(tot.worked)}</b><span>${esc(T('ساعات العمل', 'Hours worked'))}</span></div>
        <div class="stat"><b class="ltr">${hm(tot.ot)}</b><span>${esc(T('إضافي', 'Overtime'))}</span></div>
        <div class="stat"><b>${tot.leave_days}</b><span>${esc(T('إجازات', 'Leave days'))}</span></div>
      </div></section>
    ${t.punches && t.punches.length ? `<section class="card"><h3>${esc(T('بصمات اليوم', 'Today’s punches'))}</h3><div class="punches">${t.punches.map(x => `<span class="ltr">${esc(String(x).slice(-5))}</span>`).join('')}</div></section>` : ''}
    ${me.pending ? `<section class="card tap" data-go="req"><h3>${esc(T(`لديك ${me.pending} طلب بانتظار الرد`, `${me.pending} request(s) waiting for an answer`))}</h3><span class="muted">${esc(T('اضغط للمتابعة', 'Tap to follow'))}</span></section>` : ''}
  </div>`));
  $$('.action', p).forEach(b => b.onclick = () => requestForm(b.dataset.k));
  const pend = $('[data-go=req]', p); if (pend) pend.onclick = () => { S.tab = 'req'; render(); };
}

// ------------------------------------------------------------------ Calendar
async function pageCal(p) {
  const now = new Date();
  S.month = S.month || `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  p.innerHTML = `<div class="top"><h1>${esc(T('سجلي', 'My days'))}</h1></div><div class="skeleton"></div>`;
  let mth; try { mth = await GET('/api/me/month?ym=' + S.month); } catch (e) { return; }
  const first = new Date(mth.first + 'T00:00:00'), last = new Date(mth.last + 'T00:00:00');
  const byDate = Object.fromEntries(mth.days.map(d => [d.date, d]));
  const startDow = (first.getDay() + 1) % 7;  // week starts on Saturday
  const wd = S.lang === 'ar' ? ['س', 'ح', 'ن', 'ث', 'ر', 'خ', 'ج'] : ['Sa', 'Su', 'Mo', 'Tu', 'We', 'Th', 'Fr'];
  const todayIso = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  let cells = wd.map(x => `<div class="wd">${x}</div>`).join('') + '<div class="d empty"></div>'.repeat(startDow);
  for (let d = 1; d <= last.getDate(); d++) {
    const iso = `${S.month}-${String(d).padStart(2, '0')}`, r = byDate[iso];
    cells += `<button class="d ${iso === todayIso ? 'today' : ''} ${r ? '' : 'future'}" data-d="${iso}">${d}${r ? `<i style="background:${stColor(r.status)}"></i>` : ''}</button>`;
  }
  const label = first.toLocaleDateString(S.lang === 'ar' ? 'ar' : 'en-GB', { month: 'long', year: 'numeric' });
  const tot = mth.totals;
  p.querySelector('.skeleton').replaceWith(h(`<div>
    <section class="card">
      <div class="month-head"><button class="icon-btn" data-m="-1">${icon(S.lang === 'ar' ? 'next' : 'prev')}</button><b>${esc(label)}</b><button class="icon-btn" data-m="1">${icon(S.lang === 'ar' ? 'prev' : 'next')}</button></div>
      <div class="cal">${cells}</div>
      <div class="legend">${['present', 'late', 'absent', 'partial_absent', 'leave', 'incomplete', 'off'].map(s => `<span><i style="background:${stColor(s)}"></i>${esc(stName(s))}</span>`).join('')}</div>
    </section>
    <section class="card"><div class="stats">
      <div class="stat"><b>${tot.present_days}</b><span>${esc(T('أيام حضور', 'Days in'))}</span></div>
      <div class="stat"><b>${tot.late_days}</b><span>${esc(T('أيام تأخير', 'Late days'))}</span></div>
      <div class="stat"><b>${tot.absent_days}</b><span>${esc(T('غياب', 'Absent'))}</span></div>
      <div class="stat"><b>${tot.partial_absent_days ?? 0}</b><span>${esc(T('غياب جزئي', 'Partial absence'))}</span></div>
      <div class="stat"><b class="ltr">${hm(tot.absent_minutes)}</b><span>${esc(T('مدة الغياب', 'Absent duration'))}</span></div>
      <div class="stat"><b class="ltr">${hm(tot.worked)}</b><span>${esc(T('عملت', 'Worked'))}</span></div>
      <div class="stat"><b class="ltr">${hm(tot.required)}</b><span>${esc(T('المطلوب', 'Required'))}</span></div>
      <div class="stat"><b class="ltr">${hm(tot.early)}</b><span>${esc(T('خروج مبكر', 'Left early'))}</span></div>
    </div></section></div>`));
  $$('[data-m]', p).forEach(b => b.onclick = () => {
    const [y, m] = S.month.split('-').map(Number); const d = new Date(y, m - 1 + Number(b.dataset.m), 1);
    S.month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; pageCal(p);
  });
  $$('.d[data-d]', p).forEach(b => b.onclick = () => { const r = byDate[b.dataset.d]; if (r) dayDetail(r); });
}
function dayDetail(r) {
  sheet(fmtDate(r.date), `<div class="chip" style="background:var(--bg)"><i style="background:${stColor(r.status)}"></i>${esc(stName(r.status))}${r.holiday ? ' · ' + esc(r.holiday) : ''}</div>
    <div class="kv" style="margin-top:14px">
      <span>${esc(T('الدوام', 'Shift'))}</span><b class="ltr">${r.sched_in ? `${tm(r.sched_in)}–${tm(r.sched_out)}` : '—'}</b>
      <span>${esc(T('الدخول', 'In'))}</span><b class="ltr">${tm(r.clock_in)}</b>
      <span>${esc(T('الخروج', 'Out'))}</span><b class="ltr">${tm(r.clock_out)}</b>
      <span>${esc(T('ساعات العمل', 'Worked'))}</span><b class="ltr">${hm(r.worked)}</b>
      ${r.late ? `<span>${esc(T('تأخير', 'Late'))}</span><b class="ltr">${hm(r.late)}</b>` : ''}
      ${r.early ? `<span>${esc(T('خروج مبكر', 'Left early'))}</span><b class="ltr">${hm(r.early)}</b>` : ''}
      ${r.absent ? `<span>${esc(T('مدة الغياب', 'Absent duration'))}</span><b class="ltr">${hm(r.absent)}</b>` : ''}
      ${r.ot ? `<span>${esc(T('إضافي', 'Overtime'))}</span><b class="ltr">${hm(r.ot)}</b>` : ''}
    </div>
    ${r.punches && r.punches.length ? `<h3 style="margin:16px 0 8px">${esc(T('البصمات', 'Punches'))}</h3><div class="punches">${r.punches.map(x => `<span class="ltr">${esc(String(x).slice(-5))}</span>`).join('')}</div>` : ''}
    ${S.me.requests_on && ['incomplete', 'absent', 'partial_absent', 'late', 'early', 'late_early'].includes(r.status) ? `<button class="btn primary block fixit" style="margin-top:18px">${esc(T('طلب تصحيح بصمة لهذا اليوم', 'Ask to correct a punch on this day'))}</button>` : ''}`,
  (el, close) => { const b = $('.fixit', el); if (b) b.onclick = () => { close(); requestForm('manual', r.date); }; });
}

// ------------------------------------------------------------------ Requests
async function pageReq(p) {
  p.innerHTML = `<div class="top"><h1>${esc(T('طلباتي', 'My requests'))}</h1></div><div class="skeleton"></div>`;
  let reqs, bal; try { [reqs, bal] = await Promise.all([GET('/api/me/requests'), GET('/api/me/balances')]); } catch (e) { return; }
  const kindName = { leave: T('إجازة', 'Leave'), manual: T('تصحيح بصمة', 'Punch correction'), overtime: T('عمل إضافي', 'Overtime') };
  const stName2 = { pending: T('بانتظار الرد', 'Waiting'), approved: T('موافق عليه', 'Approved'), rejected: T('مرفوض', 'Rejected') };
  const tracked = bal.rows.filter(b => b.entitled);
  p.querySelector('.skeleton').replaceWith(h(`<div>
    ${tracked.length ? `<section class="card"><h3>${esc(T(`رصيد الإجازات ${bal.year}`, `Leave balance ${bal.year}`))}</h3><p class="muted">${esc(T('الرصيد بوحدات أيام العمل المجدولة، ويستبعد الراحة والعطلات. عند عدم وجود جدول يُستخدم تقويم النظام الافتراضي.', 'Balances use scheduled workday units, excluding days off and holidays. Without a schedule, the system’s default calendar applies.'))}</p><div class="list">${tracked.map(b => `
      <div class="bal"><div class="item-top"><b>${esc(b.name)}</b><span><b>${b.left}</b> ${esc(T('متبقٍ من', 'left of'))} ${b.entitled}</span></div>
      <div class="bar"><i style="width:${Math.min(100, 100 * b.used / b.entitled)}%;background:${esc(b.color)}"></i></div></div>`).join('')}</div></section>` : ''}
    <div class="list">${reqs.rows.length ? reqs.rows.map(r => `<div class="item">
      <div class="item-top"><b>${esc(kindName[r.kind])}${r.type ? ' — ' + esc(r.type) : ''}</b><span class="status ${r.status}">${esc(stName2[r.status] || r.status)}</span></div>
      <span class="muted ltr" style="text-align:start">${esc(r.kind === 'manual' ? `${r.time} · ${r.state === 1 ? T('خروج', 'out') : T('دخول', 'in')}` : span(r))}</span>
      ${r.reason ? `<span>${esc(r.reason)}</span>` : ''}
      ${r.decided_at ? `<span class="muted" style="font-size:12px">${esc(T('الرد', 'Answered'))}: ${esc(r.decided_at)}${r.approver ? ' · ' + esc(r.approver) : ''}</span>` : ''}
      ${r.status === 'pending' ? `<div class="row-actions"><button class="btn text danger" data-cancel="${r.kind}/${r.id}">${esc(T('إلغاء الطلب', 'Cancel'))}</button></div>` : ''}
    </div>`).join('') : `<div class="empty">${icon('req')}<p>${esc(T('لا توجد طلبات بعد', 'No requests yet'))}</p></div>`}</div></div>`));
  $$('[data-cancel]', p).forEach(b => b.onclick = async () => {
    if (!confirm(T('إلغاء هذا الطلب؟', 'Cancel this request?'))) return;
    try { await DEL('/api/me/requests/' + b.dataset.cancel); toast(T('أُلغي الطلب', 'Cancelled'), 'ok'); pageReq(p); } catch (e) { toast(e.message, 'bad'); }
  });
  if (S.me.requests_on) {
    const fab = h(`<button class="fab" aria-label="new">${icon('plus')}</button>`); p.appendChild(fab);
    fab.onclick = () => sheet(T('طلب جديد', 'New request'), `<div class="kinds">
        <button class="kind" data-k="leave"><span class="ic" style="background:var(--leave)">${icon('leave')}</span><span><b>${esc(T('إجازة', 'Leave'))}</b><span class="muted">${esc(T('يوم أو أكثر، أو ساعات', 'A day or more, or some hours'))}</span></span></button>
        <button class="kind" data-k="manual"><span class="ic" style="background:var(--info)">${icon('fix')}</span><span><b>${esc(T('تصحيح بصمة', 'Punch correction'))}</b><span class="muted">${esc(T('نسيت البصمة أو لم تُسجَّل', 'Forgot to punch or it was not recorded'))}</span></span></button>
        <button class="kind" data-k="overtime"><span class="ic" style="background:var(--warn)">${icon('ot')}</span><span><b>${esc(T('عمل إضافي', 'Overtime'))}</b><span class="muted">${esc(T('ساعات بعد الدوام أو في عطلة', 'Hours after the shift or on a day off'))}</span></span></button>
      </div>`, (el, close) => $$('.kind', el).forEach(b => b.onclick = () => { close(); requestForm(b.dataset.k); }));
  }
}
// whole days read as dates ("7 Oct → 8 Oct" for two days), part-days keep their hours
function span(r) {
  if (r.start.endsWith('00:00') && r.end.endsWith('00:00')) {
    const a = r.start.slice(0, 10), e = new Date(r.end.slice(0, 10) + 'T00:00:00'); e.setDate(e.getDate() - 1);
    const b = `${e.getFullYear()}-${String(e.getMonth() + 1).padStart(2, '0')}-${String(e.getDate()).padStart(2, '0')}`;
    return a === b ? `${a} · ${T('يوم واحد', '1 day')}` : `${a} → ${b} · ${r.days} ${T('أيام', 'days')}`;
  }
  return r.start.slice(0, 10) === r.end.slice(0, 10) ? `${r.start.slice(0, 10)} ${r.start.slice(11)}–${r.end.slice(11)}` : `${r.start} → ${r.end}`;
}
async function requestForm(kind, day) {
  const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  let body = '';
  if (kind === 'leave') {
    const types = (await GET('/api/me/leave-types')).rows;
    body = `<div class="field"><label>${esc(T('نوع الإجازة', 'Type'))}</label><select class="inp" name="type">${types.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('')}</select></div>
      <div class="field"><div class="seg"><button type="button" class="on" data-span="days">${esc(T('أيام', 'Days'))}</button><button type="button" data-span="hours">${esc(T('ساعات', 'Hours'))}</button></div></div>
      <div class="span-days"><div class="field"><label>${esc(T('من', 'From'))}</label><input class="inp" type="date" name="d1" value="${day || today}"></div>
        <div class="field"><label>${esc(T('إلى', 'To'))}</label><input class="inp" type="date" name="d2" value="${day || today}"></div></div>
      <div class="span-hours hidden"><div class="field"><label>${esc(T('اليوم', 'Day'))}</label><input class="inp" type="date" name="hd" value="${day || today}"></div>
        <div class="field"><label>${esc(T('من الساعة', 'From'))}</label><input class="inp" type="time" name="h1" value="09:00"></div>
        <div class="field"><label>${esc(T('إلى الساعة', 'To'))}</label><input class="inp" type="time" name="h2" value="11:00"></div></div>`;
  } else if (kind === 'manual') {
    body = `<div class="field"><label>${esc(T('اليوم', 'Day'))}</label><input class="inp" type="date" name="day" value="${day || today}" max="${today}"></div>
      <div class="field"><label>${esc(T('الوقت الصحيح', 'Correct time'))}</label><input class="inp" type="time" name="time" value="08:00"></div>
      <div class="field"><div class="seg"><button type="button" class="on" data-st="0">${esc(T('دخول', 'In'))}</button><button type="button" data-st="1">${esc(T('خروج', 'Out'))}</button></div></div>`;
  } else {
    body = `<div class="field"><label>${esc(T('اليوم', 'Day'))}</label><input class="inp" type="date" name="day" value="${day || today}"></div>
      <div class="field"><label>${esc(T('من الساعة', 'From'))}</label><input class="inp" type="time" name="h1" value="16:00"></div>
      <div class="field"><label>${esc(T('إلى الساعة', 'To'))}</label><input class="inp" type="time" name="h2" value="18:00"></div>`;
  }
  const titles = { leave: T('طلب إجازة', 'Leave request'), manual: T('تصحيح بصمة', 'Punch correction'), overtime: T('طلب عمل إضافي', 'Overtime request') };
  sheet(titles[kind], `<form>${body}<div class="field"><label>${esc(T('السبب (اختياري)', 'Reason (optional)'))}</label><textarea class="inp" name="reason" rows="2"></textarea></div>
      <button class="btn primary block">${esc(T('إرسال الطلب', 'Send request'))}</button></form>`, (el, close) => {
    let span = 'days', st = 0;
    $$('[data-span]', el).forEach(b => b.onclick = () => { span = b.dataset.span; $$('[data-span]', el).forEach(x => x.classList.toggle('on', x === b)); $('.span-days', el).classList.toggle('hidden', span !== 'days'); $('.span-hours', el).classList.toggle('hidden', span !== 'hours'); });
    $$('[data-st]', el).forEach(b => b.onclick = () => { st = Number(b.dataset.st); $$('[data-st]', el).forEach(x => x.classList.toggle('on', x === b)); });
    $('form', el).onsubmit = async (ev) => {
      ev.preventDefault();
      const v = (n) => ($(`[name=${n}]`, el) || {}).value;
      let data = { kind, reason: v('reason') };
      if (kind === 'leave') data = span === 'days' ? { ...data, leave_type_id: Number(v('type')), start: v('d1'), end: v('d2') }
        : { ...data, leave_type_id: Number(v('type')), start: `${v('hd')} ${v('h1')}`, end: `${v('hd')} ${v('h2')}` };
      else if (kind === 'manual') data = { ...data, time: `${v('day')} ${v('time')}`, state: st };
      else data = { ...data, start: `${v('day')} ${v('h1')}`, end: `${v('day')} ${v('h2')}` };
      const b = $('button.primary', el); b.disabled = true;
      try { await POST('/api/me/requests', data); close(); toast(T('أُرسل الطلب ✓ ستصلك النتيجة في الإشعارات', 'Sent ✓ — the answer will come to your inbox'), 'ok'); S.tab = 'req'; render(); }
      catch (e) { toast(e.message, 'bad'); } finally { b.disabled = false; }
    };
  });
}

// ------------------------------------------------------------------ Inbox
async function pageBell(p) {
  p.innerHTML = `<div class="top"><h1>${esc(T('الإشعارات', 'Inbox'))}</h1></div><div class="skeleton"></div>`;
  let r; try { r = await GET('/api/me/notifications'); } catch (e) { return; }
  const lv = { good: 'var(--good)', warn: 'var(--warn)', bad: 'var(--bad)', info: 'var(--info)' };
  const ic = { late: 'fix', absent: 'cal', missing_out: 'fix', request_decided: 'req' };
  p.querySelector('.skeleton').replaceWith(h(`<div class="list">${r.rows.length ? r.rows.map(n => `<div class="item note ${n.read ? '' : 'unread'}">
      <span class="ni" style="background:${lv[n.level] || lv.info}">${icon(ic[n.kind] || 'bell')}</span>
      <div><div class="item-top"><b>${esc(n.title)}</b><span class="muted ltr" style="font-size:12px">${esc(n.time.slice(5))}</span></div><div>${esc(n.body)}</div></div>
    </div>`).join('') : `<div class="empty">${icon('bell')}<p>${esc(T('لا توجد إشعارات', 'Nothing here yet'))}</p></div>`}</div>`));
  if (r.rows.some(n => !n.read)) { await POST('/api/me/notifications/read'); S.me.unread = 0; const badge = $('.tab[data-t=bell] .dot-badge'); if (badge) badge.remove(); }
}

// ------------------------------------------------------------------ Account
function pageMe(p) {
  const me = S.me;
  p.innerHTML = `<div class="top"><h1>${esc(T('حسابي', 'Account'))}</h1></div>
    <section class="card" style="display:flex;gap:14px;align-items:center">${avatarHtml().replace('class="avatar"', 'class="avatar" style="width:64px;height:64px;font-size:26px"')}
      <div><b style="font-size:18px">${esc(me.display_name || me.name)}</b><div class="muted">${esc(me.position || '')}${me.position && me.department ? ' · ' : ''}${esc(me.department || '')}</div>
      <div class="muted ltr" style="text-align:start">#${esc(me.code)}</div></div></section>
    <section class="card"><div class="kv"><span>${esc(T('آخر بصمة', 'Last punch'))}</span><b class="ltr">${esc(me.last_punch || '—')}</b></div></section>
    <div class="list">
      <button class="item kind" data-a="lang">${icon('lang')}<b>${S.lang === 'ar' ? 'English' : 'العربية'}</b></button>
      <button class="item kind" data-a="pw">${icon('key')}<b>${esc(T('تغيير كلمة المرور', 'Change password'))}</b></button>
      <button class="item kind" data-a="install">${icon('home')}<b>${esc(T('إضافة إلى الشاشة الرئيسية', 'Add to home screen'))}</b></button>
      <button class="item kind" data-a="out" style="color:var(--bad)">${icon('out')}<b>${esc(T('تسجيل الخروج', 'Sign out'))}</b></button>
    </div>`;
  $$('[data-a]', p).forEach(b => b.onclick = async () => {
    const a = b.dataset.a;
    if (a === 'lang') { setLang(S.lang === 'ar' ? 'en' : 'ar'); POST('/api/me/lang', { lang: S.lang }).catch(() => {}); S.me = await GET('/api/me'); render(); }
    if (a === 'pw') changePassword(false);
    if (a === 'install') { if (S.install) { S.install.prompt(); } else sheet(T('التثبيت على الجوال', 'Install on your phone'), `<p>${esc(T('من قائمة المتصفح اختر «إضافة إلى الشاشة الرئيسية». يفتح بعدها كتطبيق مستقل.', 'From the browser menu choose “Add to Home screen”. It then opens like an app.'))}</p>`); }
    if (a === 'out') { await POST('/api/me/logout'); S.me = null; showLogin(); }
  });
}
function changePassword(force) {
  if (S.passwordOpen) return;
  force = !!force || !!S.me?.must_change;
  S.passwordOpen = true;
  sheet(force ? T('اختر كلمة مرور جديدة', 'Choose a new password') : T('تغيير كلمة المرور', 'Change password'), `<form>
    ${force ? `<p class="muted">${esc(T('هذه أول مرة تدخل فيها. اختر كلمة مرور خاصة بك.', 'This is your first sign-in. Choose your own password.'))}</p>` : ''}
    <div class="field"><label>${esc(T('كلمة المرور الحالية', 'Current password'))}</label><input class="inp ltr" type="password" name="old" required></div>
    <div class="field"><label>${esc(T('كلمة المرور الجديدة (10 أحرف على الأقل)', 'New password (10+ characters)'))}</label><input class="inp ltr" type="password" name="new" minlength="10" maxlength="200" autocomplete="new-password" required></div>
    <div class="field"><label>${esc(T('تأكيد كلمة المرور', 'Confirm password'))}</label><input class="inp ltr" type="password" name="confirm" autocomplete="new-password" required></div>
    <button class="btn primary block">${esc(T('حفظ', 'Save'))}</button></form>`, (el, close) => {
    $('form', el).onsubmit = async (ev) => {
      ev.preventDefault();
      const b = $('button.primary', el); b.disabled = true;
      try {
        if ($('[name=new]', el).value !== $('[name=confirm]', el).value) throw new Error(T('كلمتا المرور غير متطابقتين', 'Passwords do not match'));
        await POST('/api/me/password', { old: $('[name=old]', el).value, new: $('[name=new]', el).value }); S.me.must_change = false; close(); toast(T('تم الحفظ ✓', 'Saved ✓'), 'ok'); render();
      }
      catch (e) { toast(e.message, 'bad'); }
      finally { b.disabled = false; }
    };
  }, !force, () => { S.passwordOpen = false; });
}

// ------------------------------------------------------------------ bottom sheet
function sheet(title, html, onOpen, closable = true, onClose) {
  const s = h(`<div class="scrim"><div class="sheet" role="dialog" aria-modal="true"><div class="grab"></div><h2>${esc(title)}</h2><div class="sheet-body">${html}</div></div></div>`);
  const close = () => { s.remove(); onClose?.(); };
  if (closable) s.addEventListener('click', (e) => { if (e.target === s) close(); });
  document.body.appendChild(s);
  if (onOpen) onOpen(s, close);
  return close;
}

window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); S.install = e; });
// keep the inbox badge fresh while the app is open
setInterval(async () => {
  if (!S.me || document.visibilityState !== 'visible') return;
  try { const me = await GET('/api/me'); if (me.unread !== S.me.unread) { S.me = me; if (S.tab !== 'bell') { const t = S.tab; render(); S.tab = t; } } } catch (e) { /* offline */ }
}, 60000);
boot();
