// 表格：点击表头排序。只做展示排序，不改动任何后端结论。
const NUMBER_CLEAN = /[^0-9.\-]/g;

function cellValue(row, index, kind) {
  const cell = row.children[index];
  const text = cell ? cell.textContent.trim() : '';
  if (kind === 'num') {
    const parsed = Number(text.replace(NUMBER_CLEAN, ''));
    return Number.isFinite(parsed) ? parsed : -Infinity;
  }
  return text;
}

function sortTable(table, index, kind, ascending) {
  const body = table.tBodies[0];
  if (!body) return;
  const rows = Array.from(body.rows).filter((row) => row.children.length > index);
  rows.sort((left, right) => {
    const a = cellValue(left, index, kind);
    const b = cellValue(right, index, kind);
    if (a === b) return 0;
    const result = a < b ? -1 : 1;
    return ascending ? result : -result;
  });
  rows.forEach((row) => body.appendChild(row));
}

document.querySelectorAll('table[data-sortable]').forEach((table) => {
  const heads = table.querySelectorAll('th[data-sort]');
  heads.forEach((head, index) => {
    let ascending = true;
    head.addEventListener('click', () => {
      heads.forEach((other) => other.removeAttribute('aria-sort'));
      head.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
      sortTable(table, index, head.dataset.sort, ascending);
      ascending = !ascending;
    });
    head.setAttribute('tabindex', '0');
    head.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        head.click();
      }
    });
  });
});
