/* ============ DOM 与通用渲染 ============ */
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

const esc = s => String(s === null || s === undefined ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
  .replace(/"/g,'&quot;').replace(/'/g,'&#39;');

const CH = {};           // 图表实例注册表

/* ============ 页面清单：4 个一级菜单 ============
   带 subs 的一级项在侧栏展开二级菜单；没有 subs 的（监控审计舱 / 运营分析）
   是单页。顺序即侧栏顺序，也决定 hash 的默认子页（取第一个）。 */
const PAGES = {
  cockpit:{t:'监控审计舱', fit:true},
  recon:{t:'对账中心', fit:true, subs:{
    flow:{t:'今日收支流水'},
    expense:{t:'任务消耗明细'},
    daily:{t:'T+1 日终对账'},
    book:{t:'对账台簿'}}},
  control:{t:'审计控制', fit:true, subs:{
    exec:{t:'执行监测'},
    task:{t:'任务设置'},
    income:{t:'收入配置'},
    recycle:{t:'回收调整'}}},
  analysis:{t:'运营分析', fit:true}
};

/* 页面 → 容器 id。对账中心四页共用「筛选栏 + 表格骨架」，合用一个容器；
   审计控制四页内容形态完全不同（表格 / 表单 / 图表），各用各的容器，
   互不干扰彼此的绑定与滚动位置。 */
function viewNode(page, sub){
  if (page === 'control') return 'v-control-' + sub;
  return 'v-' + page;
}

function defaultSub(page){
  const p = PAGES[page];
  return (p && p.subs) ? Object.keys(p.subs)[0] : '';
}


/* 图表主题常量：一律从 CSS 令牌读，不在 JS 里抄第二份色板。
   深/浅主题切换时 canvas 已经画好的颜色不会自己变，所以切主题必须重绘，
   由 refreshAllData(true) 统一处理（见 p10_boot.js 的 skinTgl 绑定）。 */
function themeTokens(){
  const cs = getComputedStyle(document.documentElement);
  const v = (n, d) => (cs.getPropertyValue(n) || '').trim() || d;
  return {
    text: v('--text', '#e9f1ff'), text2: v('--text2', '#93a8cc'), text3: v('--text3', '#5e739a'),
    line: v('--line', 'rgba(140,176,235,.10)'), line2: v('--line2', 'rgba(140,176,235,.18)'),
    panel: v('--panel3', '#101a36'), tip: v('--tip-bg', 'rgba(8,16,34,.95)'),
    pri: v('--info-9', '#2e7bff'), ok: v('--ok-9', '#30a46c'), warn: v('--warn-9', '#ffc53d'),
    bad: v('--bad-9', '#e5484d'), iris: v('--iris-9', '#5b5bd6'),
    holo: v('--holo-cyan', '#00ffff'), grid: v('--grid-line', 'rgba(140,176,235,.055)')
  };
}
let TT = themeTokens();

/* ECharts 是 canvas 渲染，不认 CSS 变量：option 里写 var(--x) 会画不出颜色。
   约定「主题色一律在 option 里写 var(--token)」，由这里统一解析成具体色值——
   这样“令牌只在一处定义”对 canvas 图表同样成立，不必在 JS 里再抄一份色板。 */
let VAR_CACHE = {};
function resolveColor(v){
  if (typeof v !== 'string' || v.indexOf('var(') !== 0) return v;
  const name = v.slice(4, v.indexOf(')')).trim();
  if (VAR_CACHE[name]) return VAR_CACHE[name];
  const val = (getComputedStyle(document.documentElement).getPropertyValue(name) || '').trim();
  const out = val || 'var(--text3)';
  VAR_CACHE[name] = out;
  return out;
}
function paint(node){
  if (typeof node === 'string') return resolveColor(node);
  if (Array.isArray(node)) return node.map(paint);
  if (node && typeof node === 'object'){
    const out = {};
    Object.keys(node).forEach(k=>{ out[k] = paint(node[k]); });
    return out;
  }
  return node;                      // 函数（formatter 等）原样带过去
}

/* 「令牌色 + 透明度」：canvas 只认完整色值，`rgba(var(--pri),.2)` 这种
   只有 CSS 能解析——直接塞给 ECharts 会在 addColorStop 处抛
   SyntaxError（渐变、阴影、axisPointer 都会踩）。所以图表里要半透明时
   统一走这里：读分量令牌（如 --pri = "0,110,255"）再拼成 rgba。 */
function varrgb(name, alpha){
  const raw = (getComputedStyle(document.documentElement).getPropertyValue(name) || '').trim();
  const p = raw.split(',').map(s => s.trim());
  if (p.length === 3 && p.every(x => /^\d+$/.test(x))) return 'rgba(' + p.join(',') + ',' + alpha + ')';
  return 'rgba(120,150,200,' + alpha + ')';
}

function chart(domId, option){
  const el = document.getElementById(domId);
  if (!el) return null;
  if (CH[domId]) { CH[domId].dispose(); }
  const c = echarts.init(el, null, {renderer:'canvas'});
  c.setOption(paint(option));
  CH[domId] = c;
  return c;
}

function baseGrid(o){
  return Object.assign({left:8, right:14, top:16, bottom:6, containLabel:true}, o||{});
}
function axisX(extra){
  return Object.assign({
    type:'category', axisLine:{lineStyle:{color:TT.line2}}, axisTick:{show:false},
    axisLabel:{color:TT.text3, fontSize:10.5}, splitLine:{show:false}
  }, extra||{});
}
function axisY(extra){
  return Object.assign({
    type:'value', axisLine:{show:false}, axisTick:{show:false},
    axisLabel:{color:TT.text3, fontSize:10.5},
    splitLine:{lineStyle:{color:'var(--grid-line)', type:'dashed'}}
  }, extra||{});
}
const tooltipBase = {
  // 也走令牌：切主题时 tooltip 的底色/边框跟着变，不必重建这个常量
  backgroundColor:'var(--tip-bg)', borderColor:'var(--tip-line)', borderWidth:1,
  textStyle:{color:'var(--text)', fontSize:11.5}, padding:[8,11],
  extraCssText:'border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.32);backdrop-filter:blur(6px);'
};

function cardHTML(title, sub, inner, cls){
  return '<div class="card '+(cls||'')+'">'
    + '<div class="card-hd"><div class="card-title">'+title+'</div>'
    + (sub?'<div class="card-sub">'+sub+'</div>':'') + '</div>' + inner + '</div>';
}

/* ============ 列表列宽自适应 ============ */
/* 目标：列宽跟着「这一列实际展示的内容」走，内容多的列给得多、内容少的列给得少，
   避免出现某列空着一大片、某列挤成一团。做法：
     1) 用 canvas 量出每列表头与单元格的文字自然宽度；
        单元格用「自己的字体」量（正文 11.8px ≠ 表头 10.5px），带 padding 的子元素
        （tag / lvl 徽标）按实际渲染宽算，避免把数字列估窄后出现「-3…」这种截断；
     2) 每列取「表头宽」与「该列最宽内容」的较大值作为需求宽度，并夹在上下限内；
     3) 数字 / 紧凑列（表头右对齐或含徽标）下限取内容宽——宁可整列加宽也不截断数字；
     4) 总宽超过容器时，从还有余量的列里按比例削；
     5) 总宽小于容器时，富余空间全给「内容最长的那一列」（通常是任务名），不平均摊；
     6) 结果写进 <colgroup> 并把表格宽度固定为各列之和，避免浏览器再自行分配余量。 */
const FIT_CANVAS = document.createElement('canvas');
const FIT_CTX = FIT_CANVAS.getContext('2d');
function fitTextWidth(text, font){
  try { FIT_CTX.font = font; } catch(e){}
  return FIT_CTX.measureText(text || '').width;
}
/* 单元格实际需要多宽：文字自然宽 + 左右内边距，且不小于内嵌元素的渲染宽 */
function fitCellWidth(td, font){
  const cs = getComputedStyle(td);
  const padL = parseFloat(cs.paddingLeft) || 0;
  const padR = parseFloat(cs.paddingRight) || 0;
  const t = (td.textContent||'').replace(/\s+/g,' ').trim();
  let w = 0;
  if (t){
    const s = t.length > 36 ? t.slice(0,36)+'…' : t;
    w = fitTextWidth(s, font);
  }
  const kids = Array.from(td.children);
  if (kids.length === 1){
    const ow = kids[0].getBoundingClientRect().width;
    /* 徽标（tag / lvl）自带左右内边距与边框，且处在 content-box 计算之外，
       按渲染宽取整后往往与单元格可用宽「恰好相等」——零余量时字体/缩放一变就截断。
       这里统一再留 6px 安全余量。 */
    const wExtra = (kids[0].classList && (kids[0].classList.contains('tag') || kids[0].classList.contains('lvl'))) ? 6 : 0;
    if (ow + wExtra > w) w = ow + wExtra;
  } else if (kids.length > 1){
    /* 一个单元格里并排多个徽标（如「上下文膨胀 重试风暴」）：要的是宽度之和，
       取最大值会低估，导致最后一枚徽标被切掉 */
    let sum = 0;
    kids.forEach(ch=>{
      const ow = ch.getBoundingClientRect().width;
      if (ow > 0) sum += ow + 4;             /* 4px 近似 inline 间距 */
    });
    if (sum - 4 > w) w = sum - 4;
  }
  return w + padL + padR;
}
function fitTableColumns(tbl){
  if(!tbl || !tbl.querySelector) return;
  const wrap = tbl.parentElement;
  const avail = Math.floor((wrap && wrap.clientWidth) || tbl.clientWidth || 0);
  if(avail < 60) return;
  const ths = Array.from(tbl.querySelectorAll('thead th'));
  if(!ths.length) return;
  const rows = Array.from(tbl.querySelectorAll('tbody tr'));
  const cs = getComputedStyle(ths[0]);
  const headFont = (cs.fontWeight||'400')+' '+(cs.fontSize||'12px')+' '+(cs.fontFamily||'sans-serif');
  const n = ths.length;
  const floor = [], want = [], tight = [], uplift = [], caps = [];
  let sumPlain = 0;
  ths.forEach((th,i)=>{
    const right = getComputedStyle(th).textAlign === 'right';
    const hw = fitTextWidth((th.textContent||'').trim(), headFont) + 16;
    floor[i] = hw;                                   /* 再窄也不能盖住表头 */
    caps[i] = right ? 140 : 320;
    want[i] = Math.min(hw, caps[i]);                 /* 初始需求：先按表头算 */
    th.dataset.fitNum = right ? '1' : '0';
    tight[i] = right;                                /* 数字列：下限取内容宽，不截断数字 */
    /* 表头是 letter-spacing:.05em + 大写，canvas 量出来比实际占位窄。
       不补这一截，「执行次数 / 消耗积分」这类四字表头会被削成「执行…」。
       但这截只在下限总和还装得下时才补：为了不截断表头而把整张表撑出
       横向滚动条，代价更大——表头省略号还能猜，被推出屏幕的列连猜都没得猜。 */
    const ls = parseFloat(getComputedStyle(th).letterSpacing) || 0;
    const txt = (th.textContent||'').trim();
    uplift[i] = Math.max(0, ls * txt.length + 2);
    sumPlain += hw;
  });
  let budget = Math.max(0, avail - sumPlain);
  for(let i=0;i<n && budget > 0;i++){
    const u = Math.min(uplift[i], budget);
    floor[i] += u;
    want[i] = Math.max(want[i], Math.min(floor[i], caps[i]));
    budget -= u;
  }
  rows.forEach(tr=>{
    const tds = tr.children;
    /* 占位行（padRows 的空行、空态提示行）只有一个跨列单元格：
       它的文字宽会被整段记到第一列，把「任务类型」这种短列撑到 200px 以上，
       真正需要宽度的数字列反而被挤到截断。这类行不参与测量。 */
    if (tds.length === 1 && (tds[0].colSpan || 1) > 1) return;
    for(let i=0;i<n && i<tds.length;i++){
      const td = tds[i];
      if(!td.textContent || !td.textContent.trim()) continue;
      const ccs = getComputedStyle(td);
      const cellFont = (ccs.fontWeight||'400')+' '+(ccs.fontSize||'12px')+' '+(ccs.fontFamily||'sans-serif');
      const w = fitCellWidth(td, cellFont);
      const cap = ths[i].dataset.fitNum === '1' ? 140 : 320;
      if(w > want[i]) want[i] = Math.min(w, cap);
      /* 含 tag / lvl 徽标的列按紧凑列处理：下限取内容宽 */
      if (!tight[i] && td.querySelector('.tag, .lvl')) tight[i] = true;
    }
  });
  /* 数字 / 紧凑列：下限抬到该列内容宽（上限 140），避免出现「-3…」这类截断。
     +7px 是因为 tabular-nums（等宽数字）比 canvas 量出来的比例数字更宽，
     measureText 拿不到 font-variant-numeric，不补就会刚好差几个像素。 */
  for(let i=0;i<n;i++){
    if (tight[i] && want[i] > floor[i]) floor[i] = Math.min(want[i] + 7, 140);
  }
  let w = want.slice();
  let sum = w.reduce((a,b)=>a+b, 0);

  if(sum > avail){
    /* 超出容器：从还有余量（高于表头下限）的列按比例削，削不动就横向滚动 */
    for(let pass=0; pass<12 && sum > avail; pass++){
      let pool = 0;
      for(let i=0;i<n;i++) pool += (w[i] - floor[i]);
      if(pool <= 0.5) break;
      const k = (sum - avail) / pool;
      for(let i=0;i<n;i++) w[i] = Math.max(floor[i], w[i] - (w[i]-floor[i])*k);
      sum = w.reduce((a,b)=>a+b, 0);
    }
  } else if(sum < avail){
    /* 有富余：先给「弹性列」（通常是任务名列）最多 220px，
       剩下的按比例摊给所有列。全给一列会让它涨到 800px 宽、别的列反而挤到截断。 */
    let flex = -1;
    ths.forEach((th,i)=>{ if(th.dataset.fitFlex === '1') flex = i; });
    if(flex < 0){
      outer:
      for(const tr of rows){
        const tds = tr.children;
        for(let i=0;i<n && i<tds.length;i++){
          if(tds[i].classList.contains('name')){ flex = i; break outer; }
        }
      }
    }
    const grow = avail - sum;
    if(flex >= 0){
      const add = Math.min(grow, 220);
      w[flex] += add;
      const rest = grow - add;
      if(rest > 0.5){
        const tot = w.reduce((a,b)=>a+b, 0) || 1;
        for(let i=0;i<n;i++) w[i] += rest * (w[i]/tot);
      }
    } else {
      /* 纯数字表：按需求宽比例分摊，不做平均摊，避免每列都松垮 */
      const tot = w.reduce((a,b)=>a+b, 0) || 1;
      for(let i=0;i<n;i++) w[i] += grow * (w[i]/tot);
    }
    sum = avail;
  }

  /* 差几个像素的收尾：别为了 3px 在表格底下留一条横向滚动条。
     从最宽的列里各让几像素肉眼看不出来，滚动条却是实打实多一条。
     只处理十几像素以内的残差；真的装不下（十几列的表）就老老实实滚动。 */
  if(sum >= avail && sum - avail <= 14){
    const order = w.map((v,i)=>i).sort((a,b)=>w[b]-w[a]);
    /* +3 是给亚像素留的余量：列宽写进去是整数，七列各自四舍五入之后
       渲染出来会比总和宽几像素，那几像素足够叫出一条滚动条。 */
    let left = (sum - avail) + 3;
    for(let k=0;k<order.length && left>0;k++){
      const i = order[k];
      const give = Math.min(left, Math.max(0, w[i] - 24));
      w[i] -= give;
      left -= give;
    }
    sum = w.reduce((a,b)=>a+b, 0);
  }

  let cg = tbl.querySelector('colgroup');
  if(!cg){
    cg = document.createElement('colgroup');
    tbl.insertBefore(cg, tbl.firstChild);
  }
  cg.innerHTML = w.map(x=>'<col style="width:'+Math.round(x)+'px">').join('');
  tbl.style.tableLayout = 'fixed';
  tbl.style.width = Math.round(sum)+'px';
  tbl.style.minWidth = Math.round(sum)+'px';
  tbl.classList.add('fitw');
}
/* 对一个容器里的所有 .tbl 表格做列宽自适应 */
function applyTableFit(root){
  if(!root || !root.querySelectorAll) return;
  Array.from(root.querySelectorAll('table.tbl')).forEach(fitTableColumns);
}

function deltaHTML(cur, prev, invert){
  const d = prev ? (cur-prev)/prev*100 : 0;
  const cls = Math.abs(d)<0.5 ? 'flat' : ((d>0) !== !!invert ? (invert?'down':'up') : (invert?'up':'down'));
  const sign = d>0?'+':'';
  return '<span class="delta '+cls+'">'+sign+d.toFixed(1)+'%</span>';
}

/* ============ 路由（一级 + 二级） ============ */
const inited = {};                 // 按 page/sub 缓存「已渲染过」
let firstRender = true;
let RT = {page:'cockpit', sub:''}; // 当前路由

/* hash 约定：#cockpit · #recon/flow · #control/task
   只认一级也行（#recon 落到该组第一个子页），保证外部链接不会 404。 */
function parseHash(){
  const raw = (location.hash || '').slice(1);
  if (!raw) return null;
  const parts = raw.split('/');
  const page = parts[0];
  if (!PAGES[page]) return null;
  const sub = (PAGES[page].subs && PAGES[page].subs[parts[1]]) ? parts[1] : defaultSub(page);
  return {page:page, sub:sub};
}

/* 页面 → 构造函数。放这里而不是散在各分片，是为了让「有哪些页面」只有一处定义。 */
function buildPage(page, sub){
  if (page === 'cockpit') return buildCockpit();
  if (page === 'recon') return buildRecon(sub);
  if (page === 'control'){
    if (sub === 'exec') return buildExec();
    if (sub === 'task') return buildTaskSet();
    if (sub === 'income') return buildIncome();
    return buildRecycle();
  }
  return buildAnalysis();
}

function renderView(page, sub, force){
  if (!PAGES[page]){ page = 'cockpit'; sub = ''; }
  if (PAGES[page].subs && !PAGES[page].subs[sub]) sub = defaultSub(page);
  if (!PAGES[page].subs) sub = '';
  RT = {page:page, sub:sub};
  const key = sub ? page + '/' + sub : page;

  /* 标题：一级 + 二级，位置信息始终可见 */
  const p = PAGES[page];
  $('#tbTitle').textContent = p.t + (sub ? ' · ' + p.subs[sub].t : '');

  /* 导航态：一级项高亮当前组；该组的二级菜单展开并高亮当前子页。
     用 getElementById 逐个改，比整树 querySelectorAll 再 toggle 少一次遍历——
     这张表每次切页都跑，能省就省。 */
  $$('.nav-item').forEach(n=>n.classList.toggle('on', n.dataset.page === page));
  $$('.nav-sub').forEach(g=>g.classList.toggle('open', g.dataset.parent === page));
  $$('.nav-subitem').forEach(it=>it.classList.toggle('on',
    it.dataset.page === page && it.dataset.sub === sub));
  /* 进到某个有二级的页，就把那一组展开——否则页面换了、侧栏还收着 */
  if (p.subs && NAV_COLL[page]) navToggle(page, false);

  /* 视图切换 */
  $$('.view').forEach(v=>{ v.classList.remove('on'); v.classList.remove('anim'); });
  const node = viewNode(page, sub);
  const cur = document.getElementById(node);
  if (cur) cur.classList.add('on');
  /* 一屏约束只在 PAGES 里声明一次：审计舱与对账中心强制一屏，
     审计控制的表单页与运营分析允许整页滚动。 */
  $('#viewWrap').classList.toggle('fit', !!(PAGES[page] || {}).fit);
  if (!firstRender && cur){
    cur.classList.add('anim');
    cur.addEventListener('animationend', ()=>cur.classList.remove('anim'), {once:true});
  }
  firstRender = false;

  if (force || !inited[key]){
    buildPage(page, sub);
    inited[key] = true;
  }
  /* 列宽自适应必须在元素可见（拿到真实容器宽度）之后做 */
  applyTableFit(cur);
  setTimeout(()=>{ Object.values(CH).forEach(c=>{ try{c.resize();}catch(e){} }); }, 30);
  const want = sub ? '#' + page + '/' + sub : '#' + page;
  if (location.hash !== want) history.replaceState(null, '', want);
  $('#viewWrap').scrollTop = 0;
}

function go(page, sub){ renderView(page, sub); }

/* 兼容旧调用：goTab('recon','daily') 与 go('recon','daily') 等价。
   旧写法 goTab('ledger','anomaly') 这类名字已不存在，直接按页面映射兜住。 */
const LEGACY_PAGE = {overview:'cockpit', ledger:'recon', monitor:'control', benchmark:'analysis'};
function goTab(name, tab){
  go(LEGACY_PAGE[name] || name, tab === 'anomaly' ? 'recycle' : tab);
}

/* 数据变了要重画：当前页强制重建，其余页作废缓存等下次进入 */
function invalidateAll(){
  Object.keys(inited).forEach(k=>{ inited[k] = false; });
}
function refreshView(page, sub){
  if (!PAGES[page]) return;
  const key = sub ? page + '/' + sub : page;
  if (page === RT.page && (sub ? sub === RT.sub : true)) renderView(page, sub, true);
  else inited[key] = false;
}
/* 积分配置改动后统一走这里：重算 → 侧栏 → 当前页 */
function refreshAfterBalance(){
  recalcBudget();
  syncBalanceChrome();
  invalidateAll();
  renderView(RT.page, RT.sub, true);
  setTimeout(()=>{ Object.values(CH).forEach(c=>{ try{c.resize();}catch(e){} }); }, 40);
}

/* 「数据更新」按钮：重算派生指标并强制重绘当前页（数据是本次采集的快照，不会联网再拉） */
let LAST_REFRESH = null;
function refreshAllData(quiet){
  recalcBudget();
  invalidateAll();
  renderView(RT.page, RT.sub, true);
  syncBalanceChrome();
  LAST_REFRESH = new Date();
  const st = document.getElementById('tbStamp');
  if (st){
    const p = n => String(n).padStart(2, '0');
    st.textContent = '更新于 ' + p(LAST_REFRESH.getHours()) + ':' + p(LAST_REFRESH.getMinutes())
      + ':' + p(LAST_REFRESH.getSeconds());
  }
  setTimeout(()=>{ Object.values(CH).forEach(c=>{ try{c.resize();}catch(e){} }); }, 60);
  if (!quiet){
    toast('已按本次采集快照重算并重绘 · 数据截至 '
      + (META.generatedAt || '—').slice(0, 16) + '，要拿最新日志请重跑 audit.py');
  }
}

/* ============ 二级菜单的收起 / 展开 ============
   两件事要同时成立：
   1) 用户能手动收起任一组（状态记 localStorage，下次打开还是他上次的样子）；
   2) 从审计舱的入口或者深链跳进某个二级页时，那一组必须自动展开——
      否则会变成「页面已经在对账中心，侧栏却看不到它的子项」，位置感全丢。

   收起用 max-height 过渡，不用 display:none：后者没有过渡，视觉上是「啪」地消失，
   而且会连带把焦点顺序打乱。 */
const NAV_KEY = 'wbc-nav-collapsed';
const NAV_COLL = (()=>{
  try {
    const v = JSON.parse(localStorage.getItem(NAV_KEY) || '{}');
    return {recon: !!v.recon, control: !!v.control};
  } catch (e){ return {recon:false, control:false}; }
})();

function navPersist(){
  try { localStorage.setItem(NAV_KEY, JSON.stringify(NAV_COLL)); } catch (e) {}
}

function navApply(){
  $$('.nav-sub').forEach(g=>{
    g.classList.toggle('collapsed', !!NAV_COLL[g.dataset.parent]);
  });
  $$('.nav-item.nav-has-sub').forEach(n=>{
    const c = !!NAV_COLL[n.dataset.group];
    n.classList.toggle('collapsed', c);
    const caret = n.querySelector('.nav-caret');
    if (caret) caret.setAttribute('aria-expanded', c ? 'false' : 'true');
  });
}

function navToggle(group, force){
  const next = (typeof force === 'boolean') ? force : !NAV_COLL[group];
  NAV_COLL[group] = next;
  navPersist();
  navApply();
}

/* 导航点击：一级项与二级项用同一套委托，避免两处各绑一次。
   caret 是独立命中区——点箭头只折叠，不跳页。 */
(function bindNav(){
  const bar = document.querySelector('.sidebar');
  if (!bar) return;
  bar.addEventListener('click', e=>{
    const caret = e.target.closest('[data-caret]');
    if (caret){
      e.stopPropagation();
      navToggle(caret.dataset.caret);
      return;
    }
    const it = e.target.closest('.nav-item, .nav-subitem');
    if (!it) return;
    go(it.dataset.page, it.dataset.sub);
  });
  bar.addEventListener('keydown', e=>{
    const caret = e.target.closest('[data-caret]');
    if (caret && (e.key === 'Enter' || e.key === ' ')){
      e.preventDefault();
      navToggle(caret.dataset.caret);
    }
  });
  navApply();
})();

/* 页内跳转统一走 data-go（一级）/ data-sub（二级） */
function bindGoLinks(root){
  const el = typeof root === 'string' ? document.querySelector(root) : root;
  if (!el || el.__goBound) return;
  el.__goBound = true;
  el.addEventListener('click', e=>{
    const g = e.target.closest('[data-go]');
    if (g){ go(g.dataset.go, g.dataset.sub); }
  });
}

/* ============ 分页器 ============
   列表统一每页 10 行：审计场景里「一屏能扫完 + 页码可复述」比「一次看全」重要，
   翻页成本远低于在一张长表里找行。页码只画「首 / 当前±1 / 末」，中间折叠成 …。 */
const PG_SIZE = 10;
function pgSlice(rows, page, size){
  const n = size || PG_SIZE;
  const list = rows || [];
  const total = list.length;
  const pages = Math.max(1, Math.ceil(total / n));
  const p = Math.min(Math.max(1, page || 1), pages);
  return {rows:list.slice((p-1)*n, p*n), page:p, pages:pages, total:total, size:n,
          from: total ? (p-1)*n + 1 : 0, to: Math.min(total, p*n)};
}
function pagerHTML(s, attr){
  const A = attr || 'page';
  if (!s || !s.total) return '';
  const btn = i => '<button class="pg'+(i===s.page?' on':'')+'" data-'+A+'="'+i+'">'+i+'</button>';
  const want = Array.from(new Set([1, s.pages, s.page-1, s.page, s.page+1]
    .filter(i => i >= 1 && i <= s.pages))).sort((a,b)=>a-b);
  const nums = [];
  want.forEach((i, ix)=>{
    if (ix && i - want[ix-1] > 1) nums.push('<span class="pg-dot">…</span>');
    nums.push(btn(i));
  });
  return '<div class="pager"><span class="pg-info">共 <b>'+s.total+'</b> 条 · 第 '
    + s.page+' / '+s.pages+' 页 · 每页 '+s.size+' 行'
    + (s.total ? ' · 当前 '+s.from+'–'+s.to : '') + '</span><div class="pg-btns">'
    + '<button class="pg" data-'+A+'="'+(s.page-1)+'"'+(s.page<=1?' disabled':'')+'>上一页</button>'
    + nums.join('')
    + '<button class="pg" data-'+A+'="'+(s.page+1)+'"'+(s.page>=s.pages?' disabled':'')+'>下一页</button>'
    + '</div></div>';
}

/* CSV 导出：纯前端 Blob 生成，不走网络。带 BOM，Excel 直接认中文。 */
function exportCSV(name, header, rows){
  const q = v => {
    const s = String(v === null || v === undefined ? '' : v);
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g,'""') + '"' : s;
  };
  const text = '\ufeff' + [header.map(q).join(',')]
    .concat((rows||[]).map(r => r.map(q).join(','))).join('\r\n');
  const blob = new Blob([text], {type:'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(()=>{ URL.revokeObjectURL(a.href); a.remove(); }, 120);
  toast('已导出 ' + name + '（' + (rows||[]).length + ' 行，仅在本机生成）');
}

/* ============ 抽屉：执行链路 + 消耗明细 ============ */
let DR = {tab:'chain', task:null, filter:'all'};

function phaseColor(p){ return PHASE_COLOR[p] || 'var(--text3)'; }

function drawerChain(t){
  const steps = t.steps || [];
  const hasTok = (t.inTok + t.outTok) > 0;
  const filt = DR.filter || 'all';

  /* 阶段分布（工具步并入其所属阶段做计数，仅用于说明构成） */
  const ph = {}, phOrder = [];
  steps.forEach(s=>{
    const k = s.phase || '推理';
    if (ph[k] === undefined){ ph[k] = {n:0, cr:0}; phOrder.push(k); }
    ph[k].n++; ph[k].cr += s.credits || 0;
  });
  const primary = (t.models && t.models[0]) || (steps.find(x=>x.kind==='model') || {}).model || '';
  let h = '';

  /* 概览：两列四行，长文本也不会被截断（原来的四格在 660px 抽屉里会挤掉数字） */
  const kv = (l, v) => '<div class="kv"><span>'+l+'</span><b>'+v+'</b></div>';
  /* nTools 是「不同工具种类数」，这里要的是工具步数，直接从步骤里数 */
  const nTs = steps.filter(x=>x.kind==='tool').length;
  const nMs = steps.length - nTs;
  h += '<div class="dr-kv">'
    + kv('触发方式', esc(t.trigger)+' · '+(t.rtype==='automation'?'定时':'会话'))
    + kv('步骤构成', steps.length+' 步 <i>模型 '+nMs+' / 工具 '+nTs+'</i>')
    + kv('主模型', '<span title="'+esc(primary||'—')+'">'
        + esc(M[primary] ? M[primary].name : (primary||'—'))+'</span>'
        + ((t.models||[]).length>1 ? ' <i>切换 '+(t.models.length-1)+' 次</i>' : ''))
    + kv('结束状态', '<span style="color:'+(t.status==='成功'?'var(--ok-11)':'var(--bad-11)')+'">'
        + esc(t.status)+'</span>'+(t.retry?' <i>重试 '+t.retry+' 次</i>':''))
    + '</div>';

  /* 阶段构成 chips */
  h += '<div class="phase-chips">'
    + phOrder.map(k=>{
        const r = ph[k];
        return '<span class="tag sm" style="background:rgba(var(--ink),.07);color:'+phaseColor(k)+'">'
          + esc(k)+' '+r.n+' 步 · '+fmt1(r.cr)+' 积分</span>';
      }).join('')
    + '<span class="tag sm" style="background:rgba(var(--ink),.07);color:var(--text3)">'
    + (hasTok ? 'token 埋点完整' : '无 token 埋点')+'</span>'
    + '</div>';

  if (!hasTok){
    h += '<div class="hint" style="margin-bottom:10px">该任务的日志只有积分上报，没有 token 与单步耗时'
      + '（早期版本行为）。因此逐步明细里 token 与耗时显示为「—」，积分与总时长仍然可信。</div>';
  }

  /* 过滤 */
  const nModel = nMs, nTool = nTs;
  const nBad = steps.filter(x=>x.err).length;
  h += '<div class="flex" style="gap:5px;flex-wrap:wrap;margin:2px 0 9px">'
    + [['all','全部',steps.length],['model','仅模型步',nModel],
       ['tool','仅工具步',nTool],['bad','异常步',nBad]].map(([k,n,c])=>
        '<button class="chip'+(filt===k?' on':'')+'" data-step="'+k+'">'+n+' <b>'+c+'</b></button>'
      ).join('')
    + '</div>';

  /* 时间线：固定列宽的表格式，避免数字挤在一起 */
  const shown = steps.filter(s=>filt==='all' ? true
    : filt==='model' ? s.kind==='model' : filt==='tool' ? s.kind==='tool' : !!s.err);

  h += '<div class="tl-head"><div>时间</div><div>阶段</div><div>模型 / 工具</div>'
    + '<div class="r">输入·输出</div><div class="r">积分</div><div class="r">耗时</div></div>';

  const CAP = 200;                       /* 单屏最多渲染 200 步，其余用过滤缩小范围 */
  const view = shown.slice(0, CAP);
  if (!shown.length){
    h += '<div class="hint" style="margin-top:10px">该过滤条件下没有步骤。</div>';
  }
  view.forEach(s=>{
    const isTool = s.kind==='tool';
    const who = isTool
      ? '<span class="tag cyan sm">'+esc(s.tool)+'</span>'
      : '<i class="dotm" style="background:'+modelColor(s.model)+'"></i>'
        + '<span class="nm">'+esc(M[s.model]?M[s.model].short:(s.model||'—'))+'</span>'
        + (s.err?'<span class="tag red sm">失败</span>':'')
        + (s.est?'<span class="tag amber sm">折算</span>':'');
    h += '<div class="tl-row'+(s.err?' bad':'')+'">'
      + '<div class="tl-time">'+esc((s.ts||'').slice(0,8)||String(s.i).padStart(2,'0'))+'</div>'
      + '<div><span class="tag sm" style="background:rgba(var(--ink),.07);color:'
      +   phaseColor(s.phase)+'">'+esc(s.phase||'—')+'</span></div>'
      + '<div class="tl-who">'+who+'</div>'
      + '<div class="tl-num">'+(isTool ? '—'
          : (hasTok ? (fmt(s.inTok)+' · '+fmt(s.outTok)) : '—'))+'</div>'
      + '<div class="tl-num" style="color:'+(isTool?'var(--text3)':'var(--info-11)')+'">'
      +   (isTool ? '0' : fmt1(s.credits||0))+'</div>'
      + '<div class="tl-num">'+((s.ms||0)>0 ? ((s.ms/1000).toFixed(1)+'s') : '—')+'</div>'
      + (s.arg ? '<div class="tl-arg" title="'+esc(String(s.arg).slice(0,300))+'">'
          + esc(String(s.arg).slice(0,160))+'</div>' : '')
      + '</div>';
  });

  if (shown.length > CAP){
    h += '<div class="hint" style="margin-top:8px">共 '+shown.length+' 步，已渲染前 '+CAP+' 步；'
      + '用上方过滤（仅模型步 / 仅工具步 / 异常步）或消耗明细里的逐步表查看其余步骤。</div>';
  }
  h += '<div class="hint" style="margin-top:10px">链路按日志时间顺序还原；积分为 0 的工具步骤不单独计费，'
    + '成本体现在相邻模型步的输入 token 上。时间列为步骤开始时刻（时:分:秒），'
    + '「输入·输出」为该步 token 数。</div>';
  return h;
}

function drawerDetail(t){
  const steps = t.steps || [];
  const hasTok = (t.inTok + t.outTok) > 0;
  const byModel = {}, byPhase = {}, byTool = {};
  steps.forEach(s=>{
    const c = s.credits || 0;
    if (s.kind==='model'){
      const k = s.model||'unknown';
      byModel[k] = byModel[k] || {c:0,n:0,inTok:0,outTok:0,ms:0};
      byModel[k].c += c; byModel[k].n++;
      byModel[k].inTok += s.inTok||0; byModel[k].outTok += s.outTok||0; byModel[k].ms += s.ms||0;
    } else {
      byTool[s.tool] = byTool[s.tool] || {calls:0,err:0,ms:0};
      byTool[s.tool].calls++; byTool[s.tool].ms += s.ms||0;
      if (s.err) byTool[s.tool].err++;
    }
    const p = s.phase||'推理';
    byPhase[p] = (byPhase[p]||0) + c;
  });

  const total = t.credits || 0;
  const modelRows = Object.keys(byModel).sort((a,b)=>byModel[b].c-byModel[a].c);
  const phaseRows = Object.keys(byPhase).sort((a,b)=>byPhase[b]-byPhase[a]);
  const toolRows = Object.keys(byTool).sort((a,b)=>byTool[b].calls-byTool[a].calls);

  let h = '<div class="grid" style="grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px">'
    + '<div class="kpi" style="--kc:var(--info-9);padding:10px 12px"><div class="kpi-lb">总积分</div>'
    + '<div class="kpi-v" style="font-size:19px">'+fmt1(total)+'</div></div>'
    + '<div class="kpi" style="--kc:var(--ok-11);padding:10px 12px"><div class="kpi-lb">输入 / 输出 token</div>'
    + '<div class="kpi-v" style="font-size:19px">'+(hasTok?fmt(t.inTok):'—')
    + '<span class="u">/ '+(hasTok?fmt(t.outTok):'—')+'</span></div></div>'
    + '<div class="kpi" style="--kc:var(--iris-11);padding:10px 12px"><div class="kpi-lb">端到端时长</div>'
    + '<div class="kpi-v" style="font-size:19px">'+t.duration+'<span class="u">s</span></div></div>'
    + '<div class="kpi" style="--kc:'+(t.retry?'var(--bad-9)':'var(--ok-10)')+';padding:10px 12px"><div class="kpi-lb">工具失败次数</div>'
    + '<div class="kpi-v" style="font-size:19px">'+(t.retry||0)+'<span class="u">次</span></div></div>'
    + '</div>';

  if (!hasTok){
    h += '<div class="hint" style="margin-bottom:12px">该任务没有 token 埋点，token 与单步耗时列显示「—」；'
      + '积分拆分与工具调用统计不受影响。</div>';
  }

  /* 按模型 */
  h += '<div class="sec-t">按模型拆分</div><div class="brk">'
    + modelRows.map(k=>{
        const r = byModel[k];
        const share = total ? r.c/total : 0;
        return '<div class="brk-row">'
          + '<div class="brk-name"><i class="dotm" style="background:'+modelColor(k)+'"></i>'
          + esc(M[k]?M[k].name:k)+'<span class="tag gray sm">'+r.n+' 步</span>'
          + ((t.models||[]).indexOf(k)>0?'<span class="tag amber sm">中途切换</span>':'')+'</div>'
          + '<div class="brk-bar"><i style="width:'+(share*100).toFixed(1)+'%;background:'+modelColor(k)+'"></i></div>'
          + '<div class="brk-val">'+fmt1(r.c)+' 积分<span>'+pct1(share)+'</span></div>'
          + '<div class="brk-sub">'+(hasTok?(fmt(r.inTok)+' in / '+fmt(r.outTok)+' out'):'token 无埋点')
          + (r.ms>0?(' · '+(r.ms/1000).toFixed(1)+'s'):'')+'</div>'
          + '</div>';
      }).join('')
    + '</div>';

  /* 按阶段 */
  h += '<div class="sec-t" style="margin-top:14px">按阶段拆分</div><div class="brk">'
    + phaseRows.map(k=>{
        const c = byPhase[k];
        const share = total ? c/total : 0;
        return '<div class="brk-row slim">'
          + '<div class="brk-name"><span class="tag sm" style="background:rgba(var(--ink),.08);color:'+phaseColor(k)+'">'+esc(k)+'</span></div>'
          + '<div class="brk-bar"><i style="width:'+(share*100).toFixed(1)+'%;background:'+phaseColor(k)+'"></i></div>'
          + '<div class="brk-val">'+fmt1(c)+' 积分<span>'+pct1(share)+'</span></div>'
          + '</div>';
      }).join('')
    + '</div>';

  /* 工具调用明细 */
  if (toolRows.length){
    h += '<div class="sec-t" style="margin-top:14px">工具调用明细</div>'
      + '<div class="tbl-wrap" style="max-height:none"><table class="tbl dense"><thead><tr>'
      + '<th>工具</th><th style="text-align:right">调用</th><th style="text-align:right">失败</th>'
      + '<th style="text-align:right">累计耗时</th></tr></thead><tbody>'
      + toolRows.map(k=>{
          const r = byTool[k];
          return '<tr><td class="mono">'+esc(k)+'</td><td class="num">'+r.calls+'</td>'
            + '<td class="num" style="color:'+(r.err?'var(--bad-11)':'var(--text3)')+'">'+(r.err||'—')+'</td>'
            + '<td class="num">'+(r.ms>0?((r.ms/1000).toFixed(1)+'s'):'—')+'</td></tr>';
        }).join('')
      + '</tbody></table></div>'
      + '<div class="hint" style="margin-top:8px">工具本身不扣积分，其输入输出经下一模型步计费；'
      + '因此工具明细用来看「调用次数与失败点」，积分看上面的模型 / 阶段拆分。</div>';
  }

  /* 逐步计费表 */
  h += '<div class="sec-t" style="margin-top:14px">逐步计费明细</div>'
    + '<div class="tbl-wrap" style="max-height:360px"><table class="tbl dense"><thead><tr>'
    + '<th>#</th><th>阶段</th><th>模型 / 工具</th><th style="text-align:right">in tok</th>'
    + '<th style="text-align:right">out tok</th><th style="text-align:right">积分</th>'
    + '<th style="text-align:right">耗时</th><th style="text-align:right">重试</th></tr></thead><tbody>'
    + steps.map(s=>{
        const isTool = s.kind==='tool';
        return '<tr><td class="mono">'+String(s.i).padStart(2,'0')+'</td>'
          + '<td style="color:var(--text3)">'+esc(s.phase||'')+'</td>'
          + '<td>'+(isTool?'<span class="tag cyan sm">'+esc(s.tool)+'</span>'
                :'<i class="dotm" style="background:'+modelColor(s.model)+'"></i>'+esc(M[s.model]?M[s.model].short:s.model||'—'))+'</td>'
          + '<td class="num">'+(isTool?'—':(hasTok?fmt(s.inTok):'—'))+'</td>'
          + '<td class="num">'+(isTool?'—':(hasTok?fmt(s.outTok):'—'))+'</td>'
          + '<td class="num">'+(isTool?'0':fmt1(s.credits||0))+'</td>'
          + '<td class="num">'+((s.ms||0)>0?((s.ms/1000).toFixed(1)+'s'):'—')+'</td>'
          + '<td class="num" style="color:'+(s.retry?'var(--bad-11)':'var(--text3)')+'">'+(s.retry||'—')+'</td></tr>';
      }).join('')
    + '</tbody></table></div>';

  /* 对账提示 */
  const rows = (BUDGET.reconcile && BUDGET.reconcile.rows) || [];
  const hit = rows.find(r=>r.taskId===t.id);
  if (hit){
    h += '<div class="hint'+(Math.abs(hit.diff)>0.5?' warn':'')+'" style="margin-top:10px">'
      + '本任务对账：日志 <b>'+fmt1(hit.logs)+'</b> vs '+(BUDGET.reconcile.label||'第二来源').split(' vs ')[1]
      + ' <b>'+fmt1(hit.second)+'</b>，差异 <b>'+hit.diff+'</b> 积分'
      + (Math.abs(hit.diff)>0.5?'（超过 0.5 积分，建议在「积分对账 · 日终对账」中核对）':'（在容差内）')
      + '。</div>';
  }
  /* 16 位交易流水号：把「号 ↔ 任务ID ↔ 积分」三个键摆在一起，便于对外核对 */
  if (t.txn || t.id){
    h += '<div class="dr-txn">'
      + '<div class="row"><span>交易流水号</span><b class="mono">'+esc(t.txn||'未登记')+'</b></div>'
      + '<div class="row"><span>任务 ID</span><b class="mono">'+esc(t.id||'—')+'</b></div>'
      + '<div class="row"><span>本任务积分</span><b>'+fmt1(t.credits)+'</b></div>'
      + '<div class="row"><span>累计耗时</span><b>'+t.duration+'s</b></div>'
      + '<div class="note">流水号 = 8 位日期 + 6 位任务名小写 + 2 位序列号，'
      + '由本地 txn.py 在采集时分配并注册，同一任务重跑不变号。</div>'
      + '</div>';
  }
  return h;
}

function openDrawer(taskId){
  const t = TASKS.find(x=>x.id===taskId);
  if (!t) return;
  DR.task = taskId;
  DR.filter = 'all';
  $('#drTitle').textContent = t.name;
  /* 流水号与任务 ID 放在副标题最前：对账时先认这两个键 */
  $('#drSub').textContent = [t.txn ? ('流水号 ' + t.txn) : '', '任务ID ' + t.id,
    t.type, t.level, t.trigger+'触发',
    t.project||'未绑定项目', t.ts].filter(Boolean).join(' · ');
  const h = '<div class="tabbar" id="drTabs">'
    + '<button data-t="chain" class="'+(DR.tab==='chain'?'on':'')+'">执行链路</button>'
    + '<button data-t="detail" class="'+(DR.tab==='detail'?'on':'')+'">消耗明细</button>'
    + '<span class="tabbar-note">'+fmt1(t.credits)+' 积分 · '+t.steps.length+' 步</span>'
    + '</div><div id="drPane"></div>';
  $('#drBody').innerHTML = h;
  $('#drPane').innerHTML = DR.tab==='chain' ? drawerChain(t) : drawerDetail(t);
  applyTableFit($('#drPane'));
  $('#mask').classList.add('on');
  $('#drawer').classList.add('on');
  /* 过滤 chip 在 #drPane 里、页签在 #drTabs 里，两者是兄弟节点：
     委托必须挂在稳定的 #drBody 上，否则点过滤 chip 不生效 */
  const body = document.getElementById('drBody');
  if (body && !body.__bound){
    body.addEventListener('click', e=>{
      const seg = e.target.closest('button[data-step]');
      if (seg){
        DR.filter = seg.dataset.step;
        const task = TASKS.find(x=>x.id===DR.task); if(!task) return;
        $('#drPane').innerHTML = drawerChain(task);
        applyTableFit($('#drPane'));
        return;
      }
      const b = e.target.closest('button[data-t]'); if(!b) return;
      DR.tab = b.dataset.t;
      const task = TASKS.find(x=>x.id===DR.task); if(!task) return;
      $$('#drTabs button').forEach(x=>x.classList.toggle('on', x===b));
      $('#drPane').innerHTML = DR.tab==='chain' ? drawerChain(task) : drawerDetail(task);
      applyTableFit($('#drPane'));
    });
    body.__bound = true;
  }
}
function closeDrawer(){
  $('#mask').classList.remove('on');
  $('#drawer').classList.remove('on');
}
$('#mask').addEventListener('click', closeDrawer);
$('#drClose').addEventListener('click', closeDrawer);
document.addEventListener('keydown', e=>{ if(e.key==='Escape') closeDrawer(); });

/* ============ 导出提示 ============ */
$('#btnExport').addEventListener('click', ()=>{
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = '导出说明：报告为单文件离线 HTML，数据已内嵌；需要 CSV 请在生成时加 --format all。';
  t.classList.add('on');
  clearTimeout(window.__tt);
  window.__tt = setTimeout(()=>t.classList.remove('on'), 3200);
});

/* ============ 缩放 ============ */
let rz;
window.addEventListener('resize', ()=>{
  clearTimeout(rz);
  rz = setTimeout(()=>{
    Object.values(CH).forEach(c=>{ try{c.resize();}catch(e){} });
    /* 窗口尺寸变了，容器宽度也变了，列宽要按新宽度重算 */
    const cur = document.querySelector('.view.on');
    if(cur) applyTableFit(cur);
  }, 120);
});
