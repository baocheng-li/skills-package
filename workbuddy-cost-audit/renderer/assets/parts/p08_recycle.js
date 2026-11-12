/* ================= 异常处置（并入「积分对账」标签页） =================
 * v2.1 口径：积分一旦被任务消耗，就是已发生的事实，不存在“退回积分”。
 * 因此本页不再提供任何回收能力，只做四件事：
 *   检测（规则命中）→ 确认（人判断）→ 止损（改配置/限模型/冻结额度）→ 证据留存（可导出）
 * 证据链完整保留，将来厂商支持补偿时可据此申请 —— 但今天不做任何追回承诺。
 */
const RULE_STATUS = {
  hit:{t:'命中', c:'red'},
  clear:{t:'未命中', c:'gray'},
  insufficient:{t:'样本不足', c:'amber'},
  unsupported:{t:'数据源不支持', c:'gray'},
  disabled:{t:'已关闭', c:'gray'}
};

/* 异常事件状态机（v2.1 §3.7.2）：OPEN → INVESTIGATING → CONFIRMED → MITIGATING → MITIGATED → CLOSED / IGNORED */
const ANOM_ST = {
  open:          {t:'待调查', c:'red',   cls:'red'},
  investigating: {t:'调查中', c:'amber', cls:'amber'},
  confirmed:     {t:'已确认', c:'red',   cls:'red'},
  mitigating:    {t:'止损中', c:'amber', cls:'amber'},
  mitigated:     {t:'已止损', c:'green', cls:'green'},
  closed:        {t:'已关闭', c:'gray',  cls:'gray'},
  ignored:       {t:'已忽略', c:'gray',  cls:'gray'}
};

/* 止损措施清单（v2.1 §3.7.3 X4）：全部作用于配置/额度/通知，不产生积分流水 */
const MEASURES = [
  {k:'freeze',   t:'冻结关联额度',     d:'冻结该任务的可用额度，防止继续消耗', arg:'冻结额度（积分）／期限（天）'},
  {k:'taskCfg',  t:'调整任务配置',     d:'拆分任务、限制轮次、缩短超时',       arg:'目标 task_id 已预填'},
  {k:'modelLimit',t:'限制模型使用',    d:'限制高消耗模型的使用场景或并发',     arg:'按主模型锁定'},
  {k:'cron',     t:'降低定时任务频率', d:'降低同类定时任务触发频率',           arg:'按触发方式筛出同类任务'},
  {k:'pauseBg',  t:'暂停后台隐藏任务', d:'暂停后台自动化与隐藏调用',           arg:'按 trigger_type 定位'},
  {k:'notify',   t:'通知负责人',       d:'通知任务负责人与预算责任人',         arg:'通知对象／备注'}
];
/* 规则 → 建议的止损措施（默认勾选），避免每次都从零选 */
const RULE_MEASURE = {
  RETRY_STORM:['taskCfg','pauseBg','notify'],
  TOOL_FAIL_LOOP:['taskCfg','pauseBg','notify'],
  CONTEXT_BLOAT:['taskCfg','modelLimit','notify'],
  LONG_CONTEXT_NO_COMPACT:['taskCfg','modelLimit','notify'],
  MODEL_SWITCH:['modelLimit','notify'],
  SINGLE_STEP_BURST:['freeze','modelLimit','notify'],
  ORPHAN_TASK:['pauseBg','notify'],
  BALANCE_DROP:['freeze','notify','pauseBg']
};

const DISP_KEY = 'wbc.anomaly.disp.v1';
let DISP = null;
/* rule 是「左侧审计引擎点中的规则」，dst 是处置状态，两者是并集关系：
   先按规则筛、再按状态筛，最后才分页——顺序反了会出现「第 2 页之后点规则没反应」。 */
let AF = {dst:'all', rule:'', page:1};

function dispLoad(){
  if (DISP) return DISP;
  try { DISP = JSON.parse(localStorage.getItem(DISP_KEY) || '{}') || {}; }
  catch(e){ DISP = {}; }
  if (!DISP || typeof DISP !== 'object') DISP = {};
  return DISP;
}
function dispSave(){
  try { localStorage.setItem(DISP_KEY, JSON.stringify(DISP || {})); } catch(e){}
}
function dispOf(t){ return dispLoad()[t.id] || null; }
function dispStatus(t){ return (dispOf(t) || {}).status || 'open'; }
function dispLog(t){ return (dispOf(t) || {}).log || []; }
function nowStamp(){
  const d = new Date(), p = n=>String(n).padStart(2,'0');
  return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes());
}

/* 严重级别：按该任务的止损空间估算分档（1—4） */
function evSeverity(t){
  const w = t.waste || 0;
  return w >= 100 ? 4 : w >= 30 ? 3 : w > 0 ? 2 : 1;
}
function suggestMeasures(t){
  const ids = t.ruleIds || t.flags || [];
  const set = {};
  ids.forEach(id=>{ (RULE_MEASURE[id] || ['taskCfg','notify']).forEach(k=>set[k]=1); });
  if (!Object.keys(set).length) set.taskCfg = 1, set.notify = 1;
  return Object.keys(set);
}

/* ---- 异常管理质量指标：确认率 / 止损覆盖率 / 重复率（衡量“止损做得好不好”） ---- */
function anomalyStats(list){
  const det = list.filter(t=>(t.flags||[]).length>0 && t.waste>0);
  const confirmed = det.filter(t=>['confirmed','mitigating','mitigated','closed'].indexOf(dispStatus(t))>=0
                              && (dispOf(t)||{}).conclusion !== '误报');
  const covered = confirmed.filter(t=>((dispOf(t)||{}).measures||[]).length>0);
  const closed  = det.filter(t=>['mitigated','closed','ignored'].indexOf(dispStatus(t))>=0);
  /* 重复率：某类规则在已止损/已关闭后，仍有未闭环的同规则事件 */
  const doneRules = {};
  det.forEach(t=>{ if(['mitigated','closed'].indexOf(dispStatus(t))>=0) (t.ruleIds||[]).forEach(r=>doneRules[r]=1); });
  const rep = det.filter(t=>['mitigated','closed'].indexOf(dispStatus(t))<0
                            && (t.ruleIds||[]).some(r=>doneRules[r])).length;
  const dst = {};
  det.forEach(t=>{ const s = dispStatus(t); dst[s] = (dst[s]||0)+1; });
  return {
    detected: det.length,
    list: list,
    confirmed: confirmed.length,
    covered: covered.length,
    closed: closed.length,
    repeat: rep,
    dist: dst,
    confirmRate: det.length ? confirmed.length/det.length : 0,
    coverRate: confirmed.length ? covered.length/confirmed.length : 0,
    repeatRate: det.length ? rep/det.length : 0
  };
}

/* list 为「统一筛选」后的任务集；不传则按窗口内全部任务 */
function anomalyHTML(list){
  const scope = Array.isArray(list) ? list : TASKS;
  const anomAll = scope.filter(t=>(t.flags||[]).length>0 && t.waste>0)
    .sort((a,b)=>b.waste-a.waste);
  /* 规则筛选在截断之前：先筛再截，否则点靠后的规则会查到空集。
     键要同时认 id 与名称：采集侧 ruleIds 与 flags 并不总是齐全，
     只认一个会出现「点了有命中的规则，列表却是空的」。 */
  const picked = AF.rule ? RULES.filter(r=>r.id === AF.rule)[0] : null;
  const hitRule = t => {
    const ids = t.ruleIds || [], names = t.flags || [];
    if (ids.indexOf(AF.rule) >= 0 || names.indexOf(AF.rule) >= 0) return true;
    return !!(picked && names.indexOf(picked.name) >= 0);
  };
  const anom = (AF.rule ? anomAll.filter(hitRule) : anomAll).slice(0, 60);
  const S = anomalyStats(scope);
  const waste = Math.round(scope.reduce((s,t)=>s+(t.waste||0),0));
  const totalCr = scope.reduce((s,t)=>s+t.credits,0) || 1;
  const enabled = RULES.filter(r=>r.status!=='disabled');
  const hitRules = RULES.filter(r=>r.hits>0);
  const unsupported = RULES.filter(r=>r.status==='unsupported');
  const insufficient = RULES.filter(r=>r.status==='insufficient');

  /* 状态筛选 */
  const chips = [['all','全部'],['open','待处置'],['confirmed','已确认'],
                 ['mitigated','已止损'],['closed','已关闭']];
  const shownAll = AF.dst==='all' ? anom
    : AF.dst==='open' ? anom.filter(t=>['open','investigating','mitigating'].indexOf(dispStatus(t))>=0)
    : anom.filter(t=>dispStatus(t)===AF.dst ||
        (AF.dst==='mitigated' && dispStatus(t)==='mitigating'));
  /* 每页 10 行：与左侧规则列表同屏对齐，翻页比在一张长表里捞行便宜得多 */
  const page = pgSlice(shownAll, AF.page, PG_SIZE);
  const shown = page.rows;

  let h = '<div class="honest-bar">'
    + '<b>诚实提示：</b>任务执行已产生真实资源消耗，积分扣减是计费事实，'
    + '<b>不存在“退回积分”</b>。本页的能力是<b>识别异常 → 确认归因 → 止损防止继续消耗 → 留存证据</b>；'
    + '下面的「止损空间」是<b>同类行为继续发生时可避免的消耗</b>，不是可退回金额。'
    + '</div>';

  h += '<div class="grid g4 anom-kpi">'
    + kpiMini('命中异常任务', fmt0(S.detected), '个', 'var(--bad-9)',
        '占当前口径 '+pct(S.detected/(scope.length||1))+' · 严重级别按影响积分分档')
    + kpiMini('止损空间（估算）', fmt0(waste), '积分', 'var(--warn-9)',
        '占当前口径 '+pct(waste/totalCr)+' · 已发生，向前止损而非回溯退分')
    + kpiMini('异常确认率', pct(S.confirmRate), '', 'var(--iris-11)',
        '已确认 '+S.confirmed+' / 检测 '+S.detected+' 条 · 反映规则准确性')
    + kpiMini('止损覆盖率', pct(S.coverRate), '', 'var(--ok-10)',
        '已采取止损措施 '+S.covered+' / 已确认 '+S.confirmed+' · 目标 100%')
    + '</div>';

  h += '<div class="anom-meta">'
    + '<span>命中规则 <b>'+hitRules.length+'</b> / '+enabled.length+' 条</span>'
    + '<span>样本不足 <b>'+insufficient.length+'</b> · 数据缺失 <b>'+unsupported.length+'</b></span>'
    + '<span>已闭环 <b>'+S.closed+'</b> 条</span>'
    + '<span>异常重复率 <b>'+pct(S.repeatRate)+'</b>'
      + '<i title="同类规则在已止损/已关闭后仍有未闭环事件的比例，反映止损措施是否有实效">?</i></span>'
    + '</div>';

  h += '<div class="grid anom-grid">';

  /* ---------- 审计引擎（v3.3 更名：它不生成规则，只按规则判定与筛选） ---------- */
  let rules = '<div class="anom-rules">';
  RULES.forEach(r=>{
    const st = RULE_STATUS[r.status] || RULE_STATUS.clear;
    const on = AF.rule === r.id;
    rules += '<div class="rule'+(on?' on':'')+'" data-rule="'+esc(r.id)+'" role="button" '
      + 'tabindex="0" title="点击只显示命中该规则的事件，再点一次取消">'
      + '<div class="rule-hd">'
      + '<span class="rule-name">'+r.name+'</span>'
      + '<span class="tag '+st.c+' sm">'+st.t+'</span>'
      + (r.risk?'<span class="tag purple sm">一致性风险</span>':'')
      + '<span class="tag gray sm" style="margin-left:auto;font-family:var(--mono)">'+r.id+'</span>'
      + '</div>'
      + '<div class="rule-desc">阈值：'+r.th+'</div>'
      + (r.note?'<div class="rule-desc" style="color:var(--warn-soft)">'+r.note+'</div>':'')
      + '<div class="rule-meta">'
      + '<span>命中 <b style="color:'+(r.hits?'var(--bad-11)':'var(--text)')+'">'+r.hits+'</b></span>'
      + '<span>影响积分 <b style="color:var(--warn-soft)">'+fmt0(r.waste)+'</b></span>'
      + '<span style="color:var(--text3)">止损措施：'
        + (RULE_MEASURE[r.id]||['taskCfg','notify']).map(k=>{
            const m = MEASURES.find(x=>x.k===k); return m ? m.t : k; }).join('、')+'</span>'
      + '</div></div>';
  });
  rules += '</div>';

  /* 副标题是 nowrap + 省略号：字数要卡在这个卡片宽度内，否则会被截掉半句 */
  h += cardHTML('审计引擎',
    AF.rule ? ('已选 '+AF.rule+' · 再点一次取消筛选') : '点任一规则，右侧只留命中它的事件',
    rules);

  /* ---------- 右侧：事件列表（与左侧引擎同高，每页 10 行） ---------- */
  let right = '<div class="card anom-card"><div class="card-hd"><div class="card-title">异常事件列表</div>'
    + '<div class="card-sub">'+(AF.rule ? ('按规则 '+esc(AF.rule)+' 筛选 · ') : '')+'点任意一行进入处置</div></div>'
    + '<div class="flex anom-chips">'
    + (AF.rule ? '<button class="chip on" data-rule-clear="1">规则 '+esc(AF.rule)+' ✕</button>' : '')
    + chips.map(([k,t])=>'<button class="chip'+(AF.dst===k?' on':'')+'" data-dst="'+k+'">'+t+'</button>').join('')
    + '</div>'
    + '<div class="tbl-wrap anom-rank"><table class="tbl dense"><thead><tr>'
    + '<th style="width:34%">任务</th><th style="width:42px">严重</th><th>命中规则</th>'
    + '<th style="text-align:right;width:98px" title="左：止损空间；右：该任务总积分">止损空间</th>'
    + '<th style="width:74px">处置状态</th>'
    + '</tr></thead><tbody>'
    + (shown.length ? shown.map(t=>{
        const st = ANOM_ST[dispStatus(t)] || ANOM_ST.open;
        return '<tr data-disp="'+t.id+'" class="hl">'
          + '<td class="name" title="'+esc(t.name)+'">'+esc(t.name)+'</td>'
          + '<td><span class="lvl">S'+evSeverity(t)+'</span></td>'
          + '<td>'+Array.from(new Set(t.flags)).map(f=>
              '<span class="tag red sm">'+esc(f)+'</span>').join(' ')+'</td>'
          + '<td class="num" title="止损空间 '+fmt1(t.waste)+' / 总积分 '+fmt1(t.credits)+'">'
            + '<span style="color:var(--warn-soft);font-weight:600">'+fmt1(t.waste)+'</span>'
            + '<span class="sub-num"> / '+fmt1(t.credits)+'</span></td>'
          + '<td><span class="tag '+st.cls+' sm">'+st.t+'</span></td>'
          + '</tr>';
      }).join('') : '<tr><td colspan="5" style="color:var(--text3);text-align:center;padding:18px">'
          + (AF.rule ? '该规则在当前口径内没有命中事件（再点一次规则可取消筛选）'
            : AF.dst!=='all' ? '该处置状态下暂无事件（切回「全部」看完整列表）'
            : scope.length!==TASKS.length ? '当前筛选口径内未命中异常规则（放宽筛选可看全量）'
                                          : '窗口内未命中异常规则') + '</td></tr>')
    + '</tbody></table></div>'
    + (shownAll.length > PG_SIZE ? pagerHTML(page, 'anp')
        : '<div class="anom-count">共 '+shownAll.length+' 条 · 全部显示</div>')
    /* 补偿预留收进卡片脚：它是一句「为什么现在不做」的说明，
       不值得单独占一张卡（占了就会把事件列表挤到比左侧引擎矮） */
    + '<div class="rsv-card rsv-line"><span class="tag gray sm">预留</span>'
      + '<b>补偿申请</b>'
      + '<em>厂商未开放积分补偿接口，该功能未启用；证据链已完整留存，'
      + '条件满足时可直接作申请材料</em>'
      + '<button class="btn sm" id="rsvInfo">了解详情</button></div>'
    + '</div>';          /* 关掉事件列表卡 */

  h += '<div class="anom-right">'+right+'</div></div>';

  /* ---------- 动态建议（口径改为止损） ---------- */
  const topRule = RULES.filter(r=>r.waste>0).sort((a,b)=>b.waste-a.waste)[0];
  let tip = '';
  if (topRule){
    const topTasks = scope.filter(t=>(t.flags||[]).length>0 && t.waste>0
      && (t.ruleIds||[]).includes(topRule.id));
    const lvCount = {};
    topTasks.forEach(t=>{ lvCount[t.level] = (lvCount[t.level]||0)+1; });
    const mainLv = Object.keys(lvCount).sort((a,b)=>lvCount[b]-lvCount[a])[0] || '—';
    tip = '<b>止损建议：</b>'+esc(topRule.name)+'（'+topRule.id+'）影响最大，涉及 <b>'+fmt0(topRule.waste)
      + '</b> 积分（占窗口 '+pct(topRule.waste/totalCr)+'），集中在 <b>'+esc(mainLv)+'</b> 任务（'
      + topTasks.length+' 个）。'+esc(topRule.advice)
      + '这部分积分<b>已经消耗、不可追回</b>；把该规则的止损措施落到任务配置上，'
      + '拦住的是<b>下一批</b>同类型消耗。';
  } else {
    tip = '<b>止损建议：</b>窗口内没有规则在报警，消耗结构健康；可把阈值调严一档做压力测试。';
  }
  if (S.confirmRate < 0.8 && S.detected){
    tip += ' 当前异常确认率 '+pct(S.confirmRate)+'（目标 ≥80%），先补上确认与归因，'
         + '再谈止损覆盖率，指标才有意义。';
  }
  if (unsupported.length){
    tip += ' '+unsupported.map(r=>r.name).join('、')+'因数据源缺少字段无法判定（'+unsupported[0].th
      + '），报告如实标注为「数据源不支持」而不是 0 命中。';
  }
  h += '<div class="hint mt">'+tip+'</div>';
  return h;
}

/* ================= 异常处置：证据 / 归因 / 止损 / 留痕 ================= */
function openDispose(taskId){
  const t = TASKS.find(x=>x.id===taskId);
  if (!t) return;
  const m = document.getElementById('cfgModal');
  const hd = m.querySelector('.modal-hd h3'), sub = m.querySelector('.modal-hd .sub');
  if (hd) hd.textContent = '异常处置 · ' + (t.name || t.id);
  if (sub) sub.innerHTML = '事件 <b class="mono">'+esc(t.id)+'</b> · 流水号 '
    + (t.txn ? '<b class="mono">'+esc(t.txn)+'</b>' : '未登记')
    + ' · 处置以止损与归因为主，<b>不产生积分退回</b>';
  document.getElementById('cfgBody').innerHTML = disposeHTML(t);
  drawDisposeFoot(t);
  document.getElementById('cfgMask').classList.add('on');
  m.classList.add('on');
  const body = document.getElementById('cfgBody');
  body.__dbound = false;
  bindDispose(t);
}

function disposeHTML(t){
  const d = dispOf(t) || {};
  const rules = (t.ruleIds || t.flags || []);
  const sug = suggestMeasures(t);
  const chosen = d.measures || sug;          /* 新事件默认勾上该规则的推荐措施 */
  const stepsN = (t.steps||[]).length;
  const perStep = stepsN ? (t.credits/stepsN) : 0;
  const winDays = (RAW.meta && (RAW.meta.windowDays || RAW.meta.days)) || 0;
  const avg7 = (RAW.budget && RAW.budget.dailyAvg7) || 0;

  /* ---- 证据快照 ---- */
  let h = '<div class="sec-t">证据快照</div>'
    + '<div class="ev-grid">'
    + evCell('本任务积分', fmt1(t.credits)+' 积分', '')
    + evCell('止损空间估算', fmt1(t.waste)+' 积分', '按规则口径，已消耗')
    + evCell('任务步长', fmt0(stepsN)+' 步', '')
    + evCell('单步均积分', fmt1(perStep)+' 积分', '')
    + evCell('端到端时长', fmt0(t.duration)+'s', '')
    + evCell('工具失败次数', fmt0(t.retry||0)+' 次', '')
    + evCell('输入 / 输出 token', fmt(t.inTok||0)+' / '+fmt(t.outTok||0), '')
    + evCell('窗口日均消耗', fmt1(avg7)+' 积分', winDays ? ('近 '+winDays+' 日') : '')
    + '</div>'
    + '<div class="cfg-note" style="margin:0 0 4px">快照取自本机采集结果，'
    + '与 <code>'+esc(t.id)+'</code> 的逐步明细一致；导出证据链时一并打包。</div>';

  /* ---- 归因树 ---- */
  h += '<div class="sec-t" style="margin-top:14px">归因树</div><div class="tree">'
    + '<div class="tree-n root"><b>'+esc(rules.length?rules.join(' + '):'未命中规则')+'</b>'
      + '<span>'+esc(t.name||'')+'</span></div>';
  const causes = [
    {k:'cfg',  n:'任务配置不当（轮次过多 / 未设超时 / 未拆分）',
               why:'步数 '+stepsN+'、单步均 '+fmt1(perStep)+' 积分'},
    {k:'ctx',  n:'上下文膨胀（长会话未压缩）',
               why:'输入 token '+fmt(t.inTok||0)+'，占本任务 token ' + pct((t.inTok||0)/((t.inTok||0)+(t.outTok||0)||1))},
    {k:'tool', n:'工具失败重试导致无效消耗',
               why:'失败 '+fmt0(t.retry||0)+' 次'},
    {k:'model',n:'模型选型偏贵 / 场景错配',
               why:'主模型 '+(t.mainChannel||'—')},
    {k:'trig', n:'触发方式导致的隐性消耗',
               why:(t.trigger||'—')+' 触发'+(t.project?(' · '+t.project):'')}
  ];
  const hit = (d.causes || sug.map(m2=>m2==='modelLimit'?'model':m2==='taskCfg'?'cfg':m2==='pauseBg'?'trig':'tool'));
  causes.forEach(c=>{
    const on = (hit||[]).indexOf(c.k) >= 0;
    h += '<div class="tree-n'+(on?' bad':'')+'"><span class="tree-i">'+(on?'●':'○')+'</span>'
      + '<b>'+esc(c.n)+'</b><span>'+esc(c.why)+'</span>'
      + '<span class="tag '+(on?'red':'green')+' sm">'+(on?'候选根因':'暂未命中')+'</span></div>';
  });
  h += '</div>';

  /* ---- 止损措施（多选） ---- */
  h += '<div class="sec-t" style="margin-top:14px">止损措施（多选）</div>'
    + '<div class="cfg-note" style="margin:0 0 7px">措施作用于<b>任务配置、模型限制、冻结额度、通知</b>，'
    + '<b>不修改原流水、不改余额、不产生任何积分退回</b>（v2.1 X4）。</div>'
    + '<div class="ms-grid">'
    + MEASURES.map(m2=>{
        const on = chosen.indexOf(m2.k) >= 0;
        return '<label class="ms-card'+(on?' on':'')+'">'
          + '<input type="checkbox" data-ms="'+m2.k+'"'+(on?' checked':'')+'>'
          + '<b>'+m2.t+'</b><span>'+esc(m2.d)+'</span><i>'+esc(m2.arg)+'</i></label>';
      }).join('')
    + '</div>'
    + '<div class="cfg-row" style="margin-top:8px"><div class="cfg-k">备注</div><div class="cfg-v">'
      + '<input class="cfg-txt" id="dsNote" type="text" value="'+esc(d.note||'')+'" '
      + 'placeholder="本次处置的依据 / 验证方式"></div></div>';

  /* ---- 处置记录 ---- */
  const log = dispLog(t);
  h += '<div class="sec-t" style="margin-top:14px">处置记录</div>'
    + (log.length ? '<div class="tm">'+log.map(l=>
        '<div class="tm-i"><span class="tm-t">'+esc(l.at||'')+'</span>'
        + '<span class="tm-a">'+esc(l.act||'')+'</span>'
        + '<span class="tm-r">'+esc(l.res||'')+'</span></div>').join('')+'</div>'
      : '<div class="cfg-note">还没有处置记录。确认异常后每次动作都会留痕，'
        + '并可随证据链一起导出。</div>');
  return h;
}
function evCell(k, v, s){
  return '<div class="ev-c"><span>'+esc(k)+'</span><b>'+v+'</b>'
    + (s?'<i>'+esc(s)+'</i>':'')+'</div>';
}
function drawDisposeFoot(t){
  const st = dispStatus(t);
  const foot = document.getElementById('cfgFoot');
  if (!foot) return;
  foot.innerHTML = '<span class="tag '+ANOM_ST[st].cls+' sm">'+ANOM_ST[st].t+'</span>'
    + '<button class="btn" id="dsExport">导出证据链</button>'
    + '<span class="ml-auto" style="color:var(--text3);font-size:10.5px">'
      + '处置只做止损，不退回积分</span>'
    + (st==='open' ? '<button class="btn pri" id="dsConfirm">确认异常</button>' : '')
    + (['confirmed','mitigating'].indexOf(st)>=0
        ? '<button class="btn pri" id="dsMitigate">执行止损</button>' : '')
    + (['confirmed','mitigated','investigating'].indexOf(st)>=0
        ? '<button class="btn" id="dsClose">关闭事件</button>' : '')
    + (st!=='closed' && st!=='ignored' ? '<button class="btn" id="dsIgnore">忽略</button>' : '');
  if (!foot.__dbound){
    foot.__dbound = true;
    foot.addEventListener('click', e=>{
      const t2 = TASKS.find(x=>x.id===foot.dataset.tid);
      if (!t2) return;
      const id = e.target.id;
      if (id === 'dsConfirm') return actConfirm(t2);
      if (id === 'dsMitigate') return actMitigate(t2);
      if (id === 'dsClose') return actClose(t2);
      if (id === 'dsIgnore') return actIgnore(t2);
      if (id === 'dsExport') return exportEvidence(t2);
    });
  }
  foot.dataset.tid = t.id;
}

/* 处置动作之后要重画哪一面？看路由——回收调整与对账台簿共用这套视图，
   硬编码成 renderLedgerAnomaly() 会在回收调整页上什么都没变。 */
function afterDispose(){
  if (typeof RT !== 'undefined' && RT.page === 'control' && RT.sub === 'recycle'
      && typeof renderRecycle === 'function'){ renderRecycle(); return; }
  if (typeof renderLedgerAnomaly === 'function') renderLedgerAnomaly();
}

function pushLog(t, act, res){
  const o = dispLoad();
  const rec = o[t.id] || (o[t.id] = {status:'open', measures:[], log:[]});
  rec.log = rec.log || [];
  rec.log.push({at:nowStamp(), act:act, res:res || '', who:'本机审计员'});
  DISP = o; dispSave();
}
function actConfirm(t){
  const o = dispLoad();
  const rec = o[t.id] || (o[t.id] = {measures:[], log:[]});
  const note = (document.getElementById('dsNote')||{}).value || '';
  rec.status = 'confirmed';
  rec.measures = msChecked(); rec.note = note;
  DISP = o; dispSave();
  const why = (rec.measures || []).length
    ? rec.measures.map(k=>(MEASURES.find(x=>x.k===k)||{}).t).join('、')
    : '待补充';
  pushLog(t, '确认异常', '归因：' + why + (note ? ' · ' + note : ''));
  toast('已确认异常：'+t.name);
  openDispose(t.id); afterDispose();
}
function actMitigate(t){
  const ms = msChecked();
  if (!ms.length){ toast('至少选一项止损措施'); return; }
  const o = dispLoad();
  const rec = o[t.id] || (o[t.id] = {log:[]});
  rec.status = 'mitigated';
  rec.measures = ms;
  rec.note = (document.getElementById('dsNote')||{}).value || '';
  DISP = o; dispSave();
  pushLog(t, '执行止损', ms.map(k=>(MEASURES.find(x=>x.k===k)||{}).t).join('、') + '（待验证）');
  toast('已记录止损措施 '+ms.length+' 项');
  openDispose(t.id); afterDispose();
}
function actClose(t){
  const res = window.prompt('关闭结论（已止损 / 误报 / 无法止损但已记录）：', '已止损');
  if (res === null) return;
  const o = dispLoad();
  const rec = o[t.id] || (o[t.id] = {measures:[], log:[]});
  rec.status = 'closed'; rec.conclusion = res || '已记录';
  rec.measures = msChecked();
  DISP = o; dispSave();
  pushLog(t, '关闭事件', '结论：' + rec.conclusion);
  toast('事件已关闭');
  openDispose(t.id); afterDispose();
}
function actIgnore(t){
  const why = window.prompt('忽略原因（需备注，便于事后审计）：', '');
  if (why === null) return;
  const o = dispLoad();
  const rec = o[t.id] || (o[t.id] = {measures:[], log:[]});
  rec.status = 'ignored'; rec.conclusion = '误报'; rec.note = why;
  DISP = o; dispSave();
  pushLog(t, '忽略事件', why || '未填写说明');
  toast('已标为忽略');
  openDispose(t.id); afterDispose();
}
function msChecked(){
  return Array.from(document.querySelectorAll('#cfgBody input[data-ms]'))
    .filter(i=>i.checked).map(i=>i.dataset.ms);
}
/* 导出证据链：证据快照 + 归因树 + 处置记录 + 关联流水 + 逐步明细 */
function exportEvidence(t){
  const d = dispOf(t) || {};
  const steps = (t.steps||[]).map(s=>({
    ts:s.ts||'', phase:s.phase||'', kind:s.kind||'', model:s.model||'', tool:s.tool||'',
    credits:s.credits||0, ms:s.ms||0, inTok:s.inTok||0, outTok:s.outTok||0
  }));
  const pack = {
    schema:'wbc.anomaly.evidence.v1',
    note:'任务执行已产生真实资源消耗，积分扣减不可追回；本证据包用于止损复盘与未来可能的补偿申请',
    exportedAt:nowStamp(),
    event:{id:t.id, name:t.name, taskId:t.id, txn:t.txn||'', rules:t.ruleIds||t.flags||[],
           severity:evSeverity(t), status:d.status||'open', conclusion:d.conclusion||''},
    snapshot:{credits:t.credits, avoidable:t.waste, steps:(t.steps||[]).length,
              duration:t.duration, retry:t.retry||0, inTok:t.inTok||0, outTok:t.outTok||0,
              trigger:t.trigger, project:t.project, mainChannel:t.mainChannel, ts:t.ts},
    measures:(d.measures||[]).map(k=>(MEASURES.find(x=>x.k===k)||{}).t),
    dispositionLog:d.log||[],
    stepCount:steps.length,
    steps:steps.slice(0, 400),
    compensation:{reserved:true, enabled:false,
      trigger:['厂商开放补偿 API','国家或行业发布补偿标准','厂商书面承诺补偿']}
  };
  try {
    const blob = new Blob([JSON.stringify(pack, null, 2)], {type:'application/json'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'anomaly-evidence-' + t.id + '.json';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(()=>{ try{ URL.revokeObjectURL(a.href); }catch(e){} }, 1500);
    toast('证据链已导出（含 '+steps.length+' 步明细）');
  } catch(e){ toast('导出失败，请检查浏览器下载权限'); }
}

/* 补偿申请预留：只说清楚为什么没启用、什么时候能启用 */
function openCompensateInfo(){
  const m = document.getElementById('cfgModal');
  const hd = m.querySelector('.modal-hd h3'), sub = m.querySelector('.modal-hd .sub');
  if (hd) hd.textContent = '补偿申请 · 预留未启用';
  if (sub) sub.textContent = 'v2.1 已删除全部积分回收能力，此处仅记录未来启用的条件';
  document.getElementById('cfgBody').innerHTML =
      '<div class="cfg-note" style="margin:0 0 10px">'
    + '<b>为什么现在没有“申请补偿”？</b><br/>'
    + '任务一旦执行，模型调用与资源消耗已经发生，钱包侧扣减即为计费事实。'
    + '要求退回等于要求厂商补偿 —— 当前既没有公开的补偿接口，也没有国家或行业层面的'
    + '“任务失败补偿”标准，厂商亦无书面承诺。提供一个点了也不生效的按钮，'
    + '会让业务方误以为异常消耗可以追回，因此本版不做。</div>'
    + '<div class="sec-t">启用条件（满足其一，且内部前提达成）</div>'
    + '<div class="cfg-note" style="margin:0 0 10px">'
    + '① 厂商开放积分补偿 API（如 POST /compensation/claim）；<br/>'
    + '② 国家或行业发布任务失败补偿的政策标准 / 技术规范；<br/>'
    + '③ 厂商以书面形式承诺对特定异常场景给予补偿。<br/>'
    + '内部前提：对账稳定运行 ≥3 个月、证据链完整率 ≥95%、'
    + '且有明确的补偿金额核定规则（按步骤 / 按任务 / 按比例）。</div>'
    + '<div class="sec-t">现在能做什么</div>'
    + '<div class="cfg-note" style="margin:0">'
    + '每条异常事件的证据快照、归因树与处置记录都已留痕，可用「导出证据链」把'
    + '步骤级积分 / 耗时 / token 一起打包。条件成熟时，这份材料就是申请底稿。</div>';
  document.getElementById('cfgFoot').innerHTML =
      '<span class="ml-auto"></span><button class="btn pri" id="rsvClose">知道了</button>';
  document.getElementById('rsvClose').onclick = ()=>{
    m.classList.remove('on'); document.getElementById('cfgMask').classList.remove('on');
  };
  m.classList.add('on');
  document.getElementById('cfgMask').classList.add('on');
}

/* 页面级委托：审计引擎选规则、状态胶囊、翻页、事件行下钻、预留卡「了解详情」 */
document.addEventListener('click', e=>{
  /* 规则卡与「清除」胶囊都要重画：任何筛选变化都把页码拉回第 1 页，
     否则在第 3 页点一条只命中 2 条事件的规则，会看到一个空列表。 */
  const clr = e.target.closest('[data-rule-clear]');
  if (clr){ AF.rule = ''; AF.page = 1; afterDispose(); return; }
  const rule = e.target.closest('[data-rule]');
  if (rule){
    const id = rule.getAttribute('data-rule');
    AF.rule = (AF.rule === id) ? '' : id;
    AF.page = 1;
    afterDispose();
    return;
  }
  const chip = e.target.closest('[data-dst]');
  if (chip){
    AF.dst = chip.getAttribute('data-dst');
    AF.page = 1;
    afterDispose();
    return;
  }
  const pg = e.target.closest('[data-anp]');
  if (pg){
    if (pg.disabled) return;
    AF.page = Math.max(1, +pg.getAttribute('data-anp'));
    afterDispose();
    return;
  }
  const tr = e.target.closest('tr[data-disp]');
  if (tr){ openDispose(tr.getAttribute('data-disp')); return; }
  if (e.target.id === 'rsvInfo'){ openCompensateInfo(); }
});

function bindDispose(t){
  const body = document.getElementById('cfgBody');
  if (!body || body.__dbound) return;
  body.__dbound = true;
  body.addEventListener('click', e=>{
    const lab = e.target.closest('label.ms-card');
    if (lab){ setTimeout(()=>lab.classList.toggle('on', lab.querySelector('input').checked), 0); }
  });
}
