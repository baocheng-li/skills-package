/* ==================== 积分对账 ====================
   四个页签对应四件事：
     1) 收支流水   —— T 日的每一笔钱（收入 + 支出），每笔带 16 位交易流水号
     2) 支出明细   —— T 日按任务聚合的支出：任务名 / 任务ID / 交易流水号 / 积分 / 累计耗时
     3) 日终对账   —— T+1 逐日结转核对：日初 = 日收 + 上一日日结，日结 = 日初 − 日耗
     4) 异常处置   —— 原来并入台账的规则命中视图；v2.1 起删除回收，改为止损/归因/证据留存

   全局唯一 16 位流水号 = 8 位日期 + 6 位任务名小写 + 2 位序列号
   （由 hosts/commands/txn.py 在采集阶段生成并注册，报告里直接读，页内不再另算）
   ================================================== */
const LF = {
  tab:'flow',           // flow | expense | daily | book
  kw:'', from:'', to:'',
  type:'全部', model:'全部', project:'全部',
  sort:'credits', page:1, size:10,
  sel:'',               // 任务消耗明细里展开的任务 ID
  stepFilter:'all',     // all | model | tool | err
  stepPage:1,
  pageB:1,              // 对账台簿分页
  bookSt:'all'          // 台簿状态筛选：all | ok | bad | nobal
};

const LEDGER_TRIG_TAG = {'人工':'blue','定时':'amber','后台自动化':'purple'};

/* 积分保留两位（单笔精确到分），汇总用 fmt0 */
const c2 = n => (Math.round((n || 0) * 100) / 100).toFixed(2);
const r2 = n => Math.round((n || 0) * 100) / 100;

/* 页内改「初始积分」后按同一套规则重算日链（口径与 hosts/commands/recon_daily.py 一致）：
     日初 = 日收 + 上一日日结余额；日结 = 日初 − 日耗；偏差 = 账户余额 − 日结
   只重算链条，不改日收 / 日耗 / 账户余额这三个「事实」字段。 */
function credRebuild(){
  const C = CRED;
  if (!C || !(C.days || []).length) return;
  const tol = C.tolerance || 5;
  const tolAbs = C.toleranceAbs || 0.5;
  let prev = (C.initial === null || C.initial === undefined) ? 0 : +C.initial;
  C.days.forEach((d, i)=>{
    d.first = (i === 0);
    d.prevClose = r2(prev);
    d.open = r2((d.income || 0) + d.prevClose);
    d.close = r2(d.open - (d.spend || 0));
    if (d.account === null || d.account === undefined){
      d.diff = null; d.diffPct = null; d.ok = null;
    } else {
      d.diff = r2(d.account - d.close);
      d.diffPct = d.close ? r2(d.diff / d.close * 100) : null;
      d.ok = Math.abs(d.diff) <= Math.max(tolAbs, Math.abs(d.open) * tol / 100);
    }
    if (!d.reconDone) d.status = 'pending';
    else if (d.account === null || d.account === undefined) d.status = 'nobal';
    else d.status = d.ok ? 'matched' : 'diff';
    d.statusText = (CRED_STATUS[d.status]||CRED_STATUS.nobal).text;
    const bad = d.status === 'diff';
    d.carry = (d.account === null || d.ok || !d.reconDone) ? d.close : d.account;
    d.adjust = r2(d.carry - d.close);
    d.provisional = (d.status === 'pending');
    d.hint = bad
      ? ('账面按' + (d.first ? ('初始积分 ' + c2(C.initial)) : '上一日日结余额')
         + '推算为 ' + c2(d.close) + '，实际余额 ' + c2(d.account)
         + '，相差 ' + (d.diff > 0 ? '+' : '') + c2(d.diff) + '。已按账实为准调账结转。')
      : (d.status === 'pending' ? 'T+1 未到，先按账面值结转，届时以实际余额核对。'
        : (d.status === 'nobal' ? '缺可比对的账户余额：未提供积分流水，也未在这一天填过今日余额。'
                                : '账户余额与日结余额一致（容差内）。'));
    prev = d.carry;
  });
  /* 汇总跟着刷新 */
  const T = C.totals = C.totals || {};
  let matched = 0, diff = 0, pending = 0, nobal = 0, dc = 0;
  C.days.forEach(d=>{
    if (d.status==='matched') matched++;
    else if (d.status==='diff'){ diff++; dc += Math.abs(d.diff||0); }
    else if (d.status==='pending') pending++;
    else nobal++;
  });
  Object.assign(T, {days:C.days.length, matched:matched, diff:diff, pending:pending,
                    nobal:nobal, diffCredits:r2(dc)});
}

/* 秒 → 1m32s / 2h05m，表格里比裸秒数好读 */
function lgDur(s){
  s = Math.max(0, Math.round(s || 0));
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60), h = Math.floor(m / 60);
  if (h < 1) return m + 'm' + (s % 60 ? String(s % 60).padStart(2,'0') + 's' : '');
  return h + 'h' + String(m % 60).padStart(2,'0') + 'm';
}

const CRED_STATUS = {
  matched:{cls:'green', text:'账实一致'},
  diff:   {cls:'red',   text:'存在偏差'},
  pending:{cls:'amber', text:'待 T+1 对账'},
  nobal:  {cls:'gray',  text:'无实际余额'}
};

/* ---------------- 骨架 ----------------
   二级页面共用一套筛选栏与容器：
     flow    今日收支流水
     expense 任务消耗明细（行下钻）
     daily   T+1 日终对账
     book    对账台簿（周期图表 + 台账）
   页面内不再画页签——层级交给侧栏二级菜单，页内只留筛选与内容。 */
function buildRecon(sub){
  LF.tab = (sub === 'expense' || sub === 'daily' || sub === 'book') ? sub : 'flow';
  LF.page = 1;
  const wrap = $('#v-recon');
  wrap.innerHTML = '<div id="lgFilter"></div><div id="ledgerBody"></div>';

  if (!wrap.__bound){
    wrap.__bound = true;
    wrap.addEventListener('click', e=>{
      /* 台簿分页用 data-pgb，避免与流水/明细的 data-page 互相误触 */
      const bp = e.target.closest('[data-pgb]');
      if (bp){ LF.pageB = Math.max(1, +bp.dataset.pgb); renderBook(); return; }
      const sp = e.target.closest('[data-spg]');
      if (sp){ LF.stepPage = Math.max(1, +sp.dataset.spg); renderLedgerExpense(); return; }
      const sf = e.target.closest('[data-sf]');
      if (sf){ LF.stepFilter = sf.dataset.sf; LF.stepPage = 1; renderLedgerExpense(); return; }
      if (e.target.id === 'lgCloseSteps'){ LF.sel = ''; renderLedgerExpense(); return; }
      const bk = e.target.closest('[data-bst]');
      if (bk){ LF.bookSt = bk.dataset.bst; LF.pageB = 1; renderBook(); return; }
      const dt = e.target.closest('[data-detail]');
      if (dt){
        LF.sel = (LF.sel === dt.dataset.detail) ? '' : dt.dataset.detail;
        LF.stepPage = 1; LF.stepFilter = 'all';
        renderLedgerExpense(); return;
      }
      const pg = e.target.closest('[data-page]');
      if (pg){
        LF.page = Math.max(1, +pg.dataset.page);
        if (LF.tab === 'expense') renderLedgerExpense(); else renderLedgerFlow();
        return;
      }
      if (e.target.id==='lgReset'){
        Object.assign(LF, {kw:'',from:'',to:'',type:'全部',model:'全部',project:'全部',
                           page:1, stepPage:1});
        ledgerRenderAll(); return;
      }
      if (e.target.id==='lgSaveBal'){ saveLedgerBalance(); return; }
      if (e.target.id==='lgRunRecon'){ runReconNow(); return; }
      if (e.target.id==='lgExportFlow'){ exportFlow(); return; }
      if (e.target.id==='lgExportBook'){ exportBook(); return; }
      if (e.target.id==='lgExportDaily'){ exportDaily(); return; }
      if (e.target.id==='lgExportSteps'){ exportSteps(); return; }
      const tr = e.target.closest('tr[data-id]');
      if (tr && tr.dataset.id){
        /* 明细页点行 = 就地展开执行明细；其余页点行 = 打开抽屉 */
        if (LF.tab === 'expense'){
          LF.sel = (LF.sel === tr.dataset.id) ? '' : tr.dataset.id;
          LF.stepPage = 1;
          renderLedgerExpense();
        } else {
          openDrawer(tr.dataset.id);
        }
      }
    });
  }

  ledgerRenderAll();
}

function ledgerRenderAll(){
  const f = document.getElementById('lgFilter');
  if (f){ f.innerHTML = ledgerFilterBar(); bindLedgerFilters(); }
  ledgerRenderTab();
}

function ledgerRenderTab(){
  if (LF.tab==='flow') renderLedgerFlow();
  else if (LF.tab==='expense') renderLedgerExpense();
  else if (LF.tab==='daily') renderLedgerDaily();
  else renderBook();
  applyTableFit(document.getElementById('ledgerBody'));
}

/* 表格按页大小补空行：数据只有三五条时，卡片下半截不会留一片空，
   也顺带把「这一页就这么多」说清楚，而不是让人以为还有内容没加载 */
function padRows(have, cols, size, note){
  const n = Math.max(0, (size || LF.size || 10) - have);
  if (!n) return '';
  /* 第一行写一句「到此为止」，把空白变成收尾说明；其余行只留极淡分隔线延续表格节奏 */
  let out = '<tr class="blank"><td colspan="' + cols + '" class="tbl-end">'
    + (note || ('本页 '+have+' 行 · 已全部列出')) + '</td></tr>';
  for (let i = 1; i < n; i++) out += '<tr class="blank"><td colspan="' + cols + '"></td></tr>';
  return out;
}

/* ---------------- 筛选栏 ---------------- */
function ledgerFilterBar(){
  const types = [...new Set(TASKS.map(t=>t.type))].sort();
  const models = MODELS.map(m=>m.id);
  const projects = [...new Set(TASKS.map(t=>t.project||'未绑定项目'))].sort();
  return '<div class="filterbar">'
    + '<input class="sel" id="lgKw" placeholder="搜索任务名 / 任务ID / 流水号" value="'+esc(LF.kw)+'" style="flex:0 0 186px;width:186px">'
    + '<span class="fsep">区间</span>'
    + '<input class="sel" id="lgFrom" type="date" value="'+esc(LF.from)+'" style="flex:0 0 118px;width:118px">'
    + '<span style="color:var(--text3);font-size:11px">→</span>'
    + '<input class="sel" id="lgTo" type="date" value="'+esc(LF.to)+'" style="flex:0 0 118px;width:118px">'
    + '<div class="seg" id="lgQuick">'
      + [['全部',0],['近 7 日',7],['近 14 日',14],['近 30 日',30]].map(([n,v])=>
        '<button data-d="'+v+'">'+n+'</button>').join('')
    + '</div>'
    + '<select class="sel" id="lgType"><option value="全部">全部类型</option>'
      + types.map(t=>'<option '+(LF.type===t?'selected':'')+'>'+esc(t)+'</option>').join('')+'</select>'
    + '<select class="sel" id="lgModel"><option value="全部">全部模型</option>'
      + models.map(m=>'<option value="'+esc(m)+'" '+(LF.model===m?'selected':'')+'>'
          +esc(M[m]?M[m].name:m)+'</option>').join('')+'</select>'
    + '<select class="sel" id="lgProject"><option value="全部">全部项目</option>'
      + projects.map(p=>'<option '+(LF.project===p?'selected':'')+'>'+esc(p)+'</option>').join('')+'</select>'
    + '<select class="sel" id="lgSort">'
      + [['credits','按积分降序'],['retry','按重试次数'],['duration','按时长'],['tokens','按 Token'],['ts','按时间倒序']]
        .map(([v,n])=>'<option value="'+v+'" '+(LF.sort===v?'selected':'')+'>'+n+'</option>').join('')
    + '</select>'
    + '<button class="btn" id="lgReset">清除筛选</button>'
    + '<button class="btn pri sp" id="lgExport">导出当前筛选</button>'
    + '</div>';
}

function ledgerRows(){
  let list = TASKS.slice();
  const kw = (LF.kw||'').trim().toLowerCase();
  if (kw){
    list = list.filter(t=>(t.name||'').toLowerCase().indexOf(kw)>=0
      || (t.id||'').toLowerCase().indexOf(kw)>=0
      || (t.txn||'').toLowerCase().indexOf(kw)>=0
      || (t.project||'').toLowerCase().indexOf(kw)>=0);
  }
  if (LF.from) list = list.filter(t=>(t.date||'') >= LF.from);
  if (LF.to) list = list.filter(t=>(t.date||'') <= LF.to);
  if (LF.type!=='全部') list = list.filter(t=>t.type===LF.type);
  if (LF.model!=='全部') list = list.filter(t=>(t.models||[]).indexOf(LF.model)>=0
    || t.mainChannel===LF.model);
  if (LF.project!=='全部') list = list.filter(t=>(t.project||'未绑定项目')===LF.project);

  const key = LF.sort==='credits'?'credits':LF.sort==='retry'?'retry'
    :LF.sort==='duration'?'duration':LF.sort==='tokens'?'inTok':null;
  if (key) list.sort((a,b)=>key==='inTok'
      ? ((b.inTok+b.outTok)-(a.inTok+a.outTok))
      : (b[key]||0)-(a[key]||0));
  else list.sort((a,b)=>(b.ts||'').localeCompare(a.ts||''));
  return list;
}

function ledgerFiltering(){
  return !!(LF.kw || LF.from || LF.to || LF.type!=='全部'
            || LF.model!=='全部' || LF.project!=='全部');
}

/* ---------------- 区间口径 ----------------
   以前这里是一排「T 日 / 10-29 / T / 10-28 / T-1 / 10-27 / T-2」的胶囊选择条。
   删掉它的原因不是嫌长，而是它和筛选条里的「区间」重复表达同一件事：
   两套入口控制同一个字段，用户点了胶囊、筛选条不动，两边显示的状态就对不上。

   现在只有一条口径：看哪一段，由筛选条的 from / to 决定；两个都没填就落到
   数据锚点日（也就是「今天」）。这也让「今日收支流水」这个页名名副其实——
   不填区间时它就是今天那一段。 */
function credRange(){
  const a = CRED.anchor || '';
  const from = LF.from || a;
  const to = LF.to || a;
  const single = (from === to);
  return {from, to, single, label: single ? from : (from + ' → ' + to)};
}

/* 当前区间内的流水行（收入 + 支出） */
function credFlowOf(){
  const ids = {}; ledgerRows().forEach(t=>{ ids[t.id]=1; });
  const kw = (LF.kw||'').trim().toLowerCase();
  const R = credRange();
  return (CRED.flow||[]).filter(r=>r.date >= R.from && r.date <= R.to).filter(r=>{
    if (r.dir==='out') return !!ids[r.taskId];
    return !kw || (r.name||'').toLowerCase().indexOf(kw)>=0;
  }).sort((a,b)=>(b.ts||'').localeCompare(a.ts||''));
}

/* ---------------- T 日按小时分布 ---------------- */
/* 表格行数少时（例如某天只有 3 个任务）用来把卡片下半部填满，
   同时给出「这一天是几点在烧积分」这一眼看不出来的信息。 */
function credHoursHTML(rows, label){
  const cr = new Array(24).fill(0), cnt = new Array(24).fill(0);
  (rows || []).forEach(r=>{
    if (r.dir !== 'out') return;
    const h = parseInt(String(r.ts||'').slice(11,13), 10);
    if (!isNaN(h) && h >= 0 && h < 24){ cr[h] += (r.credits||0); cnt[h]++; }
  });
  const max = Math.max.apply(null, cr.concat([0.0001]));
  const totalN = cnt.reduce((a,b)=>a+b, 0);
  const peak = cr.indexOf(max);
  const cells = cr.map((v,h)=>{
    const hh = String(h).padStart(2,'0');
    const tall = v > 0 ? Math.max(3, v/max*100) : 0;
    return '<div class="ch'+(v>0?' on':'')+'" title="'+hh+':00 · '+cnt[h]+' 笔 · '+c2(v)+' 积分">'
      /* 峰值柱标注数值，避免「只有两三根柱子」时看不出量级 */
      + '<i'+((v>0 && h===peak) ? ' class="pk" data-v="'+c2(v)+'"' : '')
        + ' style="height:'+tall.toFixed(1)+'%"></i>'
      + '<span>'+(h%3===0?hh:'')+'</span></div>';
  }).join('');
  return '<div class="cr-hours">'
    + '<div class="ch-hd">'+label
      + '<span>'+(totalN
          ? ('峰值 '+String(peak).padStart(2,'0')+':00 · '+c2(max)+' 积分 · 共 '+totalN+' 笔')
          : '这一天没有支出，柱形为空')+'</span></div>'
    + '<div class="ch-bars">'+cells+'</div>'
    + '</div>';
}

/* T 日支出 Top 5：右列固定高度的一块，回答「这一天的积分主要花在哪几个任务上」 */
function credTopHTML(rows, label){
  const outs = (rows||[]).filter(r=>r.dir!=='in');
  const list = outs.slice().sort((a,b)=>b.credits-a.credits).slice(0,5);
  if (!list.length) return '<div class="cr-top"><div class="ct-empty">T 日没有任务支出</div></div>';
  const max = list[0].credits || 1;
  const total = outs.reduce((s,r)=>s+r.credits, 0);
  const top = list.reduce((s,r)=>s+r.credits, 0);
  let h = '<div class="cr-top"><div class="ct-hd">'+label
    + '<span>合计占 T 日支出 '+pct1(total?top/total:0)+'</span></div>';
  list.forEach(r=>{
    h += '<div class="ct-r" title="'+esc(r.name)+' · '+c2(r.credits)+' 积分 · '+lgDur(r.duration)+'">'
      + '<div class="ct-1"><span class="n">'+esc(r.name||'—')+'</span><b>'+c2(r.credits)+'</b></div>'
      + '<i><b style="width:'+(r.credits/max*100).toFixed(1)+'%"></b></i>'
      + '<div class="ct-2"><span>'+esc(r.trigger||'—')+' · '+(r.steps||'—')+' 步</span>'
        + '<span>'+lgDur(r.duration)+'</span></div>'
      + '</div>';
  });
  return h + '</div>';
}

/* 近 7 日收支概览：T 日流水很短时把版面补满，也顺带给出「这几天收支持平吗」 */
function credWeekHTML(curDay){
  const days = (CRED.days || []).slice(-7).reverse();
  if (!days.length) return '<div class="cr-week"><div class="cw-empty">暂无可比对的日期</div></div>';
  const maxSpend = Math.max.apply(null, days.map(d=>d.spend||0).concat([0.0001]));
  let h = '<div class="cr-week"><div class="cw-hd">日期 · 支出条 · 净额 · 判定'
    + '<span>'+days.length+' 天</span></div><div class="cw-rows">';
  days.forEach(d=>{
    const s = CRED_STATUS[d.status] || CRED_STATUS.nobal;
    const net = (d.income||0) - (d.spend||0);
    const w = Math.max(2, (d.spend||0)/maxSpend*100);
    h += '<div class="cwk'+(d.date===curDay?' on':'')+'">'
      + '<span class="d">'+esc(d.date.slice(5))+'</span>'
      + '<i><b style="width:'+w.toFixed(1)+'%"></b></i>'
      + '<span class="n" style="color:'+(net<0?'var(--warn-soft)':'var(--ok-11)')+'">'
        + (net>0?'+':'')+c2(net)+'</span>'
      + '<span class="tag '+s.cls+' sm">'+esc(s.text.replace('账实',''))+'</span>'
      + '</div>';
  });
  return h + '</div></div>';
}

/* ---------------- 1) 积分收支流水（按区间口径，不再绑「T 日」） ---------------- */
function renderLedgerFlow(){
  const R = credRange();
  const rows = credFlowOf();
  /* 每页固定 10 行：审计要的是「这一页能对着念完」，不是一口气刷到底 */
  const pg = pgSlice(rows, LF.page, LF.size);
  const inc = rows.filter(r=>r.dir==='in');
  const exp = rows.filter(r=>r.dir==='out');
  const incSum = inc.reduce((s,r)=>s+r.credits,0);
  const expSum = exp.reduce((s,r)=>s+r.credits,0);
  const net = incSum - expSum;

  const dayRec = R.single ? (CRED.days||[]).find(d=>d.date===R.from) : null;

  /* 收入侧可信度（v3）：不看「谁复核了」，看凭证能不能复算、留痕全不全。
     有风险项时把告警顶到最前——卡片副标题是 nowrap + 省略号，放后面会被截掉。 */
  const IQ = CRED.incomeQuality || {};
  const iqN = IQ.n || 0;
  const risk = [];
  if (IQ.noSnapConfirmed) risk.push('无凭证入账 '+IQ.noSnapConfirmed);
  if (IQ.tampered) risk.push('哈希不符 '+IQ.tampered);
  if (IQ.missing) risk.push('凭证缺失 '+IQ.missing);
  const iqTxt = iqN
    ? (risk.length ? ('⚠ '+risk.join(' · ')+' · ') : '')
      + '快照覆盖 '+pct1((IQ.snapshotRate||0)/100)
      + ' · 可复算 '+pct1((IQ.evidenceRate||0)/100)
      + ' · 留痕覆盖 '+pct1((IQ.trailRate||0)/100)
    : '收入台账为空';

  /* 对账状态只在「单日」时有意义：多日区间混着不同状态，给一个标签反而误导 */
  const st = (R.single && dayRec) ? (CRED_STATUS[dayRec.status] || CRED_STATUS.nobal) : null;

  let h = '';

  /* 四张指标：收入 / 支出 / 净额 / 笔数 */
  h += '<div class="srow"><div class="srow-lb">'
    + esc(R.single ? ('T 日 · ' + R.label) : ('区间 · ' + R.label))
    + (st && st.text ? '<span class="tag '+(st.cls==='gray'?'gray':st.cls)+' sm">'+esc(st.text)+'</span>' : '')
    + '</div><div class="srow-grid g4">'
    + kpiMini('区间收入', c2(incSum), '积分', 'var(--ok-10)',
        inc.length
          ? (inc.length+' 笔 · '+(iqN ? ('快照 '+pct1((IQ.snapshotRate||0)/100))
                                      : '来自积分流水，无需凭证'))
          : (iqN ? '区间内无收入 · 台账 '+iqN+' 笔' : '区间内无积分收入记录'))
    + kpiMini('区间支出', c2(expSum), '积分', 'var(--warn-9)',
        exp.length ? (exp.length+' 个任务 · 均 '+c2(expSum/Math.max(1,exp.length))+' 积分') : '区间内无消耗')
    + kpiMini('区间净额', (net>0?'+':'')+c2(net), '积分', net>=0?'var(--ok-10)':'var(--warn-soft)',
        net>=0 ? '收入覆盖支出' : '区间净消耗')
    + kpiMini('流水笔数', fmt0(rows.length), '笔', 'var(--info-9)',
        '收入 '+inc.length+' / 支出 '+exp.length+' · 每笔一个 16 位流水号')
    + '</div></div>';

  /* 流水表 */
  let tbl = '<div class="tbl-wrap"><table class="tbl" style="table-layout:fixed">'
    + '<thead><tr>'
    + '<th>交易流水号</th><th>时间</th><th>收/支</th><th style="width:34%">摘要</th>'
    + '<th title="支出显示任务ID；收入显示凭证 SHA256 前 8 位 + 留痕条数">任务ID / 凭证</th>'
    + '<th style="text-align:right">积分</th>'
    + '<th style="text-align:right">累计耗时</th><th>状态</th>'
    + '</tr></thead><tbody>';

  if (!rows.length){
    tbl += '<tr><td colspan="8" style="text-align:center;color:var(--text3);padding:30px">'
      + '这段区间内没有积分收支流水，换个日期范围试试'
      + '</td></tr>';
  }
  for (const r of pg.rows){
    const isIn = r.dir==='in';
    /* 收入行的状态 = 台账确认状态（单人提交即入账，没有「等第二个人点头」这一档）；
       支出行的状态 = 任务执行结果。两者语义不同，不能共用一个绿标签。 */
    const IN_ST = {confirmed:['已入账','green'], pending:['草稿','amber'],
                   void:['已退回','gray']}[r.entryStatus||'confirmed'] || ['已入账','green'];
    const statusTag = isIn
      ? '<span class="tag '+IN_ST[1]+' sm">'+IN_ST[0]+'</span>'
      : '<span class="tag '+(r.status==='成功'?'green':r.status==='失败'?'red':'amber')+' sm">'
        + esc(r.status||'—')+'</span>';
    /* 收入行没有 task_id，这一格正好用来放「凭证指纹」：
       前 8 位 SHA256 + 留痕条数。支出行仍显示任务ID，列宽语义各归各的。 */
    const midCell = isIn
      ? (r.snap==='ok'
          ? '<span class="mono" title="凭证 SHA256 '+(r.snapSha||'')+'">SNP '
            + esc((r.snapSha||'').slice(0,8))+'</span>'
            + (r.trailN ? '<span class="tag gray sm" style="margin-left:4px" title="'
                + r.trailN+' 条录入留痕：CREATE/EDIT/SUBMIT/REOPEN/VOID">'
                + r.trailN+'</span>' : '')
          : r.snap==='tampered'
            ? '<span class="tag red sm" title="凭证哈希与台账不符（EVIDENCE_TAMPERED）">哈希不符</span>'
            : r.snap==='missing'
              ? '<span class="tag red sm" title="凭证文件不在，无法复算">凭证缺失</span>'
              : '<span class="tag amber sm" title="这笔收入没有凭证快照，按规则不得入账">无凭证</span>')
      : (r.taskId ? esc(r.taskId) : '<span style="color:var(--text3)">—</span>');
    tbl += '<tr'+(r.taskId?(' data-id="'+esc(r.taskId)+'"'):'')+' class="cr-row'+(isIn?' in':'')+'">'
      + '<td class="mono">'+(r.txn
          ? '<span class="txn" title="8 位日期 + 6 位任务名 + 2 位序列号">'+esc(r.txn)+'</span>'
          : '<span style="color:var(--text3)">未登记</span>')+'</td>'
      + '<td class="mono" style="color:var(--text3);font-size:10.5px">'
        + esc(isIn ? r.date.slice(5) : (r.ts||'').slice(11,19) || '—')+'</td>'
      + '<td>'+(isIn?'<span class="tag green sm">收入</span>'
                     :'<span class="tag amber sm">支出</span>')+'</td>'
      + '<td class="name" title="'+esc(r.name)+'">'+esc(r.name||'—')+'</td>'
      + '<td class="mono lnk" style="color:var(--text3);font-size:10.5px">'+midCell+'</td>'
      + '<td class="num" style="color:'+(isIn?'var(--ok-11)':'var(--text)')+'">'
        + (isIn?'+':'−')+c2(r.credits)+'</td>'
      + '<td class="num">'+(r.duration?lgDur(r.duration):'—')+'</td>'
      + '<td>'+statusTag+'</td>'
      + '</tr>';
  }
  if (rows.length) tbl += padRows(pg.rows.length, 8, PG_SIZE,
    '第 '+pg.page+' 页共 '+pg.rows.length+' 笔 · 每笔对应一个 16 位流水号');
  tbl += '</tbody></table></div>' + pagerHTML(pg, 'page');
  const bar = '<div class="cr-bar">'
    + '<span class="cr-bar-t">'+esc(R.single?'T 日':'区间')+' <b>'+esc(R.label||'—')+'</b> · 全量 '+rows.length+' 笔</span>'
    + '<button class="btn xs" id="lgRunRecon">执行对账</button>'
    + '<button class="btn xs" id="lgExportFlow">导出流水</button>'
    + '</div>';

  /* 左：表格（内容高度，上限 56%）+ 下方 24 小时分布填满；右：近 7 日 + 区间 Top 5 */
  h += '<div class="cr-flow">'
    + cardHTML('积分收支流水',
        esc(R.label||'—')+' · 收入 '+inc.length+' 笔 / 支出 '+exp.length+' 笔 · '+iqTxt
          + ' · 点行可下钻（支出看任务ID，收入看凭证指纹）',
        bar + tbl + credHoursHTML(rows, '支出时间分布（按小时归集，悬停看笔数与积分）'),
        'cr-split')
    + '<div class="cr-right2">'
      + cardHTML('近 7 日收支', '净额 = 日收 − 日耗',
          credWeekHTML(R.single ? R.from : ''), 'cr-side')
      + cardHTML('区间支出 Top 5', '按积分排序，占比条看集中度',
          credTopHTML(rows, '任务'), 'cr-top')
    + '</div>'
    + '</div>';

  $('#ledgerBody').innerHTML = h;
}

/* ---------------- 2) 任务消耗明细 ---------------- */
/* 表头口径（让读者能自己验证，而不是信一个数）：
   * 客户端只上报「步级总耗时」，输入/输出没有分开计时——所以「用时」给该步总耗时，
     token 另列展示，不编一个「输入用时 / 输出用时」出来；
   * 「命中规则」按步归因：出错步 → FAILED_BURN，重试 ≥2 次 → RETRY_STORM，
     其余留空（规则本身是任务级的，这里只标能落到步上的部分）；
   * 「可回收积分」是账面口径——已消耗不可追回，这一列只为归因与补偿申请留位置。 */
const STEP_RULE = s => s.err ? 'FAILED_BURN' : ((s.retry || 0) >= 2 ? 'RETRY_STORM' : '');
const STEP_WASTE = s => (s.err || (s.retry || 0) >= 2) ? (s.credits || 0) : 0;
const TRIG_TEXT = {'人工':'人机交互', '定时':'定时任务', '后台自动化':'后台自动化'};
/* 展示用的触发方式标签色（名字带 LBL 以免与 p03 里按原始值建的 TRIG_TAG 冲突） */
const TRIG_TAG_LBL = {'人机交互':'blue', '定时任务':'amber', '后台自动化':'purple'};

function taskOfId(id){ return TASKS.filter(t=>t.id===id)[0] || null; }

function stepListOf(t){
  const f = LF.stepFilter || 'all';
  let list = (t.steps || []).slice();
  if (f === 'model') list = list.filter(s=>s.kind !== 'tool');
  else if (f === 'tool') list = list.filter(s=>s.kind === 'tool');
  else if (f === 'err') list = list.filter(s=>s.err || (s.retry||0) >= 2);
  return list;
}

function renderLedgerExpense(){
  const R = credRange();
  const all = credFlowOf().filter(r=>r.dir==='out').sort((a,b)=>b.credits-a.credits);
  const sum = all.reduce((s,r)=>s+r.credits,0);
  const dur = all.reduce((s,r)=>s+r.duration,0);
  const slow = all.length ? all.reduce((a,b)=> b.duration>a.duration ? b : a) : null;
  const page = pgSlice(all, LF.page, LF.size);

  let h = '';
  h += '<div class="srow"><div class="srow-lb">任务消耗汇总'
    + '<span class="tag gray sm">每页 '+PG_SIZE+' 行</span></div>'
    + '<div class="srow-grid g4">'
    + kpiMini('区间支出合计', c2(sum), '积分', 'var(--warn-9)',
        all.length+' 个任务 · 占期初 '+(function(){
          const from = R.single ? R.from : R.from;
          const d = (CRED.days||[]).find(x=>x.date===from);
          return d && d.open ? pct1(sum/d.open) : '—';
        })())
    + kpiMini('任务数', fmt0(all.length), '个', 'var(--info-9)',
        '均 '+c2(all.length ? sum/all.length : 0)+' 积分 / 任务')
    + kpiMini('累计耗时', lgDur(dur), '', 'var(--ok-11)', '端到端时长之和')
    + kpiMini('最耗时任务', slow ? lgDur(slow.duration) : '—', '', 'var(--iris-11)',
        slow ? esc(String(slow.name||'').slice(0,18)) : '—')
    + '</div></div>';

  let tbl = '<div class="tbl-wrap"><table class="tbl" style="table-layout:fixed">'
    + '<thead><tr>'
    + '<th>任务类型</th><th style="width:21%">任务名称</th><th>任务ID</th>'
    + '<th>交易流水号</th><th>触发方式</th><th>模型 / 模式</th>'
    + '<th style="text-align:right">步长</th><th style="text-align:right">消耗积分</th>'
    + '<th style="text-align:right">累计耗时</th><th style="text-align:center">详情</th>'
    + '</tr></thead><tbody>';

  if (!all.length){
    tbl += '<tr><td colspan="10" style="text-align:center;color:var(--text3);padding:30px">'
      + '这一天没有任务积分支出</td></tr>';
  }
  page.rows.forEach(r=>{
    const t = taskOfId(r.taskId) || {};
    const trig = TRIG_TEXT[r.trigger] || r.trigger || '—';
    const on = LF.sel === r.taskId;
    tbl += '<tr data-id="'+esc(r.taskId)+'"'+(on?' class="sel"':'')+'>'
      + '<td><span class="tag gray sm">'+esc(t.type || r.type || '—')+'</span></td>'
      + '<td class="name" title="'+esc(r.name)+'">'+esc(r.name||'—')+'</td>'
      + '<td class="mono lnk">'+esc(r.taskId||'—')+'</td>'
      + '<td class="mono">'+(r.txn ? '<span class="txn">'+esc(r.txn)+'</span>'
          : '<span style="color:var(--text3)">未登记</span>')+'</td>'
      + '<td><span class="tag '+(TRIG_TAG_LBL[trig]||'gray')+' sm">'+esc(trig)+'</span></td>'
      + '<td><span class="mono">'+esc(r.model||'—')+'</span>'
        + '<span class="tag '+(t.level==='L4'?'red':t.level==='L3'?'amber':'gray')+' sm" '
        + 'style="margin-left:4px">'+esc(t.level||'—')+'</span></td>'
      + '<td class="num">'+(r.steps || ((t.nModelSteps||0)+(t.nTools||0)) || '—')+'</td>'
      + '<td class="num" style="font-weight:650">'+c2(r.credits)+'</td>'
      + '<td class="num">'+lgDur(r.duration)+'</td>'
      + '<td style="text-align:center">'
        + '<button class="btn xs" data-detail="'+esc(r.taskId)+'">'
        + (on ? '收起' : '查看')+'</button></td>'
      + '</tr>';
  });
  if (all.length) tbl += padRows(page.rows.length, 10, PG_SIZE,
    '第 '+page.page+' 页共 '+page.rows.length+' 条 · 点「查看」展开该任务的执行明细');
  tbl += '</tbody></table></div>';

  /* 执行明细：点行就地展开，不跳抽屉——审计时要能「上下一屏对着看」 */
  const detail = LF.sel ? stepsPanelHTML(LF.sel) : '';

  h += cardHTML('任务消耗明细',
      '点任务行展开执行明细 · 阶段 / 步骤 / 模型工具 / 用时 / 积分 / 命中规则 / 可回收',
      tbl + pagerHTML(page, 'page'), 'cr-flow-card')
    + detail;

  $('#ledgerBody').innerHTML = h;
}

function stepsPanelHTML(taskId){
  const t = taskOfId(taskId);
  if (!t) return '';
  const all = (t.steps || []);
  const steps = stepListOf(t);
  const page = pgSlice(steps, LF.stepPage, LF.size);
  const total = t.credits || 1;
  const waste = steps.reduce((s,x)=>s+STEP_WASTE(x), 0);
  /* 逐步积分是客户端按步上报的可选字段。实测 35 个任务里 26 个有逐步积分，
     另外 9 个只在任务级给一个总数——这时占比必须显示「—」，
     否则每步都算成 0%，读者会误读成「这些步骤不耗积分」。 */
  const stepSum = all.reduce((s,x)=>s+(x.credits||0), 0);
  const noStepCredits = !stepSum && (t.credits||0) > 0;
  const stepGap = (t.credits||0) - stepSum;
  const stepNote = noStepCredits
    ? '<div class="hint">本任务的积分只在任务级上报（'+c2(t.credits)+' 积分），'
      + '客户端未提供逐步分摊：下表「积分 / 占比」按缺失展示，'
      + '不代表这些步骤不耗积分。</div>'
    : (stepGap > 0.01
      ? '<div class="hint">逐步积分合计 '+c2(stepSum)+'，比任务级 '+c2(t.credits)
        + ' 少 '+c2(stepGap)+'：差额未落到具体步骤，占比按实际值展示，不做补平。</div>'
      : '');

  const f = LF.stepFilter || 'all';
  const segs = [['all','全部',steps.length],['model','模型步',(t.steps||[]).filter(s=>s.kind!=='tool').length],
                ['tool','工具步',(t.steps||[]).filter(s=>s.kind==='tool').length],
                ['err','异常步',(t.steps||[]).filter(s=>s.err||(s.retry||0)>=2).length]];

  let tbl = '<div class="tbl-wrap"><table class="tbl" style="table-layout:fixed">'
    + '<thead><tr>'
    + '<th>阶段</th><th style="text-align:right">步骤</th><th style="width:15%">模型 / 工具</th>'
    + '<th>技能</th><th style="text-align:right">用时</th><th style="text-align:right">输入→输出</th>'
    + '<th style="text-align:right">积分</th><th style="text-align:right">占比</th>'
    + '<th>命中规则</th><th>状态</th><th style="text-align:right">可回收</th>'
    + '<th style="width:20%">描述</th>'
    + '</tr></thead><tbody>';

  if (!steps.length){
    tbl += '<tr><td colspan="12" style="text-align:center;color:var(--text3);padding:26px">'
      + (f === 'all' ? '该任务没有可还原的步骤记录' : '当前筛选下没有步骤')+'</td></tr>';
  }
  page.rows.forEach(s=>{
    const rule = STEP_RULE(s);
    const desc = s.kind === 'tool'
      ? ('调用工具 ' + (s.tool || '—') + ' · ' + (s.phase || '工具调用'))
      : ((s.model ? (modelName(s.model) + ' · ') : '') + (s.phase || '推理') + '阶段'
         + (s.est ? ' · 积分为折算值' : ''));
    tbl += '<tr'+(s.err ? ' class="err"' : '')+'>'
      + '<td><span class="tag' + (s.kind==='tool'?' gray':' blue') + ' sm">'
        + esc(s.phase || (s.kind==='tool'?'工具调用':'推理'))+'</span></td>'
      + '<td class="num" style="color:var(--text3)">#'+(s.i!=null?s.i:'—')+'</td>'
      + '<td class="mono" title="'+esc(s.model||s.tool||'')+'">'
        + esc(s.kind==='tool' ? (s.tool||'—') : (s.model||'—'))+'</td>'
      + '<td style="color:var(--text3)">'+esc(s.kind==='tool' ? (s.tool||'—') : '—')+'</td>'
      + '<td class="num">'+lgDur(s.ms||0)+'</td>'
      + '<td class="num" style="color:var(--text3);font-size:10.5px">'
        + fmt0(s.inTok||0)+' → '+fmt0(s.outTok||0)+'</td>'
      + '<td class="num" style="font-weight:600">'
        + (noStepCredits ? '<span style="color:var(--text3)">—</span>' : c2(s.credits||0))+'</td>'
      + '<td class="num" style="color:var(--text3)">'
        + (noStepCredits ? '—' : pct1((s.credits||0)/total))+'</td>'
      + '<td>'+(rule ? '<span class="tag red sm">'+rule+'</span>' : '<span style="color:var(--text3)">—</span>')+'</td>'
      + '<td><span class="tag '+(s.err?'red':'green')+' sm">'+(s.err?'出错':'正常')+'</span></td>'
      + '<td class="num">'+(STEP_WASTE(s) ? c2(STEP_WASTE(s)) : '—')+'</td>'
      + '<td style="color:var(--text3)" title="'+esc(desc)+'">'+esc(desc)+'</td>'
      + '</tr>';
  });
  if (steps.length) tbl += padRows(page.rows.length, 12, PG_SIZE,
    '第 '+page.page+' 页共 '+page.rows.length+' 步');
  tbl += '</tbody></table></div>';

  return '<div class="card cr-steps">'
    + '<div class="card-hd"><div class="card-title">执行明细 · '
      + esc(String(t.name||'').slice(0,26))
      + ' <span class="tag gray sm">'+esc(t.id)+'</span></div>'
      + '<div class="card-sub">步骤 '+(t.steps||[]).length+' · 账面可回收 '+c2(waste)+' 积分'
      + ' · 输入/输出未分别计时，故只给步级总耗时</div></div>'
    + stepNote
    + '<div class="cr-steps-bar">'
      + segs.map(([k,lb,n])=>'<button class="seg-b'+(f===k?' on':'')+'" data-sf="'+k+'">'
          + lb+'<i>'+n+'</i></button>').join('')
      + '<button class="btn xs" id="lgExportSteps" style="margin-left:auto">导出明细</button>'
      + '<button class="btn xs" id="lgCloseSteps">收起</button>'
    + '</div>'
    + tbl + pagerHTML(page, 'spg')
    + '</div>';
}

/* ---------------- 3) 日终对账（T+1） ---------------- */
function renderLedgerDaily(){
  const C = CRED || {};
  const days = C.days || [];
  const T = C.totals || {};
  const t1 = C.t1 || {};

  if (!days.length){
    $('#ledgerBody').innerHTML = '<div class="hint">还没有可用于日终对账的数据：'
      + '至少需要一个有消耗的日子，以及可选的「初始积分 / 今日余额 / 积分流水」。</div>';
    return;
  }

  const last = days[days.length-1];
  const tol = C.tolerance || 5;
  const diffDays = days.filter(d=>d.status==='diff');
  const st = CRED_STATUS[t1.status] || CRED_STATUS.pending;
  /* 初始积分的来源：反推的值必须标出来，否则会被当成实测数 */
  const ISRC = {measured:'实测', derived:'反推', manual:'手填'}[
    (BUDGET||{}).initialSource] || '';

  /* ---- 顶部四格 + T+1 状态条 ---- */
  let h = '<div class="srow"><div class="srow-lb">对账全景'
    + '<span class="tag '+st.cls+' sm">'+esc(st.text)+'</span></div>'
    + '<div class="srow-grid g5">'
    + kpiMini('初始积分', C.seeded ? fmt0(C.initial) : '未设置', '积分',
        C.seeded?'var(--info-9)':'var(--text3)',
        (C.initialDate ? ('起点 '+C.initialDate.slice(5)) : '链路起点')
          + (ISRC ? ' · '+ISRC : ''))
    + kpiMini('日收合计', c2(T.income), '积分', 'var(--ok-10)',
        '领取 / 购买 · '+days.reduce((s,d)=>s+d.incomeN,0)+' 笔')
    + kpiMini('日耗合计', c2(T.spend), '积分', 'var(--warn-9)',
        '实测消耗 · '+days.reduce((s,d)=>s+d.spendN,0)+' 个任务')
    + kpiMini('期末日结余额', c2(last.close), '积分', 'var(--ok-11)',
        '截至 '+esc(last.date)+' · 次日起为日初')
    + kpiMini('偏差天数', fmt0(T.diff)+' / '+fmt0(T.days), '天',
        T.diff?'var(--bad-9)':'var(--ok-10)',
        T.diff ? ('偏差合计 '+c2(T.diffCredits)+' 积分') : '全部在容差内')
    + '</div></div>';

  /* ---- 余额三口径：可用 / 实时 / 日结（v2.1 §3.2.2：今日收入不得重复计入） ---- */
  h += balFigHTML(last);

  /* T+1 运行状态 + 三条口径，一条 5 秒能看完的说明条 */
  h += '<div class="t1bar '+(t1.status||'pending')+'">'
    + '<div class="t1-ic">T+1</div>'
    + '<div class="t1-txt">'
      + '<div class="t1-hd">T 日 <b>'+esc(t1.date||last.date)+'</b> 的账 · '
        + '<span class="tag '+st.cls+' sm">'+esc(st.text)+'</span>'
        + ' · 对账时点 <b>'+esc(t1.runAt||'—')+'</b></div>'
      + '<div class="t1-note">'+esc(t1.note||'')+'</div>'
      + '<div class="t1-rules">'
        + '<span><i>流水号</i>16 位 = 8 位日期 + 6 位任务名小写 + 2 位序列号</span>'
        + '<span><i>判定</i>|账户余额 − 日结余额| ≤ max(0.5 积分, |日初| × '
          + fmt0(tol)+'%)</span>'
        + '<span><i>结转</i>一致 → 日结余额结转；偏差 → 按账实调账结转</span>'
      + '</div>'
    + '</div>'
    + '</div>';

  /* ---- 台账操作条：执行对账 / 导出 ---- */
  h += '<div class="cr-bar"><span class="cr-bar-t">逐日链式结转 · 每页 '+PG_SIZE+' 天</span>'
    + '<button class="btn xs" id="lgRunRecon">执行对账</button>'
    + '<button class="btn xs" id="lgExportDaily">导出对账</button></div>';

  /* ---- 左：日链表 / 右：偏差与调账 ---- */
  let tbl = '<div class="tbl-wrap"><table class="tbl dense" style="table-layout:fixed">'
    + '<thead><tr>'
    + '<th>日期</th>'
    + '<th style="text-align:right">日收积分</th>'
    + '<th style="text-align:right">上一日日结余额</th>'
    + '<th style="text-align:right">日初积分</th>'
    + '<th style="text-align:right">日耗积分</th>'
    + '<th style="text-align:right">日结余额</th>'
    + '<th style="text-align:right">账户余额</th>'
    + '<th style="text-align:right">偏差</th>'
    + '<th>判定</th>'
    + '</tr></thead><tbody>';

  const pgD = pgSlice(days.slice().reverse(), LF.page, PG_SIZE);
  pgD.rows.forEach(d=>{
    const s = CRED_STATUS[d.status] || CRED_STATUS.nobal;
    const bad = d.status==='diff';
    tbl += '<tr class="'+(bad?'hl over':'')+'">'
      + '<td class="mono">'+esc(d.date)
        + (d.first?'<span class="tag blue sm" style="margin-left:5px">起点</span>':'')
        + '</td>'
      + '<td class="num" style="color:'+(d.income?'var(--ok-11)':'var(--text3)')+'">'
        + (d.income?('+'+c2(d.income)):'—')+'</td>'
      + '<td class="num" style="color:var(--text3)">'+c2(d.prevClose)+'</td>'
      + '<td class="num">'+c2(d.open)+'</td>'
      + '<td class="num" style="color:var(--warn-9)">−'+c2(d.spend)+'</td>'
      + '<td class="num" style="font-weight:650">'+c2(d.close)+'</td>'
      + '<td class="num" style="color:'+(d.account===null?'var(--text3)':'var(--info-11)')+'">'
        + (d.account===null?'—':c2(d.account))+'</td>'
      + '<td class="num" style="'+(bad?'color:var(--bad-9);font-weight:700':'color:var(--text3)')+'">'
        + (d.diff===null?'—':((d.diff>0?'+':'')+c2(d.diff)))+'</td>'
      + '<td><span class="tag '+s.cls+' sm">'+esc(s.text)+'</span></td>'
      + '</tr>';
  });
  if (days.length) tbl += padRows(pgD.rows.length, 9, PG_SIZE, '第 '+pgD.page+' 页共 '+pgD.rows.length+' 天');
  tbl += '</tbody></table></div>' + pagerHTML(pgD, 'page');

  const tableCard = cardHTML('逐日结转与账实核对',
    '日初积分 = 日收积分 + 上一日日结余额 · 日结余额 = 日初积分 − 日耗积分 · '
      + '账户余额 = 日结余额 即账实一致（容差 ±'+fmt0(tol)+'% / 0.5 积分）',
    tableCardBody(tbl, days, T, credWaterHTML(days)), 'cr-chain');

  /* 右卡：偏差与调账 + 第二来源 */
  let side = '';
  if (diffDays.length){
    side += '<div class="adj-hd"><span class="tag red sm">偏差 '+diffDays.length+' 天</span>'
      + '<span class="adj-sum">合计 '+c2(T.diffCredits)+' 积分</span></div>'
      + '<div class="adjlist">'
      + diffDays.slice().reverse().map(d=>{
          return '<div class="adj">'
            + '<div class="adj-top"><b>'+esc(d.date)+'</b>'
              + '<span class="tag red sm">'+(d.diff>0?'+':'')+c2(d.diff)+'</span></div>'
            + '<div class="adj-nums"><span>日结 '+c2(d.close)+'</span>'
              + '<span>实盘 '+c2(d.account)+'</span>'
              + '<span>调账 '+((d.adjust>0?'+':'')+c2(d.adjust))+'</span></div>'
            + '<div class="adj-hint">'+esc(d.hint||'')+'</div>'
            + '</div>';
        }).join('')
      + '</div>';
  } else {
    side += '<div class="hint" style="margin-bottom:0">'
      + (T.nobal ? ('有 '+T.nobal+' 天缺可比对的账户余额，其余日子无超容差偏差。')
                 : '所有已到对账时点的日子都在容差内。')
      + '</div>';
  }

  /* 结账摘要：偏差清单只有一两行时用它把卡片下半部补满，顺带给出一眼可读的期初期末 */
  side += '<div class="side-sep"></div>'
    + '<div class="st4">'
      + '<div><b>'+T.matched+'</b><span>账实一致</span></div>'
      + '<div class="'+(T.diff?'bad':'')+'"><b>'+T.diff+'</b><span>存在偏差</span></div>'
      + '<div><b>'+T.pending+'</b><span>待对账</span></div>'
      + '<div><b>'+T.nobal+'</b><span>无余额</span></div>'
    + '</div>';

  /* 偏差只有一两条时卡片下半截会空着：把「T+1 结转链路」放在这里，
     既填满版面，也把这一页的口径来源讲清楚（为什么是 T+1、余额怎么滚到次日） */
  side += '<div class="adj-flow">'
    + '<div class="af-hd">T+1 结转链路</div>'
    + [['T 日 24:00', '冻结当日流水，形成账面快照'],
       ['T+1 00:00', '定时任务对账：账户余额 vs 日结计算值'],
       ['对账完成', '日结余额转入次日「上一日日结余额」']]
        .map(([t,d])=>'<div class="af-i"><b>'+esc(t)+'</b><span>'+esc(d)+'</span></div>').join('')
    + '</div>';

  /* 对账参数：初始积分是日链起点，改了要按同一套规则重算，所以放在这里而不是别处 */
  const B = BUDGET || {};
  const th = B.thresholds || {};
  const inp = (id, lb, val, unit) =>
    '<label class="cfg2-f"><span>'+lb+(unit?' <i>'+unit+'</i>':'')+'</span>'
    + '<input id="'+id+'" type="number" step="100" min="0" value="'
      + ((val===null||val===undefined)?'':val)+'" placeholder="填入"></label>';
  const cfg = '<div class="cfg2">'
      + inp('ovInit','初始积分', C.seeded ? C.initial : B.initial, '积分')
      + inp('ovTb','今日余额', B.todayBalance, '积分')
    + '</div>'
    + '<div class="cfg2 cfg2-th">'
      + inp('ovWarn','黄', th.warn, '%')
      + inp('ovCrit','橙', th.crit, '%')
      + inp('ovRed','红', th.red, '%')
    + '</div>'
    + '<button class="btn sm pri" id="lgSaveBal" style="margin-top:8px;width:100%"'
      + ' title="写进报告请运行 audit.py --set-initial / --set-today-balance / --set-thresholds">'
      + '保存并重算日链</button>';

  /* 右列只放一张卡：偏差清单 + 结账摘要，对账参数折进 <details>。
     参数是「偶尔改一次」的东西，展开收起比常驻更省版面；
     也给不出偏差的日子留出空间，不至于卡片下半截空着。 */
  h += '<div class="grid cr-daily">'
    + tableCard
    + '<div class="cr-right">'
      + cardHTML('偏差与调账', '超容差的日子逐个列明，按账实为准结转',
          side
            + '<details class="side-cfg"><summary>'
              + '<span>对账参数</span><i>初始积分 / 今日余额 / 告警阈值</i>'
              + '</summary>' + cfg + '</details>',
          'cr-side')
    + '</div>'
    + '</div>';

  $('#ledgerBody').innerHTML = h;
}

/* 日结余额水位：表格行数少时用它把卡片下半部填满，
   顺便让「余额一路怎么掉下来的」一眼可见，而不是只看一排数字 */
function credWaterHTML(days){
  if (!days.length) return '';
  const closes = days.map(d=>d.close||0);
  const hi = Math.max.apply(null, closes), lo = Math.min.apply(null, closes);
  const span = hi - lo;
  const rows = days.slice().reverse().map((d,i)=>{
    /* 按区间映射到 12%—100%：日结余额彼此接近时也能看出「一天掉了多少」 */
    const w = span > 0 ? 12 + ((d.close||0) - lo) / span * 88 : 60;
    return '<div class="cw'+(d.status==='diff'?' diff':'')+(i===0?' last':'')+'">'
      + '<span>'+esc(d.date.slice(5))+'</span>'
      + '<i><b style="width:'+w.toFixed(1)+'%"></b></i>'
      + '<em>'+c2(d.close)+'</em>'
      + '</div>';
  }).join('');
  return '<div class="cr-water">'
    + '<div class="cw-hd">日结余额水位'
      + '<span>条形按区间内最高/最低日结余额映射 · 红色为存在偏差的日子</span></div>'
    + '<div class="cw-rows">'+rows+'</div>'
    + '</div>';
}

/* 日链表下的脚注：把「第二来源对账」与「结转规则」压成两行，
   说明缺余额的日子为什么对不上，避免误读 */
function tableCardBody(tbl, days, T, water){
  const nobal = days.filter(d=>d.status==='nobal').length;
  const pending = days.filter(d=>d.status==='pending').length;
  const C = CRED || {};
  const tol = C.tolerance || 5;
  const R = (BUDGET||{}).reconcile || {};
  const vd = R.diffPct;
  const over = vd !== null && vd !== undefined && Math.abs(vd) > tol;
  const second = R.available
    ? '第二来源　'+esc(R.label||'')+'　差异 <b style="color:'
        + (over?'var(--bad-9)':'var(--ok-11)')+'">'
        + (vd===null||vd===undefined?'—':((vd>0?'+':'')+vd+'%'))+'</b>'
        + (over?'（超容差）':'（在容差内）')
    : '第二来源　无（未提供积分流水，且会话积分表与日志无交集）';
  return tbl
    + (water || '')
    + '<div class="cr-foot">'
      + '<div class="cr-foot-l">共 '+days.length+' 天 · 账实一致 <b>'+T.matched+'</b>'
        + (T.diff?' · <b style="color:var(--bad-11)">偏差 '+T.diff+'</b>':'')
        + (pending?' · 待对账 '+pending:'')+(nobal?' · 无实际余额 '+nobal:'')+'</div>'
      + '<div class="cr-foot-r">'+second+'</div>'
    + '</div>'
    + '<div class="cr-foot sub">'
      + '<span>结转规则　'+esc(C.carryPolicy||'')+'</span>'
      + '<span class="ml-auto">'+(nobal?'「无实际余额」= 当天既无积分流水的 balance_after，也未填今日余额，只能报账面值。':'')+'</span>'
    + '</div>';
}

/* 余额三口径条：把「可用 / 实时 / 日结」摆在一起，并把差异解释成“今日待确认收入”。
   这样任何人都能一眼验证：这三者里，今日收入到底被加了几次。 */
function balFigHTML(last){
  const nn = v => (v === null || v === undefined) ? '—' : c2(v);
  const conf = last.income || 0, pen = last.incomePending || 0;
  const av = last.avail, rt = last.realtime, cl = last.close;
  const gap = (rt !== null && rt !== undefined && cl !== null && cl !== undefined)
    ? Math.round((rt - cl) * 100) / 100 : null;
  const ok = gap !== null && Math.abs(gap - pen) < 0.005;
  const TIP = {
    av: '当前可用余额 = 日初结转 − 今日消耗 · 不含任何今日收入，是告警与冻结判断的基准',
    inc:'今日收入：已入账的进账本与日结计算值；草稿（未提交）的只进实时余额',
    rt: '当前实时余额 = 当前可用余额 + 今日已确认收入 + 今日待确认收入',
    cl: '日结计算值 = 日初结转 + 今日已确认收入 − 今日消耗，唯一的账本口径'
  };
  return '<div class="balrow">'
    + '<div class="bal-c" title="'+TIP.av+'"><span>当前可用余额</span><b>'+nn(av)+'</b></div>'
    + '<div class="bal-op">+</div>'
    + '<div class="bal-c" title="'+TIP.inc+'"><span>今日收入 已确认 / 待确认</span>'
      + '<b>'+nn(conf)+' <em>/</em> '+nn(pen)+'</b>'
      + ((last.incomePendingN||0) ? '<span style="font-size:8.6px">待确认 '
          + last.incomePendingN+' 笔 · 不入账本</span>' : '') + '</div>'
    + '<div class="bal-op">=</div>'
    + '<div class="bal-c" title="'+TIP.rt+'"><span>当前实时余额</span><b>'+nn(rt)+'</b></div>'
    + '<div class="bal-op">·</div>'
    + '<div class="bal-c" title="'+TIP.cl+'"><span>日结计算值</span><b>'+nn(cl)+'</b></div>'
    + '<div class="bal-id'+(ok?' ok':' bad')+'">自检 实时 − 日结 = <b>'+nn(gap)+'</b>'
      + ' = 今日待确认收入 <b>'+nn(pen)+'</b>'
      + (ok ? ' ✓ 无重复计入' : ' ⚠ 需核对')+'</div>'
    + snapStrip()
    + '</div>';
}

/* 余额凭证条：日初结转这个数是从哪张截图来的。
   不说清楚的话，用户会以为 1,080.50 是自己填的——实际是从截图反推的。
   条窄，所以只留「截图值 → 日初值 + 哈希 + 校验结论」，反推算式挪进 title。 */
function snapStrip(){
  const B = BUDGET || {};
  const s = B.snapshot;
  if (!s) {
    return '<div class="bal-snap noshot" title="今日余额没有绑定凭证">'
      + '<b>余额无凭证</b><span>这个数字目前无法自证：没有截图、没有哈希，'
      + '事后无法验证填的时候屏幕上写的是不是它</span></div>';
  }
  const tone = s.state === 'ok' ? 'ok'
             : (s.state === 'stale' ? 'warn' : 'bad');
  const sha8 = (s.sha256 || '').slice(0, 8);
  /* c2 不带千分位，跟旁边 KPI 的 1,006.60 放一起会显得两套写法，这里单独分组 */
  const n2 = v => Number(v).toLocaleString('zh-CN',
    {minimumFractionDigits:2, maximumFractionDigits:2});
  const acc = (s.account !== null && s.account !== undefined) ? n2(s.account) : '—';
  const open = (s.dayOpen !== null && s.dayOpen !== undefined) ? n2(s.dayOpen) : '—';
  const gap = (s.account !== null && s.account !== undefined
               && s.dayOpen !== null && s.dayOpen !== undefined)
    ? Math.round((s.dayOpen - s.account) * 100) / 100 : null;
  const tip = '截图余额 ' + acc + (gap ? ' + 时刻前已消耗 ' + n2(gap) : '')
    + ' = 日初 ' + open
    + '\n截图时刻 ' + (s.takenAt || '—')
    + '\nSHA256 ' + (s.sha256 || '—')
    + '\n文件 ' + (s.file || '—')
    + (s.staleReason ? '\n失效原因 ' + s.staleReason : '');
  return '<div class="bal-snap '+tone+'" title="'+esc(tip)+'">'
    + '<i>余额凭证</i>'
    + '<span>截图 <b>'+acc+'</b> @ '+esc((s.takenAt||'').slice(5,16))
      + ' → 日初 <b>'+open+'</b></span>'
    + '<em class="mono">SNP '+esc(sha8)+'</em>'
    + '<u>'+esc(s.stateText || '')+'</u>'
    + '</div>';
}

/* ---------------- 4) 异常处置（识别—确认—止损—留痕） ---------------- */
function renderLedgerAnomaly(){
  /* v3.3：回收调整页的构成图已删除，这里只剩同一套「引擎 + 事件」视图；
     处置动作完成后由 afterDispose() 决定重画哪一面，不再各自硬编码。 */
  $('#ledgerBody').innerHTML = anomalyHTML(ledgerRows());
}

/* ---------------- 余额设置（保留原能力） ---------------- */
function saveLedgerBalance(){
  const g = id => { const el=document.getElementById(id); return el?String(el.value).trim():''; };
  const init = g('ovInit'), tb = g('ovTb');
  if (init === '' || tb === ''){ toast('初始积分与今日余额都要填'); return; }
  const nInit = +init, nTb = +tb;
  if (isNaN(nInit) || isNaN(nTb) || nInit < 0 || nTb < 0){
    toast('初始积分与今日余额要是非负数字'); return;
  }
  const raw = {warn:g('ovWarn'), crit:g('ovCrit'), red:g('ovRed')};
  const th = normThresholds(Object.assign({mode:'pct'}, raw));
  const fixed = thFixedNotice(raw, th);
  const persisted = saveLocalBalance(nInit, nTb, th, BUDGET.today || null);
  /* 初始积分变了 = 日链起点变了：先按同一套规则重算，再刷新各视图，
     否则用户改完参数看到的还是旧链条 */
  CRED.initial = nInit;
  CRED.seeded = true;
  credRebuild();
  refreshAfterBalance();
  const tail = '当前余额 '+fmt0(BUDGET.balance)+' · 今日消耗占比 '
             + (BUDGET.spendRatioOver ? '>100' : fmt1(BUDGET.spendRatio)) + '%'
             + ' · '+BUDGET.levelText+'，监控总览已同步'+fixed;
  toast(persisted ? ('已保存并写入浏览器：'+tail)
                  : ('已生效（本次会话）：'+tail+'。本浏览器禁用了本地存储，刷新后回到报告值，'
                     + '长期生效请运行 audit.py --set-initial / --set-today-balance'));
}

function bindLedgerFilters(){
  const kw = document.getElementById('lgKw');
  if (kw){
    kw.addEventListener('keydown', e=>{
      if (e.key==='Enter'){ LF.kw = kw.value; LF.page=1; ledgerRenderAll(); }
    });
    kw.addEventListener('blur', ()=>{ if (kw.value!==LF.kw){ LF.kw=kw.value; LF.page=1; ledgerRenderAll(); } });
  }
  const bind = (id, key) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', ()=>{ LF[key]=el.value; LF.page=1; ledgerRenderAll(); });
  };
  bind('lgFrom','from'); bind('lgTo','to'); bind('lgType','type');
  bind('lgModel','model'); bind('lgProject','project'); bind('lgSort','sort');

  const quick = document.getElementById('lgQuick');
  if (quick){
    quick.addEventListener('click', e=>{
      const b = e.target.closest('button'); if(!b) return;
      const d = +b.dataset.d;
      $$('#lgQuick button').forEach(x=>x.classList.toggle('on', x===b));
      LF.from = ''; LF.to = '';
      if (d){
        const dates = TASKS.map(t=>t.date).filter(Boolean).sort();
        const end = dates[dates.length-1] || '';
        if (end){
          const anchor = new Date(end+'T00:00:00');
          anchor.setDate(anchor.getDate() - (d-1));
          LF.from = anchor.toISOString().slice(0,10);
          LF.to = end;
        }
      }
      LF.page = 1;
      ledgerRenderAll();
    });
  }
  const ex = document.getElementById('lgExport');
  if (ex) ex.addEventListener('click', ()=>exportCurrent());
}

/* ================= 导出（当前页 + 当前筛选） ================= */
/* 导出的都是「筛选后的全量」，不是当前这一页——审计留档要的是完整集合，
   分页只是阅读方式。文件名带日期，便于和多份报告放在一起。 */
function csvDay(){
  const R = credRange();
  const t = (R.single ? R.from : (R.from + '_' + R.to)) || 'all';
  return t.replace(/-/g,'');
}

function exportFlow(){
  const rows = credFlowOf();
  exportCSV('recon-flow-' + csvDay() + '.csv',
    ['交易流水号','日期','时间','收支','摘要','任务ID','积分','累计耗时(秒)','触发方式','状态'],
    rows.map(r=>[r.txn||'', r.date||'', r.ts||'', r.dir==='in'?'收入':'支出', r.name||'',
      r.taskId||'', c2(r.credits), Math.round(r.duration||0), r.trigger||'', r.status||'']));
}

function exportExpense(){
  const rows = credFlowOf().filter(r=>r.dir==='out');
  exportCSV('recon-expense-' + csvDay() + '.csv',
    ['任务类型','任务名称','任务ID','交易流水号','触发方式','模型','模式(复杂度)','步长',
     '消耗积分','累计耗时(秒)','状态'],
    rows.map(r=>{
      const t = taskOfId(r.taskId) || {};
      return [t.type||'', r.name||'', r.taskId||'', r.txn||'', TRIG_TEXT[r.trigger]||r.trigger||'',
        r.model||'', t.level||'', (r.steps||((t.nModelSteps||0)+(t.nTools||0))||''),
        c2(r.credits), Math.round(r.duration||0), r.status||''];
    }));
}

function exportSteps(){
  const t = taskOfId(LF.sel);
  if (!t){ toast('先展开一个任务，再导出执行明细'); return; }
  const steps = stepListOf(t);
  /* 与页面同口径：没有逐步积分的任务导出空值，不写 0 假装是 0 积分步骤 */
  const stepSum = (t.steps||[]).reduce((s,x)=>s+(x.credits||0),0);
  const noStepCredits = !stepSum && (t.credits||0) > 0;
  exportCSV('recon-steps-' + String(t.id).replace(/[^\w-]/g,'') + '.csv',
    ['阶段','步骤','类型','模型/工具','技能','耗时(毫秒)','输入token','输出token','积分',
     '占比','命中规则','状态','可回收积分','描述'],
    steps.map(s=>[s.phase||'', s.i, s.kind==='tool'?'工具步':'模型步',
      s.kind==='tool'?(s.tool||''):(s.model||''), s.kind==='tool'?(s.tool||''):'',
      s.ms||0, s.inTok||0, s.outTok||0,
      noStepCredits ? '' : c2(s.credits),
      noStepCredits ? '' : pct1((s.credits||0)/(t.credits||1)),
      STEP_RULE(s)||'', s.err?'出错':'正常', STEP_WASTE(s)?c2(STEP_WASTE(s)):'',
      s.kind==='tool'?('调用工具 '+(s.tool||'')):(s.phase||'')]));
}

function exportDaily(){
  const rows = (CRED.days||[]);
  exportCSV('recon-daily.csv',
    ['日期','日初结转','收入','消耗','日结','账户余额','偏差','偏差率%','判定','归因提示'],
    rows.map(d=>[d.date, c2(d.open), c2(d.income), c2(d.spend), c2(d.close),
      d.account===null||d.account===undefined?'':c2(d.account),
      d.diff===null||d.diff===undefined?'':c2(d.diff),
      d.diffPct===null||d.diffPct===undefined?'':fmt1(d.diffPct),
      d.statusText||'', d.hint||'']));
}

function exportBook(){
  const rows = bookRows();
  exportCSV('recon-book.csv',
    ['日期','日初结转','收入','消耗','日结','账户余额','偏差','偏差率%','判定','是否T+1','归因提示'],
    rows.map(d=>[d.date, c2(d.open), c2(d.income), c2(d.spend), c2(d.close),
      d.account===null||d.account===undefined?'':c2(d.account),
      d.diff===null||d.diff===undefined?'':c2(d.diff),
      d.diffPct===null||d.diffPct===undefined?'':fmt1(d.diffPct),
      d.statusText||'', d.provisional?'待T+1':'已判定', d.hint||'']));
}

function exportCurrent(){
  if (LF.tab === 'book') return exportBook();
  if (LF.tab === 'expense') return exportExpense();
  if (LF.tab === 'daily') return exportDaily();
  return exportFlow();
}

/* ================= 4) 对账台簿 =================
   台簿看的是「跨日的账」：每天一行，日初 → 收入 → 消耗 → 日结，
   右边跟账户余额与偏差。它是日终对账的**台账视图**，不做新计算，
   只把 CRED.days 摊平成可导出的账页，让「这条链对不对」能一行一行核。 */
function bookRows(){
  let list = (CRED.days || []).slice();
  if (LF.from) list = list.filter(d => (d.date||'') >= LF.from);
  if (LF.to) list = list.filter(d => (d.date||'') <= LF.to);
  const st = LF.bookSt || 'all';
  if (st === 'ok') list = list.filter(d => d.ok === true);
  else if (st === 'bad') list = list.filter(d => d.ok === false);
  else if (st === 'nobal') list = list.filter(d => d.status === 'nobal');
  return list.sort((a,b)=>(a.date||'').localeCompare(b.date||''));
}

function renderBook(){
  const rows = bookRows();
  const page = pgSlice(rows, LF.pageB, LF.size);
  const sum = k => rows.reduce((s,d)=>s + (d[k]||0), 0);
  const bad = rows.filter(d=>d.ok===false).length;
  const nobal = rows.filter(d=>d.status==='nobal').length;
  const last = rows.length ? rows[rows.length-1] : null;

  const st = LF.bookSt || 'all';
  const segs = [['all','全部',rows.length],['ok','相符',rows.filter(d=>d.ok===true).length],
                ['bad','超容差',bad],['nobal','无实际余额',nobal]];

  let h = '<div class="srow"><div class="srow-lb">周期台账'
    + '<span class="tag gray sm">每页 '+PG_SIZE+' 行</span></div><div class="srow-grid g4">'
    + kpiMini('期间结转', c2(sum('open')), '积分', 'var(--info-9)', rows.length+' 天日初之和')
    + kpiMini('期间消耗', c2(sum('spend')), '积分', 'var(--warn-9)',
        '均 '+c2(rows.length ? sum('spend')/rows.length : 0)+' / 天')
    + kpiMini('期间收入', c2(sum('income')), '积分', 'var(--ok-10)', '仅已入账')
    + kpiMini('期末日结', last ? c2(last.close) : '—', '积分',
        bad ? 'var(--bad-11)' : 'var(--ok-11)', bad ? (bad+' 天超容差') : '账实相符')
    + '</div></div>';

  h += '<div class="lfbook">'
    + '<div class="card lg-book-chart"><div class="card-hd">'
      + '<div class="card-title">周期性对账分析</div>'
      + '<div class="card-sub">柱：日收 / 日耗 · 线：日结余额</div></div>'
      + '<div class="chart" id="bkChart"></div></div>'
    + '<div class="card lg-book-tbl"><div class="card-hd">'
      + '<div class="card-title">对账台账</div>'
      + '<div class="seg" id="bkSeg">'
      + segs.map(([k,lb,n])=>'<button data-bst="'+k+'" class="'+(st===k?'on':'')+'">'
          + lb+'<i style="font-style:normal;opacity:.6;margin-left:3px">'+n+'</i></button>').join('')
      + '</div></div>';

  let tbl = '<div class="tbl-wrap"><table class="tbl" style="table-layout:fixed">'
    + '<thead><tr><th>日期</th><th style="text-align:right">日初结转</th>'
    + '<th style="text-align:right">收入</th><th style="text-align:right">消耗</th>'
    + '<th style="text-align:right">日结</th><th style="text-align:right">账户余额</th>'
    + '<th style="text-align:right">偏差</th><th>判定</th><th style="width:26%">归因提示</th>'
    + '</tr></thead><tbody>';
  if (!rows.length){
    tbl += '<tr><td colspan="9" style="text-align:center;color:var(--text3);padding:30px">'
      + '当前筛选下没有账页</td></tr>';
  }
  page.rows.forEach(d=>{
    const s = CRED_STATUS[d.status] || CRED_STATUS.nobal;
    const diff = (d.diff === null || d.diff === undefined) ? null : d.diff;
    tbl += '<tr>'
      + '<td class="mono">'+esc(d.date)+'</td>'
      + '<td class="num">'+c2(d.open)+'</td>'
      + '<td class="num" style="color:'+(d.income?'var(--ok-10)':'var(--text3)')+'">'+c2(d.income)+'</td>'
      + '<td class="num" style="color:var(--warn-soft)">'+c2(d.spend)+'</td>'
      + '<td class="num" style="font-weight:640">'+c2(d.close)+'</td>'
      + '<td class="num">'+(d.account===null||d.account===undefined
          ? '<span style="color:var(--text3)">未提供</span>' : c2(d.account))+'</td>'
      + '<td class="num" style="color:'+(diff===null?'var(--text3)':(d.ok===false?'var(--bad-11)':'var(--ok-11)'))+'">'
        + (diff===null ? '—' : ((diff>0?'+':'')+c2(diff)))+'</td>'
      + '<td><span class="tag '+(s.cls==='gray'?'gray':s.cls)+' sm">'+esc(s.text)+'</span>'
        + (d.provisional?' <span class="tag gray sm">待T+1</span>':'')+'</td>'
      + '<td style="color:var(--text3)" title="'+esc(d.hint||'')+'">'+esc(d.hint||'—')+'</td>'
      + '</tr>';
  });
  if (rows.length) tbl += padRows(page.rows.length, 9, PG_SIZE, '第 '+page.page+' 页共 '+page.rows.length+' 条账页');
  tbl += '</tbody></table></div>' + pagerHTML(page, 'pgb');

  h += tbl
    + '<div class="book-foot">'
      + '<span>台簿口径：日初 = 上一日日结；日结 = 日初 + 日收 − 日耗；偏差 = 账户余额 − 日结</span>'
      + '<button class="btn xs" id="lgExportBook">导出台簿</button>'
    + '</div></div></div>';

  $('#ledgerBody').innerHTML = h;
  drawBookChart(rows);
}

function drawBookChart(rows){
  if (!rows.length) return;
  const labels = rows.map(d=>d.date.slice(5).replace('-','/'));
  chart('bkChart', {
    animationDuration:600,
    tooltip:Object.assign({trigger:'axis', axisPointer:{type:'shadow',
      shadowStyle:{color:varrgb('--pri',.06)}},
      formatter:ps=>{
        const i = ps[0].dataIndex, d = rows[i];
        return '<b>'+d.date+'</b><br/>日初 '+c2(d.open)+' · 收入 '+c2(d.income)
          + '<br/>消耗 '+c2(d.spend)+' · 日结 '+c2(d.close)
          + (d.account===null||d.account===undefined?'':'<br/>账户 '+c2(d.account));
      }}, tooltipBase),
    legend:{data:['收入','消耗','日结余额'], right:0, top:0, itemWidth:8, itemHeight:8,
      itemGap:12, textStyle:{color:TT.text3, fontSize:10}, icon:'roundRect'},
    grid:baseGrid({top:24, bottom:0, right:8, left:2}),
    xAxis:axisX({data:labels, axisLabel:{color:TT.text3, fontSize:9.5}}),
    yAxis:axisY({axisLabel:{color:TT.text3, fontSize:9.5, formatter:v=>fmt(v)}, splitNumber:3}),
    series:[
      {name:'收入', type:'bar', barWidth:'32%', data:rows.map(d=>c2(d.income)),
       itemStyle:{color:'var(--ok-9)', borderRadius:[2,2,0,0]}},
      {name:'消耗', type:'bar', barWidth:'32%', data:rows.map(d=>c2(d.spend)),
       itemStyle:{color:'var(--info-9)', borderRadius:[2,2,0,0]}},
      {name:'日结余额', type:'line', smooth:.3, symbol:'circle', symbolSize:4,
       data:rows.map(d=>c2(d.close)),
       lineStyle:{width:2, color:'var(--holo-cyan)', shadowColor:'var(--glow)', shadowBlur:8},
       itemStyle:{color:'var(--holo-cyan)'}}
    ]
  });
}

/* ================= 执行对账（页面内重算） =================
   页面是离线单文件，不能真的去跑定时任务。这里做的是「按当前填写的
   初始积分/今日余额/阈值重算整条日链」，让用户当场看到结果；真正的
   T+1 判定与写盘仍由 recon_daily.py 在次日 00:00 完成。 */
function runReconNow(){
  try { credRebuild(); } catch(e){}
  ledgerRenderAll();
  const t1 = CRED.t1 || {};
  toast(t1.status === 'done'
    ? '已重算日链 · T+1 对账已于 ' + (t1.runAt||'') + ' 完成'
    : '已按当前参数重算日链（页面内）· 写盘与 T+1 判定请运行 recon_daily.py --verify');
}

