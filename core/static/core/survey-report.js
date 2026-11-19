(() => {
  let pending = false;
  async function refreshReport(force = false) {
    const report = document.getElementById('survey-report');
    if (!report || pending || document.hidden) return;
    if (!force && report.contains(document.activeElement) &&
        document.activeElement.matches('input,select,textarea,button')) return;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    pending = true;
    try {
      const response = await fetch(report.dataset.refreshUrl, {
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      });
      if (!response.ok || response.redirected) throw new Error('Session or network');
      const documentCopy = new DOMParser().parseFromString(await response.text(), 'text/html');
      const updated = documentCopy.getElementById('survey-report');
      if (!updated) throw new Error('Missing report');
      for (const detail of report.querySelectorAll('details[open][data-survey-detail]')) {
        const match = [...updated.querySelectorAll('details[data-survey-detail]')]
          .find(item => item.dataset.surveyDetail === detail.dataset.surveyDetail);
        if (match) match.open = true;
      }
      report.replaceWith(updated);
    } catch (_) {
      const status = document.getElementById('survey-refresh-status');
      if (status) status.textContent = 'Не удалось обновить данные. Повторим автоматически; при необходимости обновите страницу и войдите заново.';
    } finally {
      clearTimeout(timeout);
      pending = false;
    }
  }
  document.addEventListener('click', event => {
    if (event.target.closest('[data-refresh-survey]')) refreshReport(true);
  });
  document.addEventListener('visibilitychange', () => refreshReport());
  window.addEventListener('pageshow', event => { if (event.persisted) refreshReport(); });
  setInterval(refreshReport, 30000);
})();
