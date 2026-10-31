/* Shared chart theme reads the same semantic tokens as buttons and forms. */
(() => {
  const styles = getComputedStyle(document.documentElement);
  const token = name => styles.getPropertyValue(name).trim();
  const theme = {
    primary: token('--primary'), ink: token('--ink'), muted: token('--muted'),
    success: token('--success'), warning: token('--warning'), danger: token('--danger'),
    categorical: ['#165dce', '#087e9c', '#7053b5', '#a56b0a', '#526b91', '#b14d81'],
    heatmap: ['#edf4ff', '#87b4f5', '#165dce', '#103d82'],
  };
  window.BankTheme = Object.freeze(theme);
  echarts.registerTheme('banking', {
    color: theme.categorical,
    backgroundColor: '#ffffff',
    textStyle: { fontFamily: 'Microsoft YaHei, Segoe UI, sans-serif', fontSize: 12, color: theme.muted },
    legend: { textStyle: { fontSize: 11, color: theme.muted } },
    tooltip: { backgroundColor: '#ffffff', borderColor: '#dce4ef', textStyle: { color: theme.ink, fontSize: 12 }, extraCssText: 'box-shadow:0 6px 24px rgba(24,43,73,.12);' },
    categoryAxis: { axisLine: { lineStyle: { color: '#8292a8' } }, axisLabel: { color: theme.muted, fontSize: 11 }, axisTick: { show: false } },
    valueAxis: { axisLabel: { color: theme.muted, fontSize: 11 }, nameTextStyle: { color: theme.muted, fontSize: 11 }, splitLine: { lineStyle: { color: '#e8edf5' } } },
    timeAxis: { axisLabel: { color: theme.muted, fontSize: 11 }, axisLine: { lineStyle: { color: '#8292a8' } } },
    bar: { itemStyle: { borderRadius: [3,3,0,0] } },
    line: { lineStyle: { width: 2.5 }, symbolSize: 6 },
    pie: { itemStyle: { borderColor: '#ffffff', borderWidth: 2 }, label: { color: theme.muted, fontSize: 11 } },
  });
})();
