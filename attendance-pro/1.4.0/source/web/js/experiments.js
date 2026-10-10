/* Hader — synthetic trial runs. No production attendance data is created here. */
'use strict';

const ExperimentsPage = {
  defaults: { employees: 1000, devices: 4, days: 14, repeat: 3, export_csv: true, ingestion: true, keep_write_off: true },
  status(s) {
    const labels = {
      queued: [T('في الانتظار', 'Queued'), 'info'], running: [T('قيد التشغيل', 'Running'), 'info'],
      passed: [T('نجحت', 'Passed'), 'ok'], failed: [T('فشلت', 'Failed'), 'bad'],
      cancelled: [T('أُلغيت', 'Cancelled'), ''], interrupted: [T('توقفت', 'Interrupted'), 'warn'],
    };
    return labels[s] || [T('غير معروفة', 'Unknown'), ''];
  },
  active(job) { return job && ['queued', 'running'].includes(job.status); },
  number(value, digits = 0) {
    return Number.isFinite(Number(value)) && value !== null && value !== undefined
      ? new Intl.NumberFormat(App.lang === 'ar' ? 'ar' : 'en', { maximumFractionDigits: digits }).format(Number(value)) : '—';
  },
  seconds(value) { return `${this.number(value, 4)} ${T('ث', 's')}`; },
  date(value) {
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString(App.lang === 'ar' ? 'ar' : 'en');
  },
  badge(job) { const [label, cls] = this.status(job.status); return `<span class="badge ${cls}">${esc(label)}</span>`; },
  stage(stage) {
    const labels = {
      queued: ['بانتظار البدء', 'Waiting to start'], preparing: ['تجهيز التجربة', 'Preparing the trial'],
      setup: ['تجهيز التجربة', 'Preparing the trial'], seeding: ['إنشاء البيانات التجريبية', 'Creating sample data'],
      seed: ['إنشاء البيانات التجريبية', 'Creating sample data'], reports: ['التحقق من التقارير', 'Checking reports'],
      export: ['تصدير CSV والتحقق منه', 'Exporting and checking CSV'],
      ingestion: ['محاكاة استقبال الحركات', 'Simulating attendance uploads'],
      finalizing: ['حفظ النتائج', 'Saving results'], complete: ['اكتملت التجربة', 'Trial completed'],
      finished: ['اكتملت التجربة', 'Trial completed'], passed: ['اكتملت التجربة', 'Trial completed'],
      cancelled: ['أُلغيت التجربة', 'Trial cancelled'], failed: ['فشلت التجربة', 'Trial failed'],
      interrupted: ['توقفت التجربة', 'Trial interrupted'],
    };
    return labels[stage] ? T(...labels[stage]) : T('التجربة قيد التشغيل', 'Trial in progress');
  },
  async render(container) {
    title(container, T('التشغيل التجريبي', 'Trial runs'));
    const page = h(`<div class="exp-page">
      <p class="exp-intro">${esc(T('اختبر البرنامج بموظفين وأجهزة وحركات مولّدة، دون إضافة شيء إلى سجلات الحضور الفعلية أو الاتصال بأجهزة البصمة.', 'Try generated employees, terminals and attendance records without adding to real attendance or connecting to physical terminals.'))}</p>
      <div class="exp-layout">
        <section class="card2 exp-panel" aria-labelledby="exp-config-title">
          <h2 id="exp-config-title">${esc(T('إعداد التجربة', 'Set up a trial'))}</h2>
          <div class="exp-presets">
            <button type="button" class="btn small" data-preset="quick">${esc(T('تجربة سريعة', 'Quick trial'))}</button>
            <button type="button" class="btn small" data-preset="organization">${esc(T('10,000 موظف · 20 جهازاً', '10,000 employees · 20 terminals'))}</button>
          </div>
          <form class="exp-form">
            <div class="exp-fields">${[
              ['employees', T('عدد الموظفين', 'Employees'), 50000], ['devices', T('عدد الأجهزة', 'Terminals'), 100],
              ['days', T('عدد الأيام', 'Days'), 365], ['repeat', T('تكرار قياس التقارير', 'Report timing repeats'), 5],
            ].map(([key, label, max]) => `<label class="exp-field" for="exp-${key}">${esc(label)}
              <input class="inp" id="exp-${key}" name="${key}" type="number" inputmode="numeric" min="1" max="${max}" step="1" required value="${this.defaults[key]}"></label>`).join('')}</div>
            <fieldset class="exp-options"><legend class="hidden">${esc(T('خيارات التحقق', 'Validation options'))}</legend>
              <label class="exp-option"><input type="checkbox" name="export_csv" checked><span><strong>${esc(T('تصدير الحركات إلى CSV والتحقق منها', 'Export and validate attendance CSV'))}</strong><small>${esc(T('يتيح تنزيل الملف الناتج بعد نجاح التجربة.', 'The generated file is available to download after a successful trial.'))}</small></span></label>
              <label class="exp-option"><input type="checkbox" name="ingestion" checked><span><strong>${esc(T('محاكاة استقبال متزامن من الأجهزة', 'Simulate simultaneous terminal uploads'))}</strong><small>${esc(T('يعيد إرسال حركات مولّدة للتحقق من منع التكرار. لا يستخدم أجهزة حقيقية.', 'Replays generated records to check duplicate prevention. Uses no physical terminals.'))}</small></span></label>
            </fieldset>
            <div class="exp-safety">
              <div class="exp-write-state">${esc(T('الكتابة على أجهزة التشغيل:', 'Writing to production terminals:'))} <span data-write-state>—</span></div>
              <label class="exp-option"><input type="checkbox" name="keep_write_off" checked><span><strong>${esc(T('إيقاف الكتابة على أجهزة التشغيل أثناء التجربة', 'Turn off writing to production terminals for the trial'))}</strong><small>${esc(T('يُحفظ الإعداد مطفأً عند البدء ويبقى مطفأً بعد الانتهاء. إزالة العلامة تترك الإعداد الحالي كما هو، ولا تفعّل الكتابة.', 'Saves the setting as off at startup and leaves it off afterward. Unchecking leaves the current setting unchanged; it does not turn writes on.'))}</small></span></label>
            </div>
            <div class="exp-estimate" data-estimate aria-live="polite"></div>
            <button type="submit" class="btn primary" data-start disabled>${icon('check')}${esc(T('ابدأ التجربة', 'Start trial'))}</button>
            <p class="exp-note">${esc(T('لكل موظف دخول وخروج يومياً من 08:00 إلى 16:00، مع حالات تأخير لاختبار الحساب. قد تستغرق التجربة الكبيرة عدة دقائق وتؤثر مؤقتاً في سرعة الجهاز.', 'Each employee has a daily 08:00–16:00 shift, with late arrivals to verify calculations. A large trial may take several minutes and temporarily slow this computer.'))}</p>
          </form>
          <div class="exp-history"><h3>${esc(T('التجارب السابقة', 'Recent trials'))}</h3><div class="exp-jobs"><p class="exp-loading">${esc(T('جارٍ التحميل…', 'Loading…'))}</p></div></div>
        </section>
        <section class="exp-result" aria-label="${esc(T('نتائج التجربة', 'Trial results'))}"></section>
      </div>
    </div>`);
    container.appendChild(page);
    const form = $('.exp-form', page), start = $('[data-start]', page), estimate = $('[data-estimate]', page);
    const results = $('.exp-result', page), jobsBox = $('.exp-jobs', page);
    let jobs = [], selected = null, ready = false, busy = false, mutating = false, revision = 0;
    let limits = { employees: 50000, devices: 100, days: 365, repeat: 5, punches: 2000000 };
    let jobsMarkup = '', resultMarkup = '', lastError = '';
    const alive = () => page.isConnected;
    const config = () => {
      const values = {};
      for (const key of ['employees', 'devices', 'days', 'repeat']) values[key] = Number(form.elements[key].value);
      for (const key of ['export_csv', 'ingestion', 'keep_write_off']) values[key] = form.elements[key].checked;
      return values;
    };
    const updateForm = () => {
      const values = config(), punches = values.employees * values.days * 2;
      const valid = ['employees', 'devices', 'days', 'repeat'].every(key =>
        form.elements[key].value !== '' && Number.isSafeInteger(values[key]) && values[key] > 0 && values[key] <= limits[key]);
      const over = valid && punches > limits.punches;
      const tooManyDevices = valid && values.devices > values.employees;
      form.elements.devices.max = valid ? Math.min(limits.devices, values.employees) : limits.devices;
      estimate.classList.toggle('invalid', !valid || over || tooManyDevices);
      estimate.innerHTML = !valid ? esc(T('أدخل أعداداً صحيحة ضمن الحدود المحددة.', 'Enter whole numbers within the allowed limits.'))
        : tooManyDevices ? esc(T('يجب ألا يزيد عدد الأجهزة عن عدد الموظفين في التجربة.', 'The trial must have at least one employee per terminal.'))
        : `${esc(T('الحركات المتوقعة:', 'Expected records:'))} <strong>${esc(this.number(punches))}</strong><br><small>${esc(over
          ? T(`تتجاوز هذه التجربة الحد الأقصى (${this.number(limits.punches)} حركة). قلّل الموظفين أو الأيام.`, `This trial exceeds the ${this.number(limits.punches)}-record limit. Reduce employees or days.`)
          : T('تُحفظ نتائج التحقق والملفات التجريبية فقط. بياناتك الفعلية تبقى مستقلة.', 'Only validation results and trial files are retained. Your real records remain separate.'))}</small>`;
      start.disabled = !ready || mutating || !valid || over || tooManyDevices || jobs.some(job => this.active(job));
    };
    const setWriteStatus = enabled => {
      $('[data-write-state]', page).innerHTML = `<span class="badge ${enabled ? 'warn' : 'ok'}">${esc(enabled ? T('مفعّلة', 'Enabled') : T('مطفأة', 'Off'))}</span>`;
    };
    const paintJobs = () => {
      const markup = jobs.length ? jobs.map(job => `<button type="button" class="exp-job" data-job="${esc(job.id)}" aria-pressed="${job.id === selected}">
        <span>${esc(this.number(job.config?.employees))} ${esc(T('موظف', 'employees'))} · ${esc(this.number(job.config?.days))} ${esc(T('يوم', 'days'))}<small>${esc(this.date(job.created_at))}</small></span>${this.badge(job)}</button>`).join('')
        : `<p class="exp-note">${esc(T('لا توجد تجارب بعد. ابدأ بتجربة سريعة.', 'No trials yet. Start with a quick trial.'))}</p>`;
      if (markup === jobsMarkup) return;
      jobsMarkup = markup; jobsBox.innerHTML = markup;
      $$('[data-job]', jobsBox).forEach(button => button.onclick = () => {
        revision++; selected = button.dataset.job; resultMarkup = ''; paintJobs();
        const job = jobs.find(item => item.id === selected); if (job) paintResult(job);
        refresh();
      });
    };
    const mutate = async action => {
      if (mutating) return;
      mutating = true; revision++; updateForm();
      try { await action(); } catch (error) { if (alive()) toast(error.message || String(error), 'bad'); }
      finally { mutating = false; if (alive()) { updateForm(); refresh(); } }
    };
    const paintResult = job => {
      if (!alive()) return;
      const markup = job ? this.resultHtml(job) : `<div class="card2 exp-empty"><div>${icon('db')}<h3>${esc(T('شاهد نتيجة التجربة هنا', 'Your trial results appear here'))}</h3><p>${esc(T('اختر الأعداد والخيارات ثم ابدأ. ستظهر مراحل التقدم ونتائج الحساب وسرعة التقارير، ويمكنك تنزيل النتائج.', 'Choose the numbers and options, then start. Follow progress, calculation checks and report timings, and download the results.'))}</p></div></div>`;
      if (markup === resultMarkup) return;
      // Preserve the open sample panels and button focus across progress updates.
      const opened = $$('details[open]', results).map(element => element.dataset.sample);
      const focus = results.contains(document.activeElement) ? document.activeElement.dataset.action : null;
      resultMarkup = markup; results.innerHTML = markup;
      opened.forEach(key => { const detail = $(`details[data-sample="${key}"]`, results); if (detail) detail.open = true; });
      if (focus) $(`[data-action="${focus}"]`, results)?.focus({ preventScroll: true });
      if (!job) return;
      const encoded = encodeURIComponent(job.id);
      const cancel = $('[data-action="cancel"]', results), remove = $('[data-action="delete"]', results);
      if (cancel) cancel.onclick = () => mutate(async () => {
        const updated = await POST(`/api/experiments/${encoded}/cancel`);
        if (alive() && selected === job.id && updated?.id) paintResult(updated);
      });
      if (remove) remove.onclick = async () => {
        if (mutating) return;
        if (!await confirmBox(T('حذف نتائج هذه التجربة وملفاتها التجريبية؟ لا تتأثر سجلات الحضور الفعلية.', 'Delete this trial’s results and generated files? Real attendance records are unaffected.'))) return;
        if (!alive()) return;
        mutate(async () => {
          await DEL(`/api/experiments/${encoded}`);
          if (!alive()) return;
          jobs = jobs.filter(item => item.id !== job.id);
          if (selected === job.id) selected = jobs[0]?.id || null;
          paintJobs(); paintResult(jobs.find(item => item.id === selected));
        });
      };
      $$('[data-download]', results).forEach(button => button.onclick = () =>
        download(`/api/experiments/${encoded}/download?kind=${encodeURIComponent(button.dataset.download)}`));
    };
    const refresh = async () => {
      if (!alive() || busy || mutating) return;
      busy = true; const currentRevision = revision;
      try {
        const list = await GET('/api/experiments');
        if (!alive() || revision !== currentRevision) return;
        jobs = Array.isArray(list.jobs) ? list.jobs : [];
        if (list.limits) limits = { ...limits, ...list.limits };
        for (const key of ['employees', 'devices', 'days', 'repeat']) form.elements[key].max = limits[key];
        ready = true; lastError = ''; setWriteStatus(list.write_back === true);
        if (!selected || !jobs.some(job => job.id === selected)) selected = jobs[0]?.id || null;
        paintJobs(); updateForm();
        if (!selected) { paintResult(null); return; }
        const requesting = selected;
        const job = await GET(`/api/experiments/${encodeURIComponent(requesting)}`);
        if (!alive() || revision !== currentRevision || selected !== requesting) return;
        jobs = jobs.map(item => item.id === job.id ? job : item);
        paintJobs(); updateForm();
        paintResult(job);
      } catch (error) {
        if (!alive() || revision !== currentRevision) return;
        const message = error.message || String(error);
        if (lastError !== message) { toast(message, 'bad'); lastError = message; }
        if (!ready) jobsBox.innerHTML = `<p class="exp-error">${esc(T('تعذر تحميل التجارب. سيُعاد الاتصال تلقائياً.', 'Could not load trials. Reconnecting automatically.'))}</p>`;
      } finally { busy = false; }
    };
    form.oninput = updateForm;
    form.onsubmit = event => {
      event.preventDefault(); updateForm();
      if (start.disabled || !form.reportValidity()) return;
      mutate(async () => {
        const job = await POST('/api/experiments', config());
        if (!alive()) return;
        jobs = [job, ...jobs.filter(item => item.id !== job.id)]; selected = job.id;
        paintJobs(); paintResult(job);
        if (job.config?.keep_write_off) setWriteStatus(false);
      });
    };
    $$('[data-preset]', page).forEach(button => button.onclick = () => {
      const preset = button.dataset.preset === 'quick' ? { employees: 50, devices: 2, days: 7, repeat: 1 }
        : { employees: 10000, devices: 20, days: 30, repeat: 3 };
      for (const [key, value] of Object.entries(preset)) form.elements[key].value = value;
      updateForm();
    });
    updateForm(); paintResult(null);
    const timer = setInterval(() => { if (!alive()) { clearInterval(timer); return; } refresh(); }, 1000);
    App.timers.push(timer);
    await refresh();
  },
  resultHtml(job) {
    const running = this.active(job), result = job.result, progress = job.progress || {};
    const percent = job.status === 'passed' ? 100 : Number.isFinite(Number(progress.percent)) && progress.percent !== null && progress.percent !== undefined ? Math.min(100, Math.max(0, Number(progress.percent))) : null;
    const canDownload = job.status === 'passed';
    return `<section class="card2 exp-panel">
      <div class="exp-result-head"><div><h2>${esc(T('نتيجة التجربة', 'Trial result'))} ${this.badge(job)}</h2><small>${esc(this.date(job.created_at))} · ${esc(this.number(job.config?.employees))} ${esc(T('موظف', 'employees'))} · ${esc(this.number(job.config?.devices))} ${esc(T('جهازاً', 'terminals'))} · ${esc(this.number(job.config?.days))} ${esc(T('يوماً', 'days'))}</small></div>
        <div class="exp-actions">${running ? `<button type="button" class="btn danger small" data-action="cancel">${esc(T('إيقاف التجربة', 'Stop trial'))}</button>`
          : `<button type="button" class="btn danger small" data-action="delete">${icon('del')}${esc(T('حذف التجربة', 'Delete trial'))}</button>`}
          ${canDownload && job.downloads?.json ? `<button type="button" class="btn small" data-action="json" data-download="json">${icon('download')}${esc(T('تنزيل النتائج', 'Download results'))}</button>` : ''}
          ${canDownload && job.downloads?.csv ? `<button type="button" class="btn small" data-action="csv" data-download="csv">${icon('download')}${esc(T('تنزيل الحركات CSV', 'Download attendance CSV'))}</button>` : ''}</div>
      </div>
      <div class="exp-progress"><div class="exp-progress-line" role="status" aria-live="polite"><strong>${esc(running ? this.stage(progress.stage) : this.status(job.status)[0])}</strong>${percent !== null ? `<span>${esc(this.number(percent))}%</span>` : ''}</div>
        ${running || job.status === 'passed' ? `<progress max="100" ${percent !== null ? `value="${job.status === 'passed' ? 100 : percent}"` : ''} aria-label="${esc(T('تقدم التجربة', 'Trial progress'))}"></progress>` : ''}
        ${running && progress.message ? `<p class="exp-note">${esc(progress.message)}</p>` : ''}
        ${running ? `<p class="exp-note">${esc(T('يمكنك مغادرة هذه الصفحة والعودة لمتابعة النتيجة. إغلاق البرنامج يوقف التجربة.', 'You can leave this page and return to check the result. Closing the application interrupts the trial.'))}</p>` : ''}
      </div>${job.error ? `<p class="exp-error">${esc(job.error)}</p>` : ''}
      ${job.status === 'interrupted' ? `<p class="exp-note">${esc(T('توقفت هذه التجربة بسبب إغلاق البرنامج أو إعادة تشغيله. يمكنك بدء تجربة جديدة.', 'This trial was interrupted when the application closed or restarted. You can start a new trial.'))}</p>` : ''}
    </section>${result && job.status === 'passed' ? this.summaryHtml(result) : ''}${job.preview && job.status === 'passed' ? this.previewHtml(job.preview) : ''}`;
  },
  summaryHtml(result) {
    const metric = (value, label) => `<div class="exp-metric"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`;
    const reports = result.reports || {};
    const rows = [
      ['daily', T('الحضور اليومي', 'Daily attendance')], ['summary', T('ملخص الحضور', 'Attendance summary')],
      ['transactions', T('سجل الحركات', 'Attendance records')],
    ].filter(([key]) => reports[key]).map(([key, label]) => {
      const item = reports[key];
      return `<tr><td>${esc(label)}</td><td>${esc(this.seconds(item.median_seconds))}</td><td>${esc(this.seconds(item.min_seconds))}</td><td>${esc(this.seconds(item.max_seconds))}</td><td>${esc(this.number(item.total))}</td></tr>`;
    }).join('');
    const check = (heading, detail) => `<div class="exp-check">${icon('check')}<div><strong>${esc(heading)}</strong><small>${esc(detail)}</small></div></div>`;
    return `<div class="exp-metrics">
      ${metric(this.number(result.employees), T('موظف تجريبي', 'Sample employees'))}
      ${metric(this.number(result.devices), T('جهاز محاكى', 'Simulated terminals'))}
      ${metric(this.number(result.days), T('يوم حضور', 'Attendance days'))}
      ${metric(this.number(result.punches), T('حركة حضور', 'Attendance records'))}
      ${metric(this.seconds(result.total_seconds), T('مدة التجربة الكاملة', 'Total trial time'))}
      ${metric(this.seconds(result.seed_seconds), T('وقت إنشاء البيانات', 'Sample creation time'))}
    </div><section class="card2 exp-panel"><h2>${esc(T('سرعة التقارير', 'Report speed'))}</h2>
      <p class="exp-note">${esc(T('الأزمنة لفتح صفحة من التقرير؛ قد يكون القياس الأول أبطأ. تتغير النتائج حسب سرعة الكمبيوتر وحجمه.', 'Timings measure opening a report page; the first run may be slower. Results vary with computer performance and workload.'))}</p>
      <div class="exp-table-wrap"><table class="exp-table"><thead><tr><th>${esc(T('التقرير', 'Report'))}</th><th>${esc(T('الزمن الوسيط', 'Median time'))}</th><th>${esc(T('الأسرع', 'Fastest'))}</th><th>${esc(T('الأبطأ', 'Slowest'))}</th><th>${esc(T('إجمالي الصفوف', 'Total rows'))}</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="exp-checks">${check(T('اجتاز حساب الحضور والتحقق من التقارير', 'Attendance calculations and report checks passed'), T('تشمل التجربة حالات حضور وتأخير، مع التحقق من الإجماليات والصفحات.', 'Checks include on-time and late arrivals, totals and report pages.'))}
      ${result.export ? check(T('اجتاز تصدير CSV والتحقق من جميع صفوفه', 'CSV export and every-row validation passed'), T(`${this.number(result.export.data_rows)} حركة · ${this.seconds(result.export.seconds)} للتصدير · ${this.number(result.export.size_bytes / 1048576, 1)} MB`, `${this.number(result.export.data_rows)} records · ${this.seconds(result.export.seconds)} to export · ${this.number(result.export.size_bytes / 1048576, 1)} MB`)) : ''}
      ${result.ingestion ? check(T('اجتاز الاستقبال المتزامن ومنع تكرار الحركات', 'Simultaneous uploads and duplicate prevention passed'), T(`${this.number(result.ingestion.parallel_devices)} جهازاً محاكياً · ${this.number(result.ingestion.http_200_replies)} استجابة ناجحة · ${this.number(result.ingestion.reuploaded_records)} حركة مُعادة · ${this.seconds(result.ingestion.seconds)}`, `${this.number(result.ingestion.parallel_devices)} simulated terminals · ${this.number(result.ingestion.http_200_replies)} successful replies · ${this.number(result.ingestion.reuploaded_records)} replayed records · ${this.seconds(result.ingestion.seconds)}`)) : ''}</div>
    </section><p class="exp-note">${esc(T('هذه النتيجة تثبت عمل البرنامج على البيانات المولّدة. يلزم اختبار مستقل لأجهزة البصمة الحقيقية وسعتها واتصالها.', 'This result verifies software behavior with generated data. Physical terminal capacity and connectivity require a separate test.'))}</p>`;
  },
  previewHtml(preview) {
    const employees = Array.isArray(preview.employees) ? preview.employees.slice(0, 20) : [];
    const daily = Array.isArray(preview.daily) ? preview.daily.slice(0, 20) : [];
    if (!employees.length && !daily.length) return '';
    return `<section class="card2 exp-panel exp-preview"><h2>${esc(T('عينة من البيانات التجريبية', 'Sample trial data'))}</h2>
      ${employees.length ? `<details data-sample="employees"><summary>${esc(T('الموظفون المولّدون', 'Generated employees'))}</summary><div class="exp-table-wrap"><table class="exp-table"><thead><tr><th>${esc(T('رقم الموظف', 'Employee code'))}</th><th>${esc(T('الاسم', 'Name'))}</th></tr></thead><tbody>${employees.map(row => `<tr><td>${esc(row.emp_code)}</td><td>${esc(row.name)}</td></tr>`).join('')}</tbody></table></div></details>` : ''}
      ${daily.length ? `<details data-sample="daily"><summary>${esc(T('نتائج الحضور اليومي', 'Daily attendance results'))}</summary><div class="exp-table-wrap"><table class="exp-table"><thead><tr>${[T('الموظف', 'Employee'), T('التاريخ', 'Date'), T('الدخول', 'Check-in'), T('الخروج', 'Check-out'), T('التأخير (دقيقة)', 'Late (minutes)'), T('الحالة', 'Status')].map(label => `<th>${esc(label)}</th>`).join('')}</tr></thead><tbody>${daily.map(row => `<tr><td>${esc(row.name || row.emp_code)}</td><td>${esc(row.date)}</td><td>${esc(row.clock_in)}</td><td>${esc(row.clock_out)}</td><td>${esc(this.number(row.late))}</td><td>${statusBadge(row.status)}</td></tr>`).join('')}</tbody></table></div></details>` : ''}
    </section>`;
  },
};
