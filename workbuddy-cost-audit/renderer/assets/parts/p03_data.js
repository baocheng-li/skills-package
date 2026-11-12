/* ============================================================
   数据适配层
   ------------------------------------------------------------
   输入：window.WBC_DATA —— 由 etl/collect.py 从本机真实数据生成
         （日志 credit consumed / qua.end + workbuddy.db + 会话记录）
   输出：各视图使用的聚合变量，结构与原型阶段保持一致。

   原则：
     1) 不造数：所有数值来自采集结果，缺样本的地方标 null/0 并由视图提示；
     2) 口径可解释：积分优先取客户端上报值，缺失时按实测折算率补算并标记 est；
     3) 时间轴、窗口长度、模型清单均来自数据本身。
   ============================================================ */

const RAW = (typeof window !== 'undefined' && window.WBC_DATA) || {
  meta:{windowDays:30, counts:{}, coverage:{}, span:['','']},
  models:[], tasks:[], daily:[], rules:[], automations:[], bench:[], anomalies:[]
};

const fmt = n => n>=1e6 ? (n/1e6).toFixed(2)+'M' : n>=1e4 ? (n/1e3).toFixed(1)+'k' : Math.round(n).toLocaleString('en-US');
const fmt0 = n => Math.round(n).toLocaleString('en-US');
const fmt1 = n => (Math.round(n*10)/10).toFixed(1);
const pct = n => (n*100).toFixed(1)+'%';
const pct1 = n => (Math.round(n*1000)/10).toFixed(1)+'%';

const META = RAW.meta;
const DAYS = META.windowDays || 30;

/* ---------- 模型与折算率 ---------- */
const TIER_TAG = {'高效':'cyan','均衡':'blue','旗舰':'amber','轻量':'gray'};
const MODELS = (RAW.models || []).map((m,i)=>({
  id: m.id,
  name: m.name || m.id,
  short: m.short || m.id,
  vendor: m.vendor || '—',
  tier: m.tier || '—',
  color: m.color || 'var(--text2)',
  tag: TIER_TAG[m.tier] || 'gray',
  ver: '',
  ratePerKTok: m.ratePerKTok || 0,
  calls: m.calls || 0,
  tasks: m.tasks || 0,
  inTok: m.inTok || 0,
  outTok: m.outTok || 0,
  credits: m.credits || 0
}));
const M = {}; MODELS.forEach(m=>M[m.id]=m);
const modelColor = id => (M[id]||{}).color || 'var(--text2)';
const modelName = id => (M[id]||{}).name || id;
// 混合折算：积分 = 总 token / 1000 × 实测每千 token 积分
const calcCredits = (mid,i,o) => {
  const m = M[mid];
  if (!m) return (i+o)/1000 * (META.rates ? META.rates.fallback : 0.1);
  return (i+o)/1000 * m.ratePerKTok;
};

/* ---------- 任务台账 ---------- */
const DAY_OFFSET = {};
(RAW.daily||[]).forEach(r=>{ DAY_OFFSET[r.date] = r.d; });

function normTs(ts){
  if (!ts) return null;
  return String(ts).replace('T',' ').slice(0,19);
}

/* build.py 会以数组编码压缩步骤，这里还原为对象 */
const DICT = RAW.dict || {models:[], tools:[], phases:[]};
const PHASE_FALLBACK = {0:'推理', 1:'工具调用'};
function decodeStep(a){
  const isTool = a[1] === 1;
  return {
    i: a[0],
    kind: isTool ? 'tool' : 'model',
    model: a[2] >= 0 ? DICT.models[a[2]] : null,
    tool: a[3] >= 0 ? DICT.tools[a[3]] : null,
    inTok: a[4], outTok: a[5], credits: a[6],
    ms: a[7], retry: a[8],
    ts: a[9] || null,
    phase: a[10] >= 0 ? DICT.phases[a[10]] : PHASE_FALLBACK[a[1]] || '推理',
    err: a[11] === 1,
    est: a[12] === 1
  };
}
const normStep = s => Array.isArray(s) ? decodeStep(s) : Object.assign({}, s, {ts: normTs(s.ts)});

const TASKS = (RAW.tasks||[]).map(t=>{
  const steps = (t.steps||[]).map(normStep);
  return Object.assign({}, t, {
    steps: steps,
    day: DAY_OFFSET[t.day] !== undefined ? DAY_OFFSET[t.day] : 0,
    date: t.day,
    ts: normTs(t.ts),
    creditsRaw: t.credits,
    duration: Math.round(t.duration || 0)
  });
}).sort((a,b)=> (b.ts||'').localeCompare(a.ts||''));

const totalIn = TASKS.reduce((s,t)=>s+(t.inTok||0),0);
const TOTAL_CREDITS = Math.round(TASKS.reduce((s,t)=>s+t.credits,0));
const TOTAL_IN = totalIn;
const TOTAL_OUT = TASKS.reduce((s,t)=>s+(t.outTok||0),0);
const TOTAL_TOKENS = TOTAL_IN + TOTAL_OUT;

/* ---------- 日聚合 ---------- */
const BY_DAY = (RAW.daily||[]).map(r=>Object.assign({
  人工:0, 定时:0, 后台自动化:0, credits:0, tasks:0, tokens:0
}, r));
const TODAY = BY_DAY[BY_DAY.length-1] || {credits:0,tasks:0,tokens:0,人工:0,定时:0,后台自动化:0,label:'—'};
const YESTERDAY = BY_DAY[BY_DAY.length-2] || TODAY;
const ACTIVE_DAYS = BY_DAY.filter(d=>d.tasks>0).length;
const DAY_AVG = ACTIVE_DAYS ? TOTAL_CREDITS / ACTIVE_DAYS : 0;
const PEAK_DAY = BY_DAY.reduce((a,b)=> b.credits>(a.credits||0) ? b : a, BY_DAY[0]||{credits:0});

/* ---------- 异常与可优化空间 ---------- */
const ANOMALY_TASKS = TASKS.filter(t=>(t.flags||[]).length>0 && t.waste>0)
  .sort((a,b)=>b.waste-a.waste);
const WASTE_TOTAL = Math.round(ANOMALY_TASKS.reduce((s,t)=>s+t.waste,0));
const CRON_TASKS = TASKS.filter(t=>t.trigger!=='人工');

const RULES = (RAW.rules||[]).map(r=>Object.assign({on:true}, r));

/* ---------- 自动化（定时/后台）---------- */
const AUTOS = (RAW.automations||[]).map(a=>({
  name: a.name, freq: a.freq || '—', level: a.level || 'L4',
  runs: a.runs || 0, avg: a.avg || 0, daily: a.daily || 0,
  prev: a.prev || 0, model: a.model || 'unknown',
  owner: a.owner || '本机', status: a.status || '正常',
  days: a.days || 1,
  weekly: a.weekly || 0, monthly: a.monthly || 0,
  succ: (a.succ===null||a.succ===undefined) ? null : a.succ,
  mark: a.mark || '正常', reason: a.reason || ''
}));
const AUTO_DAY_AVG = AUTOS.reduce((s,a)=>s+a.daily, 0);
const AUTO_SHARE = TOTAL_CREDITS ? AUTO_DAY_AVG*DAYS / TOTAL_CREDITS : 0;

/* ---------- 模型 × 复杂度 观测数据 ---------- */
const BENCH_ROWS = (RAW.bench||[]).map(r=>Object.assign({}, r, {q:null}));

const LEVELS = ['L1','L2','L3','L4'];
const LEVEL_LABEL = {L1:'轻量 ≤3 步', L2:'标准 4—8 步', L3:'复杂 9—15 步', L4:'重型 >15 步'};
const MIN_SAMPLE = 3;   // 单格样本低于该值不参与结论

const SCEN_META = {};
LEVELS.forEach(lv=>{
  const rows = BENCH_ROWS.filter(r=>r.lvl===lv);
  const n = rows.reduce((s,r)=>s+r.n,0);
  const types = {};
  TASKS.filter(t=>t.level===lv).forEach(t=>{ types[t.type]=(types[t.type]||0)+1; });
  const top = Object.keys(types).sort((a,b)=>types[b]-types[a]).slice(0,2);
  SCEN_META[lv] = {
    label: top.length ? top.join(' / ') : '暂无样本',
    tpl: '本机真实样本',
    ver: n ? ('n=' + n) : 'n=0',
    samples: n
  };
});

/* 边际成本效率：以该层级最低积分模型为基线，衡量“多花积分换来的成功率增益” */
const BENCH = {};
LEVELS.forEach(lv=>{
  const rows = BENCH_ROWS.filter(r=>r.lvl===lv).sort((a,b)=>a.credits-b.credits);
  if (!rows.length){ BENCH[lv] = {rows:[], base:null}; return; }
  const base = rows[0];
  rows.forEach(r=>{
    r.dc = Math.round((r.credits - base.credits)*10)/10;
    r.dsucc = Math.round((r.succ - base.succ)*10)/10;
    r.mce = r.dc > 0 ? Math.round(r.dsucc/r.dc*100)/100 : null;
    r.enough = r.n >= MIN_SAMPLE;
  });
  BENCH[lv] = {rows:rows, base:base};
});

/* ---------- 分级路由建议（由观测数据推导，样本不足则明确标注）---------- */
function bestAt(lv){
  const rows = (BENCH[lv]||{}).rows || [];
  const ok = rows.filter(r=>r.enough);
  if (ok.length < 2) return null;
  const topSucc = Math.max.apply(null, ok.map(r=>r.succ));
  const cand = ok.filter(r=>r.succ >= topSucc - 3)   // 质量差 ≤3pp 才视为可比
                  .sort((a,b)=>a.credits-b.credits);
  if (!cand.length) return null;
  const pickRow = cand[0];
  const costly = ok.reduce((a,b)=> b.credits>a.credits ? b : a, ok[0]);
  return {pick:pickRow, costly:costly,
          save: costly.credits>0 ? 1 - pickRow.credits/costly.credits : 0,
          sample: ok.reduce((s,r)=>s+r.n,0)};
}

const ROUTING = LEVELS.map((lv,i)=>{
  const colors = {L1:'var(--ok-10)', L2:'var(--info-9)', L3:'var(--warn-9)', L4:'var(--bad-9)'};
  const cnt = TASKS.filter(t=>t.level===lv).length;
  const share = TASKS.length ? cnt/TASKS.length : 0;
  const b = bestAt(lv);
  const meta = SCEN_META[lv];
  if (!b){
    return {lvl:lv, title:LEVEL_LABEL[lv], color:colors[lv],
      body:'本层级可比样本不足（需 ≥'+MIN_SAMPLE+' 个任务 × 至少 2 个模型），暂不给出换模型结论。建议先在同类任务上补采 '+MIN_SAMPLE+'—5 轮。',
      save:'待补样本', note:'覆盖 '+pct1(share)+' 任务量 · n='+meta.samples, pending:true};
  }
  return {lvl:lv, title:LEVEL_LABEL[lv], color:colors[lv],
    body:'<em>'+b.pick.model+'</em> 在该层级以 '+fmt0(b.pick.credits)+' 积分/任务达成 '+b.pick.succ+'% 成功率，'
       +'对比 <em>'+b.costly.model+'</em>（'+fmt0(b.costly.credits)+' 积分 / '+b.costly.succ+'%）成本低 '
       +pct1(b.save)+'，成功率差 '+Math.abs(Math.round((b.costly.succ-b.pick.succ)*10)/10)+'pp，在 3pp 阈值内。',
    save:'-'+pct1(b.save), note:'覆盖 '+pct1(share)+' 任务量 · n='+b.sample};
});

const PHASE_COLOR = {'规划':'var(--iris-11)','推理':'var(--info-9)','工具调用':'var(--ok-11)','生成':'var(--warn-9)','校验/重试':'var(--bad-9)'};

/* ---------- 口径与覆盖度（用于页面脚注）---------- */
const COVERAGE = META.coverage || {creditAccuracy:0, tokenCoverage:0};
const COUNTS = META.counts || {};

/* ---------- 积分监测（余额全部人工设置，不做自动抓取）---------- */
const BUDGET = Object.assign({
  source:'none', sourceLabel:'未设置', configured:false,
  initial:null, initialAt:null,
  todayBalance:null, todayBalanceDate:null, todayBalanceAt:null, todayBalanceStale:false,
  today:null, todaySpent:0, balance:null, balanceAt:null, consumePct:null,
  spendRatio:null, spendRatioOver:false,
  level:null, levelText:'未配置', exitCode:0, reason:'',
  thresholds:{mode:'pct', warn:10, crit:20, red:30},
  alerts:[], windowSpent:0, p95Task:0, dailyAvg7:0,
  reconcile:{available:false}, modelSwitch:{count:0,items:[],tasks:[]},
  rate:{today:0,d3Avg:0,d7Avg:0,prev7Avg:0,alert:false,note:''}, series:[]
}, RAW.budget || {});
const BUDGET_COLOR = {red:'var(--bad-9)', crit:'var(--warn-soft)', warn:'var(--warn-9)', none:'var(--ok-10)'};

/* ---------- 积分对账（T 日流水 / 任务支出明细 / 日终 T+1 对账） ----------
   后端 hosts/commands/recon_daily.py 已经算好逐日结转链，前端只做展示与筛选：
     days[] 每天：日收 / 上一日日结余额 / 日初 / 日耗 / 日结 / 账户余额 / 偏差 / 判定
     flow[] 每笔：16 位流水号 / 收支方向 / 任务ID / 积分 / 累计耗时 */
const CRED = Object.assign({
  available:false, anchor:'', initial:null, initialDate:null, seeded:false,
  tolerance:5, toleranceAbs:0.5, now:'',
  t1:{date:'', runAt:'', status:'pending', statusText:'待 T+1 对账', note:''},
  days:[], flow:[], totals:{}, carryPolicy:'', txnAssigned:0
}, RAW.creditRecon || {});
/* 报告生成时的原始值快照：页内改动只叠加在这份之上，recalcBudget() 每次先整体回滚 */
const BUDGET_BASE = JSON.parse(JSON.stringify(RAW.budget || {}));

/* 页内人工配置：内存为第一真源（一定生效），localStorage 只做尽力持久化。
   file:// 打开报告时部分浏览器会禁用 localStorage，不能让持久化失败拖累生效。 */
const BAL_KEY = 'wbc.balance.v2';
let BAL_CFG = null;          // {initial, todayBalance, thresholds, balanceDate}
let BAL_PERSISTED = true;    // 上一次写入 localStorage 是否成功

function loadLocalBalance(){
  if (BAL_CFG) return BAL_CFG;
  try {
    const raw = localStorage.getItem(BAL_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch(e){ BAL_PERSISTED = false; return null; }
}

/* 返回 true 表示已持久化到浏览器，false 表示仅本次会话生效 */
function saveLocalBalance(initial, todayBalance, thresholds, balanceDate){
  BAL_CFG = {initial:initial, todayBalance:todayBalance,
             thresholds:thresholds, balanceDate:balanceDate || null};
  try {
    localStorage.setItem(BAL_KEY, JSON.stringify(BAL_CFG));
    BAL_PERSISTED = true;
  } catch(e){ BAL_PERSISTED = false; }
  return BAL_PERSISTED;
}

/* 阈值单调修正：warn ≤ crit ≤ red（与后端 balance_state.thresholds_of 同口径） */
function normThresholds(th){
  const o = Object.assign({mode:'pct', warn:10, crit:20, red:30}, th || {});
  const num = v => (v===null||v===undefined||v===''||isNaN(+v)) ? null : +v;
  let w = num(o.warn), c = num(o.crit), r = num(o.red);
  if (w === null) w = 10; if (c === null) c = 20; if (r === null) r = 30;
  w = Math.max(0, Math.min(100, w));
  c = Math.max(w, Math.min(100, c));
  r = Math.max(c, Math.min(100, r));
  return {mode:'pct', warn:w, crit:c, red:r};
}

/* 阈值被单调修正时给一句说明，避免「填 95/60/20 结果全是 95」让人困惑 */
function thFixedNotice(input, th){
  const n = v => (v==='' || v===null || v===undefined || isNaN(+v)) ? null : +v;
  const a = [n(input.warn), n(input.crit), n(input.red)];
  const b = [th.warn, th.crit, th.red];
  const bad = a.some((v,i)=> v!==null && Math.abs(v - b[i]) > 1e-9);
  return bad ? '；阈值按 黄 ≤ 橙 ≤ 红 修正为 '+b.join(' / ')+'%' : '';
}

/* 今日消耗 = 结算日（后端算好的 BUDGET.today）全部任务积分；当前余额 = 今日余额 − 今日消耗 */
function recalcBudget(){
  const t = BUDGET;
  Object.assign(t, BUDGET_BASE);          // 先回到报告原始值，再叠加页内配置
  if (t.today === null || t.today === undefined){
    const days = TASKS.map(x=>x.date).filter(Boolean).sort();
    t.today = days.length ? days[days.length-1] : '';
  }
  // 注意：TASKS.day 是「窗口内第几天」的序号，日期字符串在 TASKS.date
  t.todaySpent = Math.round(TASKS.filter(x=>x.date===t.today)
    .reduce((s,x)=>s+(x.credits||0),0) * 10) / 10;
  const local = loadLocalBalance();
  if (local){
    if (local.initial !== null && local.initial !== undefined && local.initial !== '')
      t.initial = +local.initial;
    if (local.todayBalance !== null && local.todayBalance !== undefined && local.todayBalance !== '')
      t.todayBalance = +local.todayBalance;
    if (local.thresholds) t.thresholds = normThresholds(local.thresholds);
    /* 页内改的余额一律视为「当前数据日」的余额，消除跨日过期提示 */
    if (local.balanceDate) t.todayBalanceDate = local.balanceDate;
    else if (local.todayBalance !== null && local.todayBalance !== undefined)
      t.todayBalanceDate = t.today;
    t.todayBalanceStale = false;
    t.source = 'manual'; t.sourceLabel = '人工设置';
  }
  t.thresholds = normThresholds(t.thresholds);
  t.configured = (t.initial !== null && t.initial !== undefined
                  && t.todayBalance !== null && t.todayBalance !== undefined);
  if (t.configured){
    t.balance = Math.round((t.todayBalance - t.todaySpent) * 10) / 10;
    t.balanceAt = t.todayBalanceDate || t.today;
    t.consumePct = t.initial > 0
      ? Math.round((t.initial - t.balance) / t.initial * 1000) / 10 : null;
    /* 预警比 = 今日消耗 / 当前余额；余额见底即视为超出 100% */
    if (t.balance > 0){
      t.spendRatio = Math.round(t.todaySpent / t.balance * 1000) / 10;
      t.spendRatioOver = false;
    } else if (t.todaySpent > 0){
      t.spendRatio = null; t.spendRatioOver = true;
    } else {
      t.spendRatio = 0; t.spendRatioOver = false;
    }
    const th = t.thresholds || {};
    const r = t.spendRatioOver ? Infinity : t.spendRatio;
    if (r === null || r === undefined){
      t.level = null; t.levelText = '无法判定';
    } else if (r >= (th.red||30)){
      t.level = 'red'; t.levelText = '红色告警';
    } else if (r >= (th.crit||20)){
      t.level = 'crit'; t.levelText = '橙色告警';
    } else if (r >= (th.warn||10)){
      t.level = 'warn'; t.levelText = '黄色告警';
    } else {
      t.level = 'none'; t.levelText = '正常';
    }
    t.source = 'manual'; t.sourceLabel = '人工设置';
  } else {
    t.balance = null; t.consumePct = null;
    t.spendRatio = null; t.spendRatioOver = false;
    t.level = null; t.levelText = '未配置';
    t.source = 'none'; t.sourceLabel = '未设置';
  }
  return t;
}

/* 余额变了要通知的地方。
   v3.2 起侧栏不再显示金额（改为工具类型 / 版本 / 用户名），余额与告警统一在
   监控审计舱读数区与「对账中心 → T+1 日终对账」出现——同一个数只在一处，
   改完才不会出现「侧栏说 950、页面说 1019」这种自相矛盾。
   这个函数保留成单一钩子：以后哪里需要跟着余额刷新，往这里加，而不是散落各处。 */
function syncBalanceChrome(){
  /* 侧栏已不承载金额，这里留作扩展点：以后哪里需要跟着余额刷新就往这加 */
}

/* 轻提示 */
function toast(msg){
  const el = document.getElementById('toast');
  if (!el){ console.log(msg); return; }
  el.textContent = msg;
  el.classList.add('on');
  clearTimeout(el.__t);
  el.__t = setTimeout(()=>el.classList.remove('on'), 2600);
}

/* ---------- F2 总览与 F1/F7 补充数据 ---------- */
const OVERVIEW = Object.assign({totals:{}, banner:[], topTasks:[], quality:{}, source:{}}, RAW.overview || {});
const PROJECTS = RAW.projects || [];
const MATRIX = RAW.matrix || [];
const CLASSIFICATION = RAW.classification || {};
const SCHED = Object.assign({items:[], monthlyForecast:0, weeklyCredits:0}, RAW.scheduled || {});
const RECS = Object.assign({tiers:{t1:[],t2:[],t3:[]}, immediateWindowSaving:0},
                           RAW.recommendations || {});

/* ---------- 任务监测（定时 / 后台 / 技能 / 隐藏调用）---------- */
const MON = Object.assign({
  totals:{}, categories:[], records:[], autoRuns:[], configs:[],
  skills:[], hidden:[], notes:[], sufficient:false
}, RAW.monitor || {});

const LEVEL_COLOR = {L1:'var(--ok-10)', L2:'var(--info-9)', L3:'var(--warn-9)', L4:'var(--bad-9)'};
const TRIG_TAG = {'人工':'blue', '定时':'amber', '后台自动化':'purple'};

/* ---------- 通用小组件 ---------- */
function kpiMini(lb, v, u, color, ft){
  return '<div class="kpi" style="--kc:'+color+';padding:11px 14px 10px">'
    + '<div class="kpi-lb">'+lb+'</div>'
    + '<div class="kpi-v" style="font-size:20px">'+v+(u?'<span class="u">'+u+'</span>':'')+'</div>'
    + '<div class="kpi-ft">'+ft+'</div></div>';
}

function bannerHTML(items){
  if (!items || !items.length) return '';
  return items.map(b=>{
    const icon = b.level==='green' ? '✓' : b.level==='gray' ? 'i' : '!';
    return '<div class="banner '+(b.level||'gray')+'">'
      + '<div class="bi">'+icon+'</div>'
      + '<div style="min-width:0"><div class="bt">'+b.title+'</div>'
      + '<div class="bd">'+b.desc+'</div></div>'
      + (b.tag?'<span class="tag gray sm bg">'+b.tag+'</span>':'')
      + '</div>';
  }).join('');
}

