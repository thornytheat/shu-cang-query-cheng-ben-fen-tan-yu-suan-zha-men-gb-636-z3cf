// 清单归档：打印、复制批次号。打印样式在 print.css，交互元件在打印版隐藏。
(function () {
  const printBtn = document.getElementById('print-btn');
  if (printBtn) {
    printBtn.addEventListener('click', () => window.print());
  }

  const copyBtn = document.getElementById('copy-btn');
  const copyMsg = document.getElementById('copy-msg');
  if (!copyBtn) return;
  copyBtn.addEventListener('click', async () => {
    const value = copyBtn.dataset.copy || '';
    if (!value) {
      if (copyMsg) copyMsg.textContent = '还没有批次号';
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      if (copyMsg) copyMsg.textContent = '已复制 ' + value;
    } catch (error) {
      if (copyMsg) copyMsg.textContent = '复制失败，请手动选中';
    }
  });
})();
