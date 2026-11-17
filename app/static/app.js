// 表单：导入账单与重算闸门。状态集中在 state，请求失败给可见反馈。
const state = { busy: false };

function setMessage(node, text, kind) {
  if (!node) return;
  node.textContent = text;
  node.classList.remove('is-bad', 'is-ok');
  if (kind) node.classList.add(kind);
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  let data = {};
  try {
    data = await response.json();
  } catch (error) {
    data = { message: 'HTTP ' + response.status };
  }
  if (!response.ok) {
    const error = new Error(data.message || 'HTTP ' + response.status);
    error.status = response.status;
    error.code = data.error || '';
    throw error;
  }
  return data;
}

function yuan(cents) {
  const value = Number(cents || 0) / 100;
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function bindImport() {
  const form = document.getElementById('import-form');
  if (!form) return;
  const message = document.getElementById('import-msg');
  const button = document.getElementById('import-submit');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.busy) return;
    const source = document.getElementById('source');
    const charset = document.getElementById('charset');
    if (!source || !source.value) {
      setMessage(message, '先选一份样例', 'is-bad');
      return;
    }
    state.busy = true;
    if (button) button.disabled = true;
    setMessage(message, '导入中…');
    try {
      const data = await postJson('/api/import', {
        source: source.value,
        charset: charset && charset.value ? charset.value : null,
      });
      setMessage(message,
        data.batch_no + ' 完成，总额 ' + yuan(data.total_cents) + ' 元', 'is-ok');
      window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
      setMessage(message, '导入失败：' + error.message + '（换一份样例再试）', 'is-bad');
      if (source) source.setAttribute('aria-invalid', 'true');
    } finally {
      state.busy = false;
      if (button) button.disabled = false;
    }
  });
}

function bindGate() {
  const form = document.getElementById('gate-form');
  if (!form) return;
  const message = document.getElementById('gate-msg');
  const button = document.getElementById('gate-submit');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.busy) return;
    const budgets = {};
    form.querySelectorAll('input[data-dept]').forEach((input) => {
      budgets[input.dataset.dept] = input.value.trim();
    });
    state.busy = true;
    if (button) button.disabled = true;
    setMessage(message, '提交中…');
    try {
      const data = await postJson('/api/gates', {
        batch_id: form.dataset.batch,
        expected_version: Number(form.dataset.version),
        budgets: budgets,
      });
      setMessage(message, '版本 ' + data.version + ' 已重算，保留 ' + yuan(data.kept_cents) + ' 元', 'is-ok');
      window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
      setMessage(message, '提交失败：' + error.message + '（刷新后按新版本再提交）', 'is-bad');
    } finally {
      state.busy = false;
      if (button) button.disabled = false;
    }
  });
}

function bindAppeal() {
  const form = document.getElementById('appeal-form');
  if (!form) return;
  const message = document.getElementById('appeal-msg');
  const button = document.getElementById('appeal-submit');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.busy) return;
    const query = document.getElementById('appeal-query');
    const dept = document.getElementById('appeal-dept');
    const start = document.getElementById('appeal-start');
    const duration = document.getElementById('appeal-duration');
    if (!query || !query.value) {
      setMessage(message, '先选一条查询', 'is-bad');
      return;
    }
    if (!dept || !dept.value.trim()) {
      setMessage(message, '填改判到的部门', 'is-bad');
      return;
    }
    const fix = {};
    if (start && start.value.trim()) fix.started_at = start.value.trim();
    if (duration && duration.value.trim()) fix.duration_s = duration.value.trim();
    const entry = { query_id: query.value, target_dept: dept.value.trim() };
    if (Object.keys(fix).length) entry.fix = fix;
    state.busy = true;
    if (button) button.disabled = true;
    setMessage(message, '提交中…');
    try {
      const data = await postJson('/api/appeals', {
        batch_id: form.dataset.batch,
        expected_version: Number(form.dataset.version),
        appeals: [entry],
      });
      setMessage(message,
        '版本 ' + data.version + ' 采纳 ' + data.applied + ' 条，变动 '
        + yuan(data.diff.moved_cents) + ' 元', 'is-ok');
      window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
      setMessage(message, '提交失败：' + error.message + '（核对查询与部门）', 'is-bad');
    } finally {
      state.busy = false;
      if (button) button.disabled = false;
    }
  });
}

function bindRollback() {
  const form = document.getElementById('rollback-form');
  if (!form) return;
  const message = document.getElementById('rollback-msg');
  const button = document.getElementById('rollback-submit');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.busy) return;
    const target = document.getElementById('rollback-target');
    if (!target || !target.value) {
      setMessage(message, '先选目标版本', 'is-bad');
      return;
    }
    state.busy = true;
    if (button) button.disabled = true;
    setMessage(message, '回滚中…');
    try {
      const data = await postJson('/api/appeals/rollback', {
        batch_id: form.dataset.batch,
        expected_version: Number(form.dataset.version),
        to_version: Number(target.value),
      });
      setMessage(message,
        '已回滚到 v' + data.restored_from + '，新版本 v' + data.version, 'is-ok');
      window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
      setMessage(message, '回滚失败：' + error.message + '（刷新后重试）', 'is-bad');
    } finally {
      state.busy = false;
      if (button) button.disabled = false;
    }
  });
}

bindImport();
bindGate();
bindAppeal();
bindRollback();
