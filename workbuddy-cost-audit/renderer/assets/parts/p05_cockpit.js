/* ================= 一级菜单一：监控审计舱 =================
   四个板块，一屏不滚动：
     ① 今日动态监测 —— 四个数（日初结转 / 当前可用 / 今日消耗 / 今日收入）
        + 今日实时消耗累计走向 + 三类不可见执行入口
     ② 周期动态监测 —— 月度 / 季度 / 年度切换，五项规模指标 + 收支柱状图
     ③ 对账审计分析 —— 偏差分析（可跳详情）× 风险审计状态（可跳处置）
     ④ 告警待办   —— 收入维护 / 余额告警 / 日终对账 / 风险审计

   口径（与 references/credit-ledger-schemas.md 一致）：
     日初结转 = 上一日日结余额；当前可用 = 日初 − 今日消耗；
     今日收入只算「已入账」，草稿不计；周期合计一律来自 CRED.days 逐日账。
   数据只覆盖采集窗口内的若干天，切到季度/年度时**如实标注覆盖天数**，
   不做任何按天数外推的估算。 */
const CK = {period:'month'};    // month | quarter | year

const ckSum = (arr, f) => arr.reduce((s,x)=>s + (f(x) || 0), 0);
const ckRound = n => Math.round((n || 0) * 100) / 100;
function ckYmd(d){
  const p = n => String(n).padStart(2,'0');
  return d.getFullYear() + '-' + p(d.getMonth()+1) + '-' + p(d.getDate());
}
function ckAnchor(){
  return String(CRED.anchor || TODAY.date || (BY_DAY[BY_DAY.length-1]||{}).date || '').slice(0,10);
}
function ckRange(kind){
  const a = ckAnchor();
  const d = new Date(a + 'T00:00:00');
  if (isNaN(d.getTime())) return {from:'0000-01-01', to:'9999-12-31', label:'全部'};
  if (kind === 'quarter'){
    const q = Math.floor(d.getMonth() / 3);
    return {from:ckYmd(new Date(d.getFullYear(), q*3, 1)), to:a, label:'Q' + (q+1)};
  }
  if (kind === 'year') return {from:d.getFullYear() + '-01-01', to:a, label:d.getFullYear() + ' 年'};
  return {from:ckYmd(new Date(d.getFullYear(), d.getMonth(), 1)), to:a, label:(d.getMonth()+1) + ' 月'};
}

/* 周期聚合：只用逐日账，不引入任何估算 */
function ckAgg(kind){
  const R = ckRange(kind);
  const all = (CRED.days || []);
  const days = all.filter(x => x.date >= R.from && x.date <= R.to);
  const last = days.length ? days[days.length-1] : null;
  return {
    range:R, days:days,
    income: ckRound(ckSum(days, d=>d.income)),
    spend: ckRound(ckSum(days, d=>d.spend)),
    /* 结转总额取「区间第一天的日初」：它才是上期真正结转进来的额度。
       把每天日初相加会把同一笔钱重复算 N 次，看着像规模很大，其实是错的。 */
    carryIn: days.length ? ckRound(days[0].open) : 0,
    close: last ? ckRound(last.close) : 0,
    pool: ckRound(CRED.initial || (BUDGET||{}).initial || 0),
    covered: days.length,
    window: all.length
  };
}

/* 周期分桶：月度按日、季度按周、年度按月 */
function ckBuckets(kind, days){
  if (!days.length) return [];
  const map = {}, order = [];
  const push = (k, lb, d) => {
    if (!map[k]){ map[k] = {label:lb, income:0, spend:0}; order.push(k); }
    map[k].income += d.income || 0;
    map[k].spend += d.spend || 0;
  };
  const from = new Date(days[0].date + 'T00:00:00');
  days.forEach(d => {
    const dt = new Date(d.date + 'T00:00:00');
    if (kind === 'year'){
      const k = d.date.slice(0,7);
      push(k, (dt.getMonth()+1) + '月', d);
    } else if (kind === 'quarter'){
      const wk = Math.floor((dt - from) / 86400000 / 7) + 1;
      const k = 'w' + wk;
      push(k, '第' + wk + '周', d);
    } else {
      push(d.date, d.date.slice(5).replace('-','/'), d);
    }
  });
  return order.map(k => {
    const b = map[k];
    b.income = ckRound(b.income); b.spend = ckRound(b.spend);
    return b;
  });
}

/* 今日实时走向：按任务开始时刻归到小时，累计成曲线 */
function ckToday(){
  const day = String((BUDGET||{}).today || ckAnchor()).slice(0,10);
  const list = TASKS.filter(t => String(t.ts || '').slice(0,10) === day);
  const hours = new Array(24).fill(0);
  let lastH = 0;
  list.forEach(t => {
    const h = parseInt(String(t.ts || '').slice(11,13), 10);
    if (isNaN(h) || h < 0 || h > 23) return;
    hours[h] += (t.credits || 0);
    if (h > lastH) lastH = h;
  });
  const n = Math.max(1, lastH + 1);
  let acc = 0;
  const cum = hours.slice(0, n).map(v => { acc += v; return ckRound(acc); });
  const inc = hours.slice(0, n).map(v => ckRound(v));
  const total = ckRound(acc);
  const cron = ckRound(list.filter(t=>t.trigger!=='人工').reduce((s,t)=>s+t.credits,0));
  return {day:day, list:list, labels:inc.map((_,i)=>String(i).padStart(2,'0')+':00'),
          inc:inc, cum:cum, total:total, cron:cron, n:list.length};
}

/* 偏差与风险统计 */
function ckReconStats(){
  const days = (CRED.days || []);
  const withBal = days.filter(d => d.account !== null && d.account !== undefined);
  const bad = withBal.filter(d => d.ok === false);
  const worst = bad.concat().sort((a,b)=>Math.abs(b.diff||0)-Math.abs(a.diff||0))[0] || null;
  const pend = days.filter(d => d.provisional || d.status === 'pending');
  const recent = days.slice().reverse().slice(0, 4).map(d=>{
    const has = d.account !== null && d.account !== undefined;
    const diff = has ? (d.diff || 0) : null;
    return {
      a: String(d.date||'').slice(5),
      b: has ? ('账户 ' + fmt0(d.account) + ' · 偏差 ' + (diff>0?'+':'') + fmt1(diff))
             : '未提供账户余额',
      c: has ? (d.ok === false ? 'var(--bad-11)' : 'var(--ok-11)') : 'var(--text3)',
      d: has ? (d.ok === false ? '超容差' : '相符') : '无法比对'
    };
  });
  return {
    days: days.length, withBal: withBal.length,
    recent: recent,
    ok: withBal.filter(d=>d.ok===true).length,
    bad: bad.length, worst: worst,
    nobal: days.filter(d=>d.status==='nobal').length,
    pending: pend.length,
    diffCredits: ckRound(withBal.reduce((s,d)=>s+Math.abs(d.diff||0),0))
  };
}
function ckRiskStats(){
  const list = (typeof ANOMALY_TASKS !== 'undefined' && ANOMALY_TASKS) ? ANOMALY_TASKS : [];
  const pending = list.filter(t => (typeof dispStatus === 'function' ? dispStatus(t) : 'open') === 'open');
  const top = list.slice().sort((a,b)=>(b.waste||0)-(a.waste||0)).slice(0,4).map(t=>({
    a: String(t.id||'').slice(-6),
    b: String(t.name||'').slice(0,16) + ' · ' + (t.ruleIds||[]).join('/'),
    c: 'var(--warn-soft)',
    d: fmt0(t.waste||0) + ' 分'
  }));
  return {
    total: list.length, pending: pending.length, top: top,
    waste: Math.round(list.reduce((s,t)=>s+(t.waste||0),0)),
    rules: (RULES||[]).filter(r=>r.on !== false).length
  };
}

function ckLevelColor(lv){
  return {red:'var(--bad-9)', orange:'var(--warn-soft)', crit:'var(--warn-soft)',
          yellow:'var(--warn-9)', warn:'var(--warn-9)', green:'var(--ok-10)',
          ok:'var(--ok-10)', gray:'var(--text3)'}[lv] || 'var(--text3)';
}

/* ---------- 板块一：今日动态监测 ---------- */
function ckTilesHTML(){
  const B = BUDGET || {};
  const today = (CRED.days||[]).filter(d => d.date === ckAnchor())[0] || null;
  const open = today ? ckRound(today.open) : null;
  const used = today ? ckRound(today.spend) : (B.todaySpent || 0);
  const income = today ? ckRound(today.income) : 0;
  const bal = B.configured ? ckRound(B.balance) : (today ? ckRound(today.close) : null);
  const lvC = ckLevelColor(B.level);
  const tiles = [
    {lb:'日初结转', v:(open===null?'—':fmt0(open)), u:'积分',
     ft:'上一日日结余额', kc:'var(--text)'},
    {lb:'当前可用', v:(bal===null?'<span class="none">未配置</span>':fmt0(bal)), u:(bal===null?'':'积分'),
     ft:B.configured ? ('预警比 ' + (B.spendRatioOver?'&gt;100':fmt1(B.spendRatio||0)) + '%') : '填初始积分与今日余额',
     kc:B.configured ? lvC : 'var(--text3)'},
    {lb:'今日消耗', v:fmt0(used), u:'积分', ft:today ? (used? '按小时累计见下':'今日暂无消耗') : '今日无账',
     kc:'var(--ok-11)'},
    {lb:'今日收入', v:fmt0(income), u:'积分',
     ft:income>0 ? '已入账 + 待确认' : '今日未登记收入', kc:income>0?'var(--info-9)':'var(--text3)'}
  ];
  return tiles.map(t=>
    '<div class="ck-kpi"><div class="lb">'+t.lb+'</div>'
    + '<div class="v" style="color:'+t.kc+'">'+t.v+(t.u?'<span class="u">'+t.u+'</span>':'')+'</div>'
    + '<div class="ft">'+t.ft+'</div></div>').join('');
}

function ckEntriesHTML(){
  const cats = (MON.categories || []);
  const pick = id => cats.filter(c=>c.id===id)[0] || {count:0, credits:0};
  const items = [
    {id:'scheduled', t:'定时任务', c:pick('scheduled')},
    {id:'background', t:'后台隐藏', c:pick('background')},
    {id:'skill', t:'技能 / 子代理', c:pick('skill')}
  ];
  /* 子代理单独并进「技能」这一入口，点进去由执行监测按分类筛选 */
  const mt = MON.totals || {};
  const skillCnt = (pick('skill').count || 0) + (mt.subagentTasks || 0);
  const skillCr = ckRound((pick('skill').credits || 0));
  return items.map((x,i)=>{
    const cnt = i===2 ? skillCnt : (x.c.count || 0);
    const cr = i===2 ? skillCr : ckRound(x.c.credits || 0);
    return '<div class="ck-entry" data-cat="'+x.id+'" role="button" tabindex="0">'
      + '<span class="ck-et">'+x.t+'</span>'
      + '<span class="ck-ev">'+cnt+'<i>次</i></span>'
      + '<span class="tag gray sm" title="该分类累计积分">'+fmt1(cr)+' 分</span>'
      + '</div>';
  }).join('');
}

/* ---------- 板块三：对账审计分析 ---------- */
/* 面板下半部的小明细：不新增一个数字，只把上面那句结论拆成「哪几天 / 哪几个任务」，
   既补满版面，也让结论可以当场核对。 */
function ckMiniList(rows){
  if (!rows || !rows.length) return '<div class="ck-mini-list"><div class="ml-r">'
    + '<span class="ml-t">暂无可列明细</span></div></div>';
  return '<div class="ck-mini-list">' + rows.map(x=>
    '<div class="ml-r"><span class="ml-d">'+esc(x.a)+'</span>'
    + '<span class="ml-t" title="'+esc(x.b)+'">'+esc(x.b)+'</span>'
    + '<span class="ml-v" style="color:'+(x.c||'var(--text2)')+'">'+esc(x.d)+'</span></div>'
  ).join('') + '</div>';
}

function ckReconMini(){
  const s = ckReconStats();
  const total = Math.max(1, s.withBal);
  const okPct = Math.round(s.ok / total * 100);
  const badPct = Math.round(s.bad / total * 100);
  const nobalPct = Math.max(0, 100 - okPct - badPct);
  const worstTxt = s.worst
    ? ('最大偏差 ' + s.worst.date.slice(5) + ' · ' + fmt1(Math.abs(s.worst.diff)) + ' 积分')
    : (s.withBal ? '已比对日无超容差偏差' : '缺可比对的账户余额');
  return '<div class="ck-mini">'
    + '<div class="mh">对账偏差分析'
    + '<span class="go" data-go="recon" data-sub="daily">查看详情 →</span></div>'
    + '<div class="mv">'+s.bad+'<span class="u">天超容差</span></div>'
    + '<div class="ms">已比对 '+s.withBal+' 天 · 相符 '+s.ok+' 天 · 无实际余额 '+s.nobal+' 天'
    + '<br/>'+worstTxt+'</div>'
    + ckMiniList(s.recent)
    + '<div class="ck-mini-ft">容差 = max(0.5 积分, |日初| × '+fmt0(CRED.tolerance||5)+'%)</div>'
    + '<div class="mbar" title="已比对日的状态构成">'
    + '<i style="width:'+okPct+'%;background:var(--ok-9)"></i>'
    + '<i style="width:'+badPct+'%;background:var(--bad-9)"></i>'
    + '<i style="width:'+nobalPct+'%;background:rgba(var(--ink),.18)"></i></div>'
    + '</div>';
}
function ckRiskMini(){
  const r = ckRiskStats();
  const share = r.total ? Math.round(r.pending / r.total * 100) : 0;
  return '<div class="ck-mini">'
    + '<div class="mh">风险审计状态'
    + '<span class="go" data-go="control" data-sub="recycle">去审计 →</span></div>'
    + '<div class="mv" style="color:'+(r.pending?'var(--warn-soft)':'var(--ok-10)')+'">'
    + r.pending+'<span class="u">项待处置 / 共 '+r.total+'</span></div>'
    + '<div class="ms">命中规则 '+r.rules+' 条 · 可回收（账面）'+fmt0(r.waste)+' 积分'
    + '<br/>已处置 '+(r.total-r.pending)+' 项，处置动作均留痕</div>'
    + ckMiniList(r.top)
    + '<div class="ck-mini-ft">可回收为账面口径 · 已消耗不可追回</div>'
    + '<div class="mbar"><i style="width:'+share+'%;background:var(--warn-9)"></i>'
    + '<i style="width:'+(100-share)+'%;background:var(--ok-9)"></i></div>'
    + '</div>';
}

/* ---------- 板块四：告警待办 ---------- */
function ckTodoHTML(){
  const B = BUDGET || {};
  const today = (CRED.days||[]).filter(d => d.date === ckAnchor())[0] || null;
  const rows = [];

  const inc = today ? ckRound(today.income) : 0;
  rows.push(inc > 0
    ? {lv:'ok', t:'今日收入已登记', d:'已入账 '+fmt0(inc)+' 积分，凭证与留痕见收入配置',
       a:'查看', go:'control', sub:'income'}
    : {lv:'warn', t:'今日收入待维护', d:'今日尚无已入账收入；有进账请补录并附凭证截图',
       a:'去录入', go:'control', sub:'income'});

  if (!B.configured){
    rows.push({lv:'gray', t:'积分未配置', d:'填初始积分与今日余额后才会产生预警',
      a:'去配置', go:'recon', sub:'daily'});
  } else if (B.level && B.level !== 'none'){
    rows.push({lv:B.level, t:(B.levelText||'余额告警'), d:'今日消耗 '+fmt0(B.todaySpent)
      + ' / 当前可用 '+fmt0(B.balance)+' = '+(B.spendRatioOver?'>100':fmt1(B.spendRatio))+'%',
      a:'查看', go:'recon', sub:'daily'});
  } else {
    rows.push({lv:'ok', t:'可用余额正常', d:'预警比 '+fmt1(B.spendRatio||0)+'%，低于黄色阈值 '
      + fmt0((B.thresholds||{}).warn||10)+'%', a:'查看', go:'recon', sub:'daily'});
  }

  const t1 = CRED.t1 || {};
  rows.push({lv: t1.status === 'done' ? 'ok' : 'warn',
    t: t1.status === 'done' ? 'T+1 对账已完成' : '日终对账待执行',
    d: t1.status === 'done' ? ('对账时刻 ' + (t1.runAt||'—')) : (t1.note || 'T 日账在次日 00:00 由定时任务对账'),
    a:'查看', go:'recon', sub:'daily'});

  const r = ckRiskStats();
  rows.push({lv: r.pending ? 'orange' : 'ok',
    t: r.pending ? ('风险审计 '+r.pending+' 项待处置') : '风险审计无待处置',
    d: '命中规则 '+r.rules+' 条 · 账面可回收 '+fmt0(r.waste)+' 积分',
    a: r.pending?'去处置':'查看', go:'control', sub:'recycle'});

  const covered = (CRED.days||[]).length;
  if (covered && covered < 7){
    rows.push({lv:'gray', t:'采集窗口仅覆盖 '+covered+' 天',
      d:'周期分析的合计只含这 '+covered+' 天，不做按天外推',
      a:'看台簿', go:'recon', sub:'book'});
  }

  return rows.map(x=>{
    const c = ckLevelColor(x.lv);
    return '<div class="todo" data-go="'+x.go+'" data-sub="'+x.sub+'" role="button" tabindex="0">'
      + '<i class="td-dot" style="background:'+c+';box-shadow:0 0 7px '+c+'66"></i>'
      + '<div class="td-b"><div class="td-t">'+esc(x.t)+'</div>'
      + '<div class="td-d" title="'+esc(x.d)+'">'+esc(x.d)+'</div></div>'
      + '<span class="td-a">'+x.a+' →</span></div>';
  }).join('');
}

/* ---------- 组装 ---------- */
function buildCockpit(){
  const T = OVERVIEW.totals || {};
  const agg = ckAgg(CK.period);

  let h = ''
    /* ① 今日动态 */
    + '<section class="card ck-card ck-today">'
    +   '<div class="card-hd"><div class="card-title">今日动态监测</div>'
    +   '<div class="card-sub">T 日 <b>'+esc(ckAnchor().slice(5) || '—')+'</b> · 日志实时汇总</div></div>'
    +   '<div class="ck-kpis">'+ckTilesHTML()+'</div>'
    +   '<div class="ck-chart" id="ck-today-chart"></div>'
    +   '<div class="ck-entries">'+ckEntriesHTML()+'</div>'
    + '</section>'
    /* ② 周期动态 */
    + '<section class="card ck-card ck-period">'
    +   '<div class="card-hd"><div class="card-title">周期动态监测</div>'
    +   '<div class="seg" id="ckSeg">'
    +     '<button data-p="month" class="'+(CK.period==='month'?'on':'')+'">月度</button>'
    +     '<button data-p="quarter" class="'+(CK.period==='quarter'?'on':'')+'">季度</button>'
    +     '<button data-p="year" class="'+(CK.period==='year'?'on':'')+'">年度</button>'
    +   '</div></div>'
    +   '<div class="ck-kpis" id="ckPeriodKpis">'+ckPeriodKpisHTML(agg)+'</div>'
    +   '<div class="ck-chart short" id="ck-period-chart"></div>'
    +   '<div class="ck-note" id="ckPeriodNote">'+ckPeriodNoteHTML(agg)+'</div>'
    + '</section>'
    /* ③ 对账审计分析 */
    + '<section class="card ck-card ck-audit">'
    +   '<div class="card-hd"><div class="card-title">对账审计分析</div>'
    +   '<div class="card-sub">账实相符度 × 风险处置进度</div></div>'
    +   '<div class="ck-pair">'+ckReconMini()+ckRiskMini()+'</div>'
    + '</section>'
    /* ④ 告警待办 */
    + '<section class="card ck-card ck-todo">'
    +   '<div class="card-hd"><div class="card-title">告警待办</div>'
    +   '<div class="card-sub">收入维护 · 余额告警 · 日终对账 · 风险审计</div></div>'
    +   '<div class="todo-list">'+ckTodoHTML()+'</div>'
    + '</section>';

  $('#v-cockpit').innerHTML = h;

  ckDrawToday();
  ckDrawPeriod(agg);
  bindCockpit();
}

function ckPeriodKpisHTML(a){
  const items = [
    {lb:'积分池总额', v:fmt0(a.pool), u:'积分', ft:'初始积分口径'},
    {lb:'期初结转', v:fmt0(a.carryIn), u:'积分', ft:'上一期结转到本期'},
    {lb:'消耗总额', v:fmt0(a.spend), u:'积分', ft:a.covered+' 天合计'},
    {lb:'收入总额', v:fmt0(a.income), u:'积分', ft:'仅已入账'},
    {lb:'可用积分', v:fmt0(a.close), u:'积分', ft:'期末日结余额'}
  ];
  return items.map(x=>'<div class="ck-kpi"><div class="lb">'+x.lb+'</div>'
    + '<div class="v">'+x.v+'<span class="u">'+x.u+'</span></div>'
    + '<div class="ft">'+x.ft+'</div></div>').join('');
}
function ckPeriodNoteHTML(a){
  const short = a.covered < a.window;
  return '<span>'+esc(a.range.label)+' · '+a.range.from.slice(5)+' → '+a.range.to.slice(5)
    + ' · 有账 '+a.covered+' 天</span>'
    + (short ? '<i class="ck-warn" title="采集窗口只有 '+a.window+' 天，本周期其余日期没有数据；不做按天外推">窗口覆盖不足</i>' : '');
}

function ckDrawToday(){
  const t = ckToday();
  if (!t.n){
    $('#ck-today-chart').innerHTML = '<div class="empty">今日尚无任务记录，无法绘制实时走向</div>';
    return;
  }
  const opt = {
    animationDuration:600,
    tooltip:Object.assign({trigger:'axis', axisPointer:{type:'line',
      lineStyle:{color:'var(--line3)', type:'dashed'}},
      formatter:ps=>{
        const i = ps[0].dataIndex;
        return '<b>'+t.labels[i]+'</b><br/>本小时 '+fmt1(t.inc[i])+' · 累计 '+fmt1(t.cum[i])+' 积分';
      }}, tooltipBase),
    grid:baseGrid({top:14, bottom:0, right:10, left:2}),
    xAxis:axisX({data:t.labels, boundaryGap:false,
      axisLabel:{color:TT.text3, fontSize:9.5, interval:Math.max(0, Math.ceil(t.labels.length/7)-1)}}),
    yAxis:axisY({axisLabel:{color:TT.text3, fontSize:9.5, formatter:v=>fmt(v)}, splitNumber:3}),
    series:[
      {name:'每小时', type:'bar', barWidth:'46%', data:t.inc,
       itemStyle:{color:varrgb('--pri',.22), borderRadius:[2,2,0,0]}, z:1},
      {name:'累计消耗', type:'line', smooth:.32, symbol:'circle', symbolSize:4,
       showSymbol:false, data:t.cum, z:3,
       lineStyle:{width:2, color:'var(--holo-cyan)', shadowColor:'var(--glow)', shadowBlur:10},
       itemStyle:{color:'var(--holo-cyan)', borderColor:'var(--panel3)', borderWidth:1.5},
       areaStyle:{color:{type:'linear', x:0,y:0,x2:0,y2:1, colorStops:[
         {offset:0, color:varrgb('--pri',.26)},{offset:1, color:varrgb('--pri',0)}]}}}
    ]
  };
  chart('ck-today-chart', opt);
}

function ckDrawPeriod(agg){
  const bk = ckBuckets(CK.period, agg.days);
  if (!bk.length){
    $('#ck-period-chart').innerHTML = '<div class="empty">本周期无数逐日账，无法绘制收支</div>';
    return;
  }
  const opt = {
    animationDuration:600,
    tooltip:Object.assign({trigger:'axis', axisPointer:{type:'shadow',
      shadowStyle:{color:varrgb('--pri',.06)}},
      formatter:ps=>{
        const i = ps[0].dataIndex;
        return '<b>'+bk[i].label+'</b><br/>收入 '+fmt1(bk[i].income)
          + ' · 消耗 '+fmt1(bk[i].spend)+'<br/>净额 '+fmt1(bk[i].income - bk[i].spend);
      }}, tooltipBase),
    legend:{data:['收入','消耗'], right:0, top:0, itemWidth:8, itemHeight:8, itemGap:12,
      textStyle:{color:TT.text3, fontSize:10}, icon:'roundRect'},
    grid:baseGrid({top:22, bottom:0, right:8, left:2}),
    xAxis:axisX({data:bk.map(b=>b.label), axisLabel:{color:TT.text3, fontSize:9.5}}),
    yAxis:axisY({axisLabel:{color:TT.text3, fontSize:9.5, formatter:v=>fmt(v)}, splitNumber:3}),
    series:[
      {name:'收入', type:'bar', barWidth:'38%', barGap:'12%', data:bk.map(b=>b.income),
       itemStyle:{color:'var(--ok-9)', borderRadius:[2,2,0,0]}},
      {name:'消耗', type:'bar', barWidth:'38%', data:bk.map(b=>b.spend),
       itemStyle:{color:'var(--info-9)', borderRadius:[2,2,0,0]}}
    ]
  };
  chart('ck-period-chart', opt);
}

function ckRenderPeriod(){
  const agg = ckAgg(CK.period);
  const k = document.getElementById('ckPeriodKpis');
  if (k) k.innerHTML = ckPeriodKpisHTML(agg);
  const n = document.getElementById('ckPeriodNote');
  if (n) n.innerHTML = ckPeriodNoteHTML(agg);
  ckDrawPeriod(agg);
}

function bindCockpit(){
  const v = $('#v-cockpit');
  if (!v.__bound){
    v.addEventListener('click', e=>{
      /* 三类不可见执行入口：先设好分类筛选再跳执行监测 */
      const en = e.target.closest('.ck-entry');
      if (en){
        MONF.cat = en.dataset.cat || 'all';
        go('control', 'exec');
        return;
      }
      const sg = e.target.closest('#ckSeg button');
      if (sg){
        CK.period = sg.dataset.p;
        $$('#ckSeg button').forEach(b=>b.classList.toggle('on', b===sg));
        ckRenderPeriod();
        return;
      }
    });
    bindGoLinks(v);
    v.__bound = true;
  }
}
