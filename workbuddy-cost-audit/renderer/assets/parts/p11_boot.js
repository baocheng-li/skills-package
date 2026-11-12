/* ================= 启动 ================= */
(function boot(){
  // 轻提示容器（注意别叫 toast：p03_data.js 里有个同名函数，同名会把函数遮住）
  const toastEl = document.createElement('div');
  toastEl.id = 'toast';
  document.body.appendChild(toastEl);

  // 积分监测：先按日志实时算今日消耗，再套用页内人工配置
  recalcBudget();

  // 侧边栏与顶栏口径：全部来自本次采集结果
  const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };

  /* 侧栏三行：工具类型 / 版本 / 用户名。
     都来自本次采集（meta.sources、meta.user），不是写死的文案——
     报告可能来自 WorkBuddy，也可能来自 CodeBuddy，写死一个就是错的。 */
  const SRC = (META.sources || []).filter(s=>s && s.tool);
  const toolTxt = SRC.length
    ? [...new Set(SRC.map(s=>s.tool))].join(' / ')
    : '—';
  const verTxt = SRC.length
    ? [...new Set(SRC.map(s=>s.version).filter(Boolean))].join(' / ') || '未上报'
    : '未上报';
  setTxt('sfTool', toolTxt);
  setTxt('sfVer', (META.appVersion ? 'v' + META.appVersion : '—')
    + (verTxt === '未上报' ? '' : ' · 客户端 ' + verTxt));
  setTxt('sfUser', META.user || '—');
  const tip = document.getElementById('sfTool');
  if (tip) tip.title = SRC.length
    ? SRC.map(s=>s.tool + (s.version? ' ' + s.version : '') + '  ' + s.root).join('\n')
    : '未发现客户端日志目录';
  syncBalanceChrome();
  // 积分实测率改为按钮的悬浮说明；「数据更新」= 按本次采集快照重算并重绘全部页面
  const br = document.getElementById('btnRefresh');
  if (br){
    br.title = '积分实测率 ' + pct1(COVERAGE.creditAccuracy || 0)
      + ' · 点击按本次采集快照重算并重绘全部页面';
    br.addEventListener('click', ()=>refreshAllData());
  }
  const st0 = document.getElementById('tbStamp');
  if (st0) st0.textContent = '采集于 ' + (META.generatedAt || '—').slice(5, 16);

  // 任务监测：不可见执行次数；台账：异常任务数
  const mt = (MON.totals || {});
  setTxt('nbMon', String(mt.unseenTasks || 0));
  setTxt('nbAnom', String((RAW.anomalies || []).length));

  // 首屏按 hash 落地（支持 #recon/flow 这类二级深链）；无 hash 进监控审计舱
  const r0 = parseHash() || {page:'cockpit', sub:''};
  renderView(r0.page, r0.sub);

  // 浏览器前进/后退：hash 变了要跟着换页，否则地址栏与内容会不一致
  window.addEventListener('hashchange', ()=>{
    const r = parseHash();
    if (!r) return;
    if (r.page !== RT.page || r.sub !== RT.sub) renderView(r.page, r.sub);
  });

  /* ---------- 全息主题切换 ----------
     深色 = 监控大屏（长时间盯屏、环境光弱），浅色 = SaaS 后台（白天、要打印）。
     切换必须重绘图表：ECharts 是 canvas，已画好的像素不会因为 CSS 变量变了
     就重算，不重绘会出现「浅色页面 + 深色坐标轴」的花屏。 */
  (function bindSkin(){
    const root = document.documentElement;
    let saved = null;
    try { saved = localStorage.getItem('wbc-skin'); } catch (e) {}
    if (saved === 'light' || saved === 'dark') root.setAttribute('data-skin', saved);
    const btn = document.getElementById('skinTgl');
    if (!btn) return;
    btn.addEventListener('click', ()=>{
      const next = root.getAttribute('data-skin') === 'light' ? 'dark' : 'light';
      root.setAttribute('data-skin', next);
      try { localStorage.setItem('wbc-skin', next); } catch (e) {}
      VAR_CACHE = {};
      TT = themeTokens();
      refreshAllData(true);
      toast(next === 'light'
        ? '已切换浅色全息主题 · 适合白天与打印'
        : '已切换深色全息主题 · 适合长时间盯屏');
    });
  })();
})();
