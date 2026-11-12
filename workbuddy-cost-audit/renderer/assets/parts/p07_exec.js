/* ================= 二级页面：执行监测（审计控制） =================
   读者关心两件事：① 哪些执行**出了岔子**（错过、中断、失败）；
   ② 定时任务这一条链路**是否按点跑**。所以异常单独一块置顶，
   而不是让人在长表里自己捞。 */
const MONF = {cat:'all', page:1};

const CAT_COLOR = {scheduled:'var(--warn-9)', background:'var(--iris-11)', skill:'var(--ok-11)',
                   hidden:'var(--bad-9)', visible:'var(--info-9)'};
const CAT_LABEL = {scheduled:'定时任务执行', background:'后台自动化',
                   skill:'技能 / 子代理链', hidden:'隐藏调用', visible:'人工常规'};

function monCat(id){
  return (MON.categories||[]).find(c=>c.id===id) || {count:0,credits:0,share:0,avg:0};
}

const ABN_KIND = {missed:['错过计划时间','amber'], interrupted:['执行中断','red'],
                  failed:['执行失败','red']};
/* 异常来源两处：automation_runs 的 kind（错过/中断）+ 任务本身的状态（失败/部分交付）。
   两者语义不同，所以「说明」列保留原始字段值，不做美化翻译——审计要能看到原话。 */
function abnList(){
  const out = [];
  (MON.autoRuns||[]).forEach(r=>{
    if (!r.kind || !ABN_KIND[r.kind]) return;
    out.push({ts:r.ts || r.missedAt, name:r.name || '—', kind:r.kind,
      note:[r.status, r.resultState, r.sessionOutcome].filter(Boolean).join(' / '), credits:null});
  });
  (MON.records||[]).forEach(r=>{
    if (!r.status || r.status === '成功') return;
    out.push({ts:r.ts, name:r.name || '—', kind:'failed',
      note:'任务状态：' + r.status, credits:r.credits, taskId:r.taskId});
  });
  return out.sort((a,b)=>String(b.ts||'').localeCompare(String(a.ts||'')));
}

function abnHTML(){
  const rows = abnList();
  const cfgN = (MON.configs||[]).length;
  const runs = (MON.autoRuns||[]).length;
  const T = MON.totals || {};
  let h = '<div class="card abn-card"><div class="card-hd">'
    + '<div class="card-title">异常执行任务</div>'
    + '<div class="card-sub">定时任务 '+cfgN+' 个 · 执行记录 '+runs+' 次 · 补跑 '
      + fmt0(T.missed||0)+' · 中断 '+fmt0(T.interrupted||0)+'</div></div>';
  if (!rows.length){
    return h + '<div class="abn-ok">窗口内没有错过、中断或失败的执行</div></div>';
  }
  h += '<div class="tbl-wrap abn-wrap"><table class="tbl dense"><thead><tr>'
    + '<th>时间</th><th style="width:34%">任务</th><th>异常类型</th><th>说明</th>'
    + '<th style="text-align:right">积分</th></tr></thead><tbody>'
    + rows.slice(0, 6).map(r=>{
        const k = ABN_KIND[r.kind] || ['异常','amber'];
        return '<tr' + (r.taskId ? ' data-id="'+esc(r.taskId)+'"' : '') + '>'
          + '<td class="mono" style="font-size:10.5px">'+esc(String(r.ts||'').slice(5,16))+'</td>'
          + '<td class="name" title="'+esc(r.name)+'">'+esc(r.name)+'</td>'
          + '<td><span class="tag '+k[1]+' sm">'+k[0]+'</span></td>'
          + '<td style="color:var(--text3);font-size:10.5px">'+esc(r.note||'—')+'</td>'
          + '<td class="num">'+(r.credits==null ? '—' : fmt1(r.credits))+'</td></tr>';
      }).join('')
    + '</tbody></table></div>'
    + (rows.length > 6 ? '<div class="abn-more">另有 '+(rows.length-6)+' 条，见下方执行记录</div>' : '')
    + '</div>';
  return h;
}

/* ---------- 执行明细：一行一个「这一次执行到底调了什么」 ----------
   执行记录表只能放 9 列，放不下的三件事在弹窗里展开：
     1) 概览：谁触发、什么模型、几步、多少积分；
     2) 技能 / 子代理链：具体调了哪个能力、几次 —— 日志里记到载体
        （Skill / DeferExecuteTool / mcp__xxx）这一级，有具体名字就照实显示；
     3) 隐藏调用：能按会话归属的就列出来，不能归属的说清楚为什么不能。 */
const SKILL_LABEL = {
  Skill:'技能调用', DeferExecuteTool:'技能执行器', Agent:'子代理派发',
  Task:'子代理任务', TaskOutput:'子代理输出'
};
function isSkillTool(n){
  return !!SKILL_LABEL[n] || /^mcp__/.test(n || '');
}
function execSkills(t){
  const m = {}, order = [];
  ((t && t.steps) || []).forEach(s=>{
    if (s.kind !== 'tool' || !isSkillTool(s.tool)) return;
    const k = s.tool;
    if (!m[k]){ m[k] = {name:k, label:SKILL_LABEL[k] || 'MCP 工具', n:0, credits:0}; order.push(k); }
    m[k].n++; m[k].credits += s.credits || 0;
  });
  return order.map(k=>m[k]).sort((a,b)=>b.n-a.n);
}
/* 隐藏调用按会话归属：客户端上报了 session 才归得上，没上报就如实说没有 */
function execHidden(t){
  const sid = t && t.session ? String(t.session).slice(0,8) : '';
  const all = MON.hidden || [];
  if (!sid) return {sid:'', rows:[], canLink:false};
  const rows = all.filter(h=>h.session && h.session === sid);
  return {sid:sid, rows:rows, canLink:true};
}

function openExecDetail(taskId){
  const r = (MON.records || []).filter(x=>x.taskId === taskId)[0];
  const t = TASKS.filter(x=>x.id === taskId)[0];
  if (!r) return;
  const stTag = r.status === '成功' ? 'green' : r.status === '失败' ? 'red' : 'amber';
  const kv = (k, v, cls) => '<div class="ev-c"><span>'+esc(k)+'</span><b'+(cls?' style="'+cls+'"':'')+'>'+v+'</b></div>';

  let h = '<div class="sec-t">概览</div><div class="ev-grid">'
    + kv('执行时间', esc(String(r.ts||'').slice(5,16)))
    + kv('类别', esc(CAT_LABEL[r.cat] || r.cat))
    + kv('触发方式', esc(r.trigger||'—'))
    + kv('主模型', esc(M[r.model] ? M[r.model].short : (r.model||'—')))
    + kv('步数', fmt0(r.steps||0)+' 步')
    + kv('积分', fmt1(r.credits||0)+' 积分')
    + kv('时长', fmt0(r.duration||0)+'s')
    + kv('状态', '<span class="tag '+stTag+' sm">'+esc(r.status||'—')+'</span>')
    + '</div>';

  /* ---- 技能 / 子代理链 ---- */
  const sk = execSkills(t);
  h += '<div class="sec-t" style="margin-top:14px">技能 / 子代理链</div>';
  if (!sk.length){
    h += '<div class="cfg-note" style="margin:0">这次执行没有调用 Skill / MCP / 子代理能力，'
      + '（'+(r.cat === 'skill' ? '归类为技能链是依据会话标记，' : '')+'步骤里只有常规工具）。</div>';
  } else {
    const generic = sk.filter(s=>['Skill','DeferExecuteTool'].indexOf(s.name) >= 0);
    h += '<div class="ex-sk">'
      + sk.map(s=>'<div class="ex-sk-i">'
          + '<b class="mono">'+esc(s.name)+'</b>'
          + '<span class="tag '+(s.name==='Agent'||s.name==='Task'?'purple':'cyan')+' sm">'+esc(s.label)+'</span>'
          + '<em>'+s.n+' 次 · '+fmt1(s.credits)+' 积分</em>'
        + '</div>').join('')
      + '</div>';
    if (generic.length){
      h += '<div class="cfg-note" style="margin:6px 0 0">'
        + '<b>子技能名未上报。</b>日志只记到调用载体（'
        + generic.map(s=>s.name).join(' / ')
        + '）这一级，具体被调起的是哪个技能、哪个子代理没有落库；'
        + '能确认的是调了 '+generic.reduce((a,b)=>a+b.n,0)+' 次，'
        + '金额按载体内部的模型步骤结算。要看逐步链路请点右下角「完整执行链路」。</div>';
    }
  }

  /* ---- 隐藏调用 ---- */
  const hid = execHidden(t);
  h += '<div class="sec-t" style="margin-top:14px">隐藏调用明细</div>';
  if (hid.rows.length){
    h += '<div class="tbl-wrap"><table class="tbl dense"><thead><tr>'
      + '<th>时间</th><th>类型</th><th style="text-align:right">in / out</th>'
      + '<th style="text-align:right">折算积分</th></tr></thead><tbody>'
      + hid.rows.map(x=>'<tr>'
          + '<td class="mono" style="font-size:10.5px">'+esc(String(x.ts||'').slice(5,16))+'</td>'
          + '<td>'+esc(x.name||x.rtype||'')+(x.est?' <span class="tag amber sm">折算</span>':'')+'</td>'
          + '<td class="num" style="font-size:10px;color:var(--text3)">'+fmt(x.inTok)+' / '+fmt(x.outTok)+'</td>'
          + '<td class="num">'+fmt1(x.credits)+'</td></tr>').join('')
      + '</tbody></table></div>';
  } else {
    h += '<div class="cfg-note" style="margin:0">'
      + '<b>这次执行名下没有可归属的隐藏调用。</b>'
      + (hid.canLink
          ? '会话 <span class="mono">'+esc(hid.sid)+'</span> 内未匹配到话题生成 / 上下文压缩记录。'
          : '话题生成、上下文压缩这类请求不挂在任务上，客户端也未上报会话 ID，'
            + '因此无法逐条归属到某一次执行——只能看窗口内的总量。')
      + ' 窗口内共 <b>'+fmt0((MON.totals||{}).hiddenCalls||0)+' 次</b>、约 <b>'
      + fmt0((MON.totals||{}).hiddenCredits||0)+' 积分</b>，</div>'
      + '<div class="ctl-bar" style="margin-top:6px">'
      + '<button class="btn sm" id="exGoHidden">查看全部隐藏调用明细</button>'
      + '<span class="ctl-hint">切到「隐藏调用明细」页签逐条看</span></div>';
  }

  ctlModalHead('执行明细 · ' + (r.name || taskId),
    '任务ID <b class="mono">'+esc(taskId)+'</b> · '+esc(CAT_LABEL[r.cat]||r.cat)
    + ' · '+esc(String(r.ts||'').slice(0,16)));
  document.getElementById('cfgBody').innerHTML = h;
  document.getElementById('cfgFoot').innerHTML =
      '<button class="btn" id="exChain">完整执行链路</button>'
    + '<span class="ml-auto" style="color:var(--text3);font-size:10.5px">'
      + '明细只读，不改账</span>'
    + '<button class="btn pri" id="exClose">关闭</button>';
  ctlOpenModal();
  const foot = document.getElementById('cfgFoot');
  if (!foot.__exbound){
    foot.__exbound = true;
    foot.addEventListener('click', e=>{
      const id = e.target.id;
      if (id === 'exClose'){ ctlCloseModal(); return; }
      if (id === 'exChain'){
        ctlCloseModal();
        openDrawer(document.getElementById('cfgBody').__exTask || taskId);
        return;
      }
    });
  }
  document.getElementById('cfgBody').__exTask = taskId;
  const bh = document.getElementById('cfgBody');
  if (!bh.__exbound){
    bh.__exbound = true;
    bh.addEventListener('click', e=>{
      if (e.target.id === 'exGoHidden'){
        ctlCloseModal();
        MONF.cat = 'hidden'; MONF.page = 1; buildExec();
      }
    });
  }
}

function openHiddenDetail(idx){
  const x = (MON.hidden || [])[idx];
  if (!x) return;
  const kv = (k, v) => '<div class="ev-c"><span>'+esc(k)+'</span><b>'+v+'</b></div>';
  const h = '<div class="ev-grid">'
    + kv('调用时间', esc(String(x.ts||'').slice(0,16)))
    + kv('类型', esc(x.name || x.rtype || '—'))
    + kv('rtype', '<span class="mono" style="font-size:10.5px">'+esc(x.rtype||'—')+'</span>')
    + kv('模型', esc(M[x.model] ? M[x.model].short : (x.model||'—')))
    + kv('输入 token', fmt(x.inTok||0))
    + kv('输出 token', fmt(x.outTok||0))
    + kv('折算积分', fmt1(x.credits||0) + (x.est ? ' <span class="tag amber sm">折算</span>' : ''))
    + kv('耗时', fmt1(x.duration||0)+'s')
    + '</div>'
    + '<div class="cfg-note" style="margin-top:10px">'
    + (x.est
        ? '这类请求客户端通常不上报积分，金额按本机实测每千 token 折算率估算，'
          + '不是账单值；对账时以账单为准。'
        : '该条积分来自客户端上报值。')
    + ' 它不产生任务、不进会话列表，但按同样费率扣积分。</div>';
  ctlModalHead('隐藏调用明细', '用户无感 · 不产生任务 · 按折算率估算');
  document.getElementById('cfgBody').innerHTML = h;
  document.getElementById('cfgFoot').innerHTML =
      '<span class="ml-auto"></span><button class="btn pri" id="exClose2">关闭</button>';
  ctlOpenModal();
  const f = document.getElementById('cfgFoot');
  if (!f.__hdbound){
    f.__hdbound = true;
    f.addEventListener('click', e=>{ if (e.target.id === 'exClose2') ctlCloseModal(); });
  }
}

/* ---------- 执行记录：分页 + 五类页签 ---------- */
function execRecordsHTML(){
  const records = MON.records || [];
  const cats = ['all','scheduled','background','skill','hidden'];
  const label = id => id === 'all' ? '全部'
    : id === 'hidden' ? '隐藏调用明细' : (CAT_LABEL[id] || id);
  const cnt = id => id === 'all' ? records.length
    : id === 'hidden' ? (MON.hidden||[]).length : monCat(id).count;

  let h = '<div class="card exec-rec"><div class="card-hd">'
    + '<div class="card-title">执行记录</div>'
    + '<div class="card-sub">窗口内不可见执行的逐条明细 · 每页 '+PG_SIZE+' 行 · 点行看明细</div></div>'
    + '<div class="flex exec-tabs">'
    + cats.map(id=>'<button class="chip'+(MONF.cat===id?' on':'')+'" data-cat="'+id+'">'
        + esc(label(id))+' <b>'+fmt0(cnt(id))+'</b></button>').join('')
    + '<span class="ml-auto exec-tip">'
    + (MONF.cat === 'hidden'
        ? '隐藏调用不产生任务，按调用逐条列'
        : '技能 / 子代理链的行内点开有具体能力与隐藏调用')
    + '</span></div>';

  if (MONF.cat === 'hidden'){
    const rows = MON.hidden || [];
    const page = pgSlice(rows, MONF.page, PG_SIZE);
    h += '<div class="tbl-wrap exec-tbl"><table class="tbl dense"><thead><tr>'
      + '<th style="width:84px">时间</th><th style="width:110px">类型</th><th>说明</th>'
      + '<th style="width:74px">会话</th><th style="text-align:right">in / out</th>'
      + '<th style="text-align:right;width:92px">折算积分</th>'
      + '</tr></thead><tbody>';
    if (!rows.length){
      h += '<tr><td colspan="6" class="exec-empty">窗口内没有话题生成 / 上下文压缩类调用</td></tr>';
    }
    page.rows.forEach((x, i)=>{
      const idx = (page.page-1)*page.size + i;
      h += '<tr class="hl" data-exh="'+idx+'">'
        + '<td class="mono" style="font-size:10.5px;color:var(--text2)">'+esc(String(x.ts||'').slice(5,16))+'</td>'
        + '<td><span class="tag red sm">'+esc(x.rtype||'—')+'</span></td>'
        + '<td class="name" title="'+esc(x.name)+'">'+esc(x.name)
          + (x.est?' <span class="tag amber sm">折算</span>':'')+'</td>'
        + '<td class="mono" style="font-size:10px;color:var(--text3)">'+esc(x.session||'—')+'</td>'
        + '<td class="num" style="font-size:10px;color:var(--text3)">'+fmt(x.inTok)+' / '+fmt(x.outTok)+'</td>'
        + '<td class="num">'+fmt1(x.credits)+'</td>'
        + '</tr>';
    });
    h += '</tbody></table></div>' + pagerHTML(page, 'exp');
  } else {
    const rows = records.filter(r=>MONF.cat === 'all' || r.cat === MONF.cat);
    const page = pgSlice(rows, MONF.page, PG_SIZE);
    h += '<div class="tbl-wrap exec-tbl"><table class="tbl dense"><thead><tr>'
      + '<th style="width:84px">时间</th><th style="width:104px">类别</th><th>任务</th>'
      + '<th style="width:74px">触发</th><th style="width:78px">类型</th>'
      + '<th style="text-align:right;width:54px">步数</th><th style="text-align:right;width:70px">积分</th>'
      + '<th style="text-align:right;width:62px">时长</th><th style="width:72px">状态</th>'
      + '</tr></thead><tbody>';
    if (!rows.length){
      h += '<tr><td colspan="9" class="exec-empty">该类别窗口内没有执行记录</td></tr>';
    }
    page.rows.forEach(r=>{
      h += '<tr class="hl" data-exr="'+esc(r.taskId)+'">'
        + '<td class="mono" style="font-size:10.5px;color:var(--text2)">'+esc((r.ts||'').slice(5,16))+'</td>'
        + '<td><span class="tag sm" style="background:'+CAT_COLOR[r.cat]+'22;color:'+CAT_COLOR[r.cat]
          +';border-color:'+CAT_COLOR[r.cat]+'44">'+esc(CAT_LABEL[r.cat]||r.cat)+'</span></td>'
        + '<td class="name" title="'+esc(r.name)+'">'+esc(r.name)+'</td>'
        + '<td><span class="tag '+(LEDGER_TRIG_TAG[r.trigger]||'gray')+' sm">'+esc(r.trigger)+'</span></td>'
        + '<td style="color:var(--text2);font-size:10.5px">'+esc(r.type||r.rtype||'—')+'</td>'
        + '<td class="num">'+(r.steps||0)+'</td>'
        + '<td class="num">'+fmt1(r.credits||0)+'</td>'
        + '<td class="num">'+(r.duration||0)+'s</td>'
        + '<td><span class="tag '+(r.status==='成功'?'green':r.status==='失败'?'red':'amber')+' sm">'+esc(r.status)+'</span></td>'
        + '</tr>';
    });
    h += '</tbody></table></div>' + pagerHTML(page, 'exp');
  }
  return h + '</div>';
}

function buildExec(){
  const T = MON.totals || {};
  const records = MON.records || [];
  const cfgs = MON.configs || [];

  /* 横幅 */
  const share = T.unseenShare || 0;
  const lvl = share >= 0.3 ? 'yellow' : (T.unseenTasks ? 'gray' : 'green');
  let h = '<div class="banner '+lvl+'">'
    + '<div class="bi">'+(lvl==='green'?'✓':'i')+'</div>'
    + '<div style="min-width:0"><div class="bt">'
    + (T.unseenTasks ? ('窗口内 '+T.unseenTasks+' 次执行属于后台 / 技能 / 隐藏链路，合计 '+fmt0(T.unseenCredits)+' 积分（占 '+pct1(share)+'）')
                     : '窗口内没有后台 / 技能 / 隐藏链路执行')
    + '</div><div class="bd">'
    + '监测范围：定时任务执行记录、后台自动化会话、技能 / MCP / 子代理调用、话题生成与上下文压缩等隐藏调用。'
    + (T.missed ? ('其中 '+T.missed+' 次是<b>错过计划时间后补跑</b>。') : '')
    + '</div></div>'
    + '<span class="tag gray sm bg">四类互斥归类</span>'
    + '</div>';

  /* ---------- 第一行：四个统计指标（左）+ 定时任务配置（右） ----------
     左右等高：外层 grid 拉伸，两个子块各自 flex 列，内容在块内垂直居中。
     指标放 2×2 而不是 4×1——配置卡的高度由任务条数决定，
     四张细长的指标卡拉成那个高度会显得空。 */
  const cfgEntry = '<div class="cfg-entry">'
    + '<span>下表是<b>窗口内已在跑</b>的定时任务与实测消耗；'
    + '要让本报告的监测数据<b>自动更新</b>，还需新增一条定时重跑采集的任务。</span>'
    + '<button class="btn sm pri" id="btnAutoCfg" style="flex:0 0 auto">任务配置</button>'
    + '</div>';

  const cfgTable = cfgs.length
    ? '<div class="cfg-list">' + cfgs.map(c=>
        '<div class="cfg-item">'
        + '<div class="cfg-i-main"><b title="'+esc(c.name)+'">'+esc(c.name)+'</b>'
          + '<span>'+esc(c.freq)+' · '
            +'<i class="dotm" style="background:'+modelColor(c.model)+'"></i>'
            +esc(M[c.model]?M[c.model].short:(c.model||'—'))
            +' · <span class="tag '+(c.status==='ACTIVE'||c.status==='active'?'green':'gray')+' sm">'
            +esc(c.status)+'</span></span></div>'
        + '<div class="cfg-i-last" title="'+esc(c.lastRun||'')+'">'+esc(c.lastRun||'—')+'</div>'
        + '<div class="cfg-i-num">'+ (c.runs||0) +' 次 · '+fmt0(c.credits||0)+' 积分'
          +'<span>均值 '+fmt0(c.avg||0)+'</span></div>'
        + '</div>').join('') + '</div>'
    : '<div class="hint">本机没有配置定时任务，或数据库不可读。</div>';

  h += '<div class="exec-top">'
    + '<div class="exec-kpis">'
      + kpiMini('执行记录', fmt0(records.length), '次', 'var(--info-9)', '窗口内逐条可下钻')
      + kpiMini('后台 / 技能 / 隐藏', fmt0(T.unseenTasks||0), '次', 'var(--warn-9)',
          '任务侧共 '+fmt0(T.tasks||0)+' 个')
      + kpiMini('其中消耗', fmt0(T.unseenCredits||0), '积分', 'var(--bad-9)',
          '占含隐藏调用总消耗 '+pct1(share))
      + kpiMini('隐藏调用（折算）', fmt0(T.hiddenCalls||0), '次', 'var(--bad-9)',
          '话题生成 / 压缩 · 约 '+fmt0(T.hiddenCredits||0)+' 积分')
    + '</div>'
    + '<div class="card mon-card exec-cfg">'
      + '<div class="card-hd"><div class="card-title">定时任务配置</div>'
        + '<div class="card-sub">来自 automations 表 · 与左侧指标同高</div></div>'
      + cfgEntry + cfgTable
    + '</div>'
    + '</div>';

  /* ---------- 异常执行置顶：出岔子的先看 ---------- */
  h += abnHTML();

  /* ---------- 执行记录（含隐藏调用明细页签 + 分页） ---------- */
  h += execRecordsHTML();

  /* 说明 */
  h += '<div class="notes-line"><div style="font-size:10.5px;color:var(--text3);line-height:1.7">'
    + (MON.notes||[]).map(n=>'<div>· '+esc(n)+'</div>').join('')
    + '</div></div>';

  const v = document.getElementById('v-control-exec');
  v.innerHTML = h;

  /* 事件：页签 / 翻页 / 行下钻 / 定时任务配置 */
  if (!v.__bound){
    v.addEventListener('click', e=>{
      if (e.target.id === 'btnAutoCfg'){ openAutoCfg(); return; }
      const c = e.target.closest('[data-cat]');
      if (c){ MONF.cat = c.dataset.cat; MONF.page = 1; buildExec(); return; }
      const p = e.target.closest('[data-exp]');
      if (p){
        if (p.disabled) return;
        MONF.page = Math.max(1, +p.dataset.exp);
        buildExec(); return;
      }
      const hr = e.target.closest('[data-exh]');
      if (hr){ openHiddenDetail(+hr.dataset.exh); return; }
      const tr = e.target.closest('[data-exr]');
      if (tr){ openExecDetail(tr.dataset.exr); return; }
    });
    v.__bound = true;
  }
}

/* ============ 定时更新监测数据 · 任务配置 ============ */
const AUTO_KEY = 'wbc.autoupdate.v1';
const AUTO_DEFAULT = {on:true, freq:'daily', hour:9, minute:0, weekday:1,
                      out:'out', format:'all', os:'win',
                      script:'', scriptCustom:false, scriptPath:''};
let AUTO_CFG = null;

function loadAutoCfg(){
  if (AUTO_CFG) return AUTO_CFG;
  try {
    const raw = localStorage.getItem(AUTO_KEY);
    if (raw) AUTO_CFG = Object.assign({}, AUTO_DEFAULT, JSON.parse(raw));
  } catch(e){}
  if (!AUTO_CFG) AUTO_CFG = Object.assign({}, AUTO_DEFAULT);
  return AUTO_CFG;
}
function saveAutoCfg(c){
  AUTO_CFG = Object.assign({}, AUTO_DEFAULT, c || {});
  try { localStorage.setItem(AUTO_KEY, JSON.stringify(AUTO_CFG)); } catch(e){}
  return AUTO_CFG;
}

const WEEK_CN = ['周一','周二','周三','周四','周五','周六','周日'];
const WEEK_EN = ['MON','TUE','WED','THU','FRI','SAT','SUN'];

/* ---------- 任务执行脚本：模板 / 编辑 / 导入 ---------- */
/* 关键点：脚本一旦落盘，系统计划任务每次只执行这个本地脚本，
   不再需要每次唤醒 WorkBuddy 或重新生成任务脚本 —— 触发时零积分消耗。 */
function defaultScriptText(c){
  const dir = '<项目目录>';
  const out = c.out || 'out';
  const fmt = c.format || 'all';
  if (c.os === 'win'){
    return [
      '@echo off',
      'REM WorkBuddy 积分审计 · 定时重跑采集（只读、离线、不消耗积分）',
      'REM 由「任务监测 → 定时任务配置 → 任务配置」生成，可直接编辑',
      'setlocal',
      'set "PROJECT=' + dir + '"',
      'set "OUT=' + out + '"',
      'cd /d "%PROJECT%"',
      'REM 复用统一的刷新入口：跑完自动写 out\\refresh.log 与 out\\last-refresh.json',
      'python -m hosts.commands.refresh_report --out "%OUT%" --format ' + fmt
        + ' --yes >> "%PROJECT%\\out\\refresh.log" 2>&1',
      'endlocal'
    ].join('\r\n');
  }
  return [
    '#!/bin/sh',
    '# WorkBuddy 积分审计 · 定时重跑采集（只读、离线、不消耗积分）',
    '# 由「任务监测 → 定时任务配置 → 任务配置」生成，可直接编辑',
    'PROJECT="' + dir + '"',
    'OUT="' + out + '"',
    'cd "$PROJECT" || exit 1',
    '# 复用统一的刷新入口：跑完自动写 out/refresh.log 与 out/last-refresh.json',
    'python3 -m hosts.commands.refresh_report --out "$OUT" --format ' + fmt
      + ' --yes >> "$PROJECT/out/refresh.log" 2>&1'
  ].join('\n');
}
function scriptFileName(c){
  return c.os === 'win' ? 'refresh-cost-audit.bat' : 'refresh-cost-audit.sh';
}
function defaultScriptPath(c){
  return c.os === 'win'
    ? '%USERPROFILE%\\.workbuddy\\refresh-cost-audit.bat'
    : '~/.workbuddy/refresh-cost-audit.sh';
}
/* 脚本路径按目标系统给默认建议；已由用户改成自定义的路径要原样保留 */
function resolveScriptPath(c){
  const cur = (c.scriptPath || '').trim();
  const dft = defaultScriptPath(c);
  if(!cur || cur === defaultScriptPath(AUTO_DEFAULT)
      || cur === '%USERPROFILE%\\.workbuddy\\refresh-cost-audit.bat'
      || cur === '~/.workbuddy/refresh-cost-audit.sh') return dft;
  return cur;
}
function currentScript(c){
  /* 用户没有自定义过就始终用模板，模板会随「产物目录 / 输出格式 / 目标系统」联动 */
  if(c.scriptCustom && (c.script||'').trim()) return c.script;
  return defaultScriptText(c);
}
/* 系统计划任务直接调用本地脚本：触发时不需要 WorkBuddy 在线 */
function autoCmdText(c, kind){
  const path = resolveScriptPath(c);
  const hh = String(c.hour).padStart(2, '0') + ':' + String(c.minute).padStart(2, '0');
  if (kind === 'cmd') return 'python "<项目目录>/hosts/commands/refresh_report.py" --out ' + c.out;
  if (c.os === 'win'){
    let sc = '/SC DAILY /ST ' + hh;
    if (c.freq === 'hourly') sc = '/SC HOURLY';
    else if (c.freq === 'weekly') sc = '/SC WEEKLY /D ' + WEEK_EN[c.weekday || 0] + ' /ST ' + hh;
    /* /TR 的值整体用双引号包住，内部的路径引号要转义成 \" */
    const tr = ('cmd /c "' + path + '"').replace(/"/g, '\\"');
    return 'schtasks /Create /TN "WorkBuddyCostAudit" /TR "' + tr + '" ' + sc + ' /F';
  }
  const whenCn = c.freq === 'hourly' ? '每小时' : c.freq === 'weekly' ? '每周' : '每天';
  let cron;
  if (c.freq === 'hourly') cron = '0 * * * *';
  else if (c.freq === 'weekly') cron = c.minute + ' ' + c.hour + ' * * ' + ((c.weekday || 0) + 1);
  else cron = c.minute + ' ' + c.hour + ' * * *';
  return '# ' + whenCn + '重跑一次采集（crontab -e 追加一行；脚本路径按需改成绝对路径）\n'
    + cron + ' /bin/sh "' + path + '"';
}

function autoPromptText(c){
  const hh = String(c.hour).padStart(2, '0') + ':' + String(c.minute).padStart(2, '0');
  const when = c.freq === 'hourly' ? '每小时'
    : c.freq === 'weekly' ? ('每' + WEEK_CN[c.weekday || 0] + ' ' + hh + ' ') : ('每天 ' + hh + ' ');
  const path = resolveScriptPath(c);
  return '请帮我创建一个' + when + '执行的定时任务：执行本机脚本 ' + path
    + '（脚本已写好并落盘，内容是重跑 workbuddy-cost-audit 的采集脚本，'
    + '只读采集、不联网、不消耗积分）。执行后请把本次的「当前余额、今日消耗、预警比、告警级别」告诉我。';
}

function autoCfgHTML(){
  const c = loadAutoCfg();
  const seg = (key, opts, cur)=>'<div class="cfg-seg" data-seg="'+key+'">'
    + opts.map(([v,t])=>'<button data-v="'+v+'" class="'+(cur===v?'on':'')+'">'+t+'</button>').join('')
    + '</div>';
  const hourOpt = n => Array.from({length:24},(_,i)=>'<option value="'+i+'" '
    + (+n===i?'selected':'')+'>'+String(i).padStart(2,'0')+':00</option>').join('');
  const minOpt = n => [0,10,20,30,40,50].map(i=>'<option value="'+i+'" '
    + (+n===i?'selected':'')+'>'+String(i).padStart(2,'0')+':00</option>').join('');

  let h = '<div class="cfg-row"><div class="cfg-k">启用</div><div class="cfg-v">'
    + '<label class="cfg-sw"><input type="checkbox" id="acOn" '
    + (c.on?'checked':'')+'> 定时重跑采集，保持监测数据最新</label></div></div>';

  h += '<div class="cfg-row"><div class="cfg-k">频率</div><div class="cfg-v">'
    + seg('freq', [['hourly','每小时'],['daily','每天'],['weekly','每周']], c.freq)
    + '</div></div>';

  h += '<div class="cfg-row"><div class="cfg-k">执行时间</div><div class="cfg-v">'
    + (c.freq==='weekly'
        ? '<select id="acWeek">'+WEEK_CN.map((w,i)=>'<option value="'+i+'" '
            +((c.weekday||0)===i?'selected':'')+'>'+w+'</option>').join('')+'</select>' : '')
    + (c.freq==='hourly' ? '<span style="color:var(--text3)">每小时的第 0 分钟触发</span>'
        : '<select id="acHour">'+hourOpt(c.hour)+'</select>'
          + '<span style="color:var(--text3)">:</span>'
          + '<select id="acMin">'+minOpt(c.minute)+'</select>')
    + '</div></div>';

  h += '<div class="cfg-row"><div class="cfg-k">产物目录</div><div class="cfg-v">'
    + '<input class="cfg-txt" id="acOut" type="text" value="'+esc(c.out)+'" placeholder="out">'
    + '<span style="color:var(--text3)">报告与 CSV 输出到该目录</span></div></div>';

  h += '<div class="cfg-row"><div class="cfg-k">输出格式</div><div class="cfg-v">'
    + seg('format', [['all','全部'],['html','仅 HTML'],['json','仅 JSON']], c.format)
    + '</div></div>';

  h += '<div class="cfg-row"><div class="cfg-k">目标系统</div><div class="cfg-v">'
    + seg('os', [['win','Windows'],['unix','macOS / Linux']], c.os)
    + '</div></div>';

  /* ---------- 任务执行脚本：编辑 / 导入 / 保存 ---------- */
  const fname = scriptFileName(c);
  h += '<div class="sec-t" style="margin-top:14px">任务执行脚本</div>'
    + '<div class="cfg-note" style="margin:0 0 7px">'
    + '触发时<b>系统计划任务直接执行这个本地脚本</b>，脚本内部调用 <code>hosts/commands/refresh_report.py</code> '
    + '重跑采集并刷新报告 —— <b>不需要每次唤醒 WorkBuddy，也不需要重新生成脚本</b>，因此不消耗积分。'
    + '可在此直接编辑，或从本机导入已有脚本。</div>'
    + '<div class="cfg-row"><div class="cfg-k">脚本路径</div><div class="cfg-v">'
      + '<input class="cfg-txt" id="acScriptPath" type="text" value="'
        + esc(resolveScriptPath(c)) + '">'
      + '<button class="btn sm" id="acImport">导入脚本…</button>'
      + '<input type="file" id="acFile" accept=".bat,.cmd,.sh,.py,.txt" style="display:none">'
    + '</div></div>'
    + '<div class="cfg-editor">'
      + '<textarea id="acScript" spellcheck="false" rows="11">'
      + esc(currentScript(c)) + '</textarea>'
    + '</div>'
    + '<div class="cfg-row" style="margin-top:8px"><div class="cfg-k">操作</div><div class="cfg-v">'
      + '<button class="btn sm" id="acSaveScript">保存脚本到本机</button>'
      + '<button class="btn sm" id="acResetScript">恢复默认模板</button>'
      + '<button class="btn sm" id="acCopyScript">复制脚本</button>'
      + '<span class="ml-auto" style="color:var(--text3);font-size:10.5px" id="acScriptState">'
        + (c.scriptCustom ? '已自定义脚本' : '默认模板') + '</span>'
    + '</div></div>'
    + '<div class="cfg-note">「保存脚本到本机」会把上面的内容下载为 <code>' + fname + '</code>，'
    + '放到脚本路径指定的位置即可；系统计划任务只认这一个文件，之后改脚本也只需改这个文件。</div>';

  h += '<div class="sec-t" style="margin-top:12px">可执行命令</div>'
    + '<div class="cfg-cmd"><pre id="acCmd">'+esc(autoCmdText(c,'sch'))+'</pre></div>'
    + '<div class="cfg-note">把 <b>&lt;项目目录&gt;</b> 换成本机 workbuddy-cost-audit 的实际路径。'
    + 'Windows 用「以管理员身份」运行一次即可注册；'
    + (c.os==='unix'?'macOS / Linux 用 <code>crontab -e</code> 追加。':'')
    + '命令只做离线只读采集，不联网、不改客户端配置。</div>';

  h += '<div class="sec-t" style="margin-top:12px">交给 WorkBuddy 创建</div>'
    + '<div class="cfg-cmd"><pre id="acPrompt">'+esc(autoPromptText(c))+'</pre></div>'
    + '<div class="cfg-note">复制上面这段话，在 WorkBuddy 里发送即可创建对应的定时自动化；'
    + '这条路径只在<b>首次创建</b>时需要用一次，之后任务自己按脚本执行，不再消耗积分。</div>';

  h += '<div class="cfg-note" style="margin-top:10px">'
    + '<b>说明：</b>本页的配置只保存在这台机器的浏览器里，用于生成上面的命令与提示语；'
    + '它<b>不会</b>自己注册系统任务。报告是离线单文件，浏览器无权、也不会去改动系统调度。</div>';
  return h;
}

function openAutoCfg(){
  const body = document.getElementById('cfgBody');
  const foot = document.getElementById('cfgFoot');
  if (!body || !foot) return;
  body.innerHTML = autoCfgHTML();
  foot.innerHTML = '<button class="btn" id="acCopyCmd">复制命令</button>'
    + '<button class="btn" id="acCopyPrompt">复制提示语</button>'
    + '<span class="ml-auto" style="color:var(--text3);font-size:10.5px" id="acState">'
    + (loadAutoCfg().__saved ? '已保存' : '未保存') + '</span>'
    + '<button class="btn pri" id="acSave">保存配置</button>';
  document.getElementById('cfgMask').classList.add('on');
  document.getElementById('cfgModal').classList.add('on');
  bindAutoCfg();
}
function closeAutoCfg(){
  document.getElementById('cfgMask').classList.remove('on');
  document.getElementById('cfgModal').classList.remove('on');
}

/* 读取脚本编辑框内容（未改动时返回 null 表示沿用当前脚本） */
function scriptFromEditor(){
  const el = document.getElementById('acScript');
  return el ? el.value : '';
}
/* 保存脚本到本机：把编辑框内容下载成 .bat / .sh 文件 */
function downloadScript(){
  const c = loadAutoCfg();
  const text = scriptFromEditor();
  if (!text.trim()){ toast('脚本内容为空'); return; }
  const name = scriptFileName(c);
  try {
    const blob = new Blob([text], {type:'text/plain;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    setTimeout(()=>{ try{ URL.revokeObjectURL(url); }catch(e){} }, 1500);
    toast('已下载 ' + name + '，放到「脚本路径」指定位置即可');
  } catch(e){
    toast('下载失败，请用「复制脚本」手动保存');
  }
}

function bindAutoCfg(){
  const body = document.getElementById('cfgBody');
  if (!body || body.__bound) return;
  body.__bound = true;

  const g = id => document.getElementById(id);
  const refresh = ()=>{
    const c = loadAutoCfg();
    c.on = !!(g('acOn') && g('acOn').checked);
    c.out = (g('acOut') && g('acOut').value.trim()) || 'out';
    c.scriptPath = (g('acScriptPath') && g('acScriptPath').value.trim()) || defaultScriptPath(c);
    if (g('acHour')) c.hour = +g('acHour').value;
    if (g('acMin')) c.minute = +g('acMin').value;
    if (g('acWeek')) c.weekday = +g('acWeek').value;
    AUTO_CFG = Object.assign({}, AUTO_DEFAULT, c);
    if (g('acCmd')) g('acCmd').textContent = autoCmdText(AUTO_CFG, 'sch');
    if (g('acPrompt')) g('acPrompt').textContent = autoPromptText(AUTO_CFG);  };
  /* 脚本被编辑/导入后才算「自定义」，此前一律跟模板联动 */
  const markCustom = ()=>{
    const c = loadAutoCfg();
    c.script = scriptFromEditor();
    c.scriptCustom = true;
    AUTO_CFG = Object.assign({}, AUTO_DEFAULT, c);
    const st = g('acScriptState'); if (st) st.textContent = '已自定义脚本';
  };

  body.addEventListener('click', e=>{
    /* 脚本区按钮 */
    if (e.target.id === 'acImport'){
      const f = g('acFile');
      if (f) f.click();
      return;
    }
    if (e.target.id === 'acSaveScript'){ markCustom(); downloadScript(); return; }
    if (e.target.id === 'acResetScript'){
      const c = loadAutoCfg();
      c.script = ''; c.scriptCustom = false;
      AUTO_CFG = Object.assign({}, AUTO_DEFAULT, c);
      const ed = g('acScript');
      if (ed) ed.value = defaultScriptText(c);
      const st = g('acScriptState'); if (st) st.textContent = '默认模板';
      toast('已恢复默认脚本模板');
      return;
    }
    if (e.target.id === 'acCopyScript'){
      copyText(scriptFromEditor(), '脚本已复制');
      return;
    }
    const b = e.target.closest('[data-seg] button'); if (!b) return;
    const seg = b.closest('[data-seg]');
    const key = seg.dataset.seg, val = b.dataset.v;
    refresh();                                   /* 先把输入框里的改动收进来 */
    const c = loadAutoCfg(); c[key] = (key==='hour'||key==='minute') ? +val : val;
    /* 换系统时，脚本路径若还是上一套系统的默认值，就跟着换成新系统的默认路径 */
    if (key === 'os') c.scriptPath = resolveScriptPath(c);
    AUTO_CFG = Object.assign({}, AUTO_DEFAULT, c);
    seg.querySelectorAll('button').forEach(x=>x.classList.toggle('on', x===b));
    /* 频率会改变时间行（每周要选星期）；换了系统或格式后默认脚本模板也要跟着变。
       只要用户没有自定义过脚本（script 为空），就整块重画刷新模板。 */
    if (key === 'freq' || ((key === 'os' || key === 'format') && !AUTO_CFG.scriptCustom)){
      body.innerHTML = autoCfgHTML();
      body.__bound = false; bindAutoCfg();
    } else {
      refresh();
    }
  });
  body.addEventListener('change', e=>{
    if (e.target.id === 'acFile'){
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      const fr = new FileReader();
      fr.onload = ()=>{
        const ed = g('acScript');
        if (ed) ed.value = String(fr.result || '');
        markCustom();
        refresh();
        toast('已导入脚本：' + file.name);
      };
      fr.onerror = ()=>toast('读取脚本失败');
      fr.readAsText(file);
      return;
    }
    if (['acHour','acMin','acWeek','acOut','acOn','acScriptPath'].indexOf(e.target.id) >= 0) refresh();
  });
  body.addEventListener('input', e=>{
    if (e.target.id === 'acScript'){
      markCustom();
      refresh();
    } else if (e.target.id === 'acScriptPath'){
      refresh();
    }
  });

  const foot = document.getElementById('cfgFoot');
  if (foot && !foot.__bound){
    foot.__bound = true;
    foot.addEventListener('click', e=>{
      const id = e.target.id;
      if (id === 'acSave'){
        refresh();
        const c = saveAutoCfg(AUTO_CFG); c.__saved = true;
        const st = g('acState'); if (st) st.textContent = '已保存 · 下次打开自动带出';
        toast('定时更新配置已保存到本机浏览器');
        return;
      }
      if (id === 'acCopyCmd' || id === 'acCopyPrompt'){
        const el = g(id === 'acCopyCmd' ? 'acCmd' : 'acPrompt');
        copyText(el ? el.textContent : '', id === 'acCopyCmd' ? '命令已复制' : '提示语已复制');
      }
    });
  }
}

/* ============ T+1 日终对账 · 定时任务配置 ============ */
/* T 日结束时余额已经定格，次日 00:00 起就能拿「账户余额 vs 日结余额」对账。
   脚本只读本地采集结果，不唤醒 WorkBuddy、不产生新的任务，因此零积分消耗。 */
const RECON_KEY = 'wbc.recon.v1';
const RECON_DEFAULT = {on:true, hour:0, minute:0, os:'win', scriptPath:'', __saved:false};
let RECON_CFG = null;

function loadReconCfg(){
  if (RECON_CFG) return RECON_CFG;
  try {
    const raw = localStorage.getItem(RECON_KEY);
    if (raw) RECON_CFG = Object.assign({}, RECON_DEFAULT, JSON.parse(raw));
  } catch(e){}
  if (!RECON_CFG) RECON_CFG = Object.assign({}, RECON_DEFAULT);
  return RECON_CFG;
}
function saveReconCfg(c){
  RECON_CFG = Object.assign({}, RECON_DEFAULT, c || {});
  try { localStorage.setItem(RECON_KEY, JSON.stringify(RECON_CFG)); } catch(e){}
  return RECON_CFG;
}
function reconHHMM(c){ return String(c.hour).padStart(2,'0')+':'+String(c.minute).padStart(2,'0'); }
function reconScriptName(c){ return c.os === 'win' ? 'recon-daily.bat' : 'recon-daily.sh'; }
function reconScriptPath(c){
  const cur = (c.scriptPath || '').trim();
  if (cur) return cur;
  return c.os === 'win' ? '%USERPROFILE%\\.workbuddy\\recon-daily.bat'
                        : '~/.workbuddy/recon-daily.sh';
}
/* 对账脚本正文：只调本地 Python，不联网、不调模型 */
function reconScriptText(c){
  const dir = '<项目目录>';
  if (c.os === 'win'){
    return [
      '@echo off',
      'REM WorkBuddy 积分审计 · T+1 日终积分对账（只读本地数据、不消耗积分）',
      'REM 由「任务监测 → 定时任务配置 → 对账配置」生成，可直接编辑',
      'setlocal',
      'set "PROJECT='+dir+'"',
      'cd /d "%PROJECT%"',
      'python -m hosts.commands.recon_daily --save "%PROJECT%\\out\\recon-daily.json"'
        + ' --log "%PROJECT%\\out\\recon.log" --yes',
      'endlocal'
    ].join('\r\n');
  }
  return [
    '#!/bin/sh',
    '# WorkBuddy 积分审计 · T+1 日终积分对账（只读本地数据、不消耗积分）',
    '# 由「任务监测 → 定时任务配置 → 对账配置」生成，可直接编辑',
    'PROJECT="'+dir+'"',
    'cd "$PROJECT" || exit 1',
    'python3 -m hosts.commands.recon_daily --save "$PROJECT/out/recon-daily.json"'
      + ' --log "$PROJECT/out/recon.log" --yes'
  ].join('\n');
}
/* 系统计划任务命令：默认每天 00:00（T+1 的起点） */
function reconCmdText(c){
  const path = reconScriptPath(c);
  const hh = reconHHMM(c);
  if (c.os === 'win'){
    const tr = ('cmd /c "'+path+'"').replace(/"/g, '\\"');
    return 'schtasks /Create /TN "WorkBuddyCreditRecon" /TR "'+tr+'" /SC DAILY /ST '+hh+' /F';
  }
  return '# 每天 '+hh+' 跑一次 T+1 日终对账（crontab -e 追加一行）\n'
    + (+c.minute)+' '+(+c.hour)+' * * * /bin/sh "'+path+'"';
}
function reconPromptText(c){
  return '请帮我创建一个每天 '+reconHHMM(c)+' 执行的定时任务：执行本机脚本 '+reconScriptPath(c)
    + '（脚本已写好并落盘，内容是跑 workbuddy-cost-audit 的 T+1 日终积分对账，'
    + '只读本地采集结果、不联网、不调用模型，因此不消耗积分）。'
    + '执行后请把「对账日期、日初积分、日耗、日结余额、账户余额、偏差、判定」告诉我。';
}

function reconCfgHTML(){
  const c = loadReconCfg();
  const hourOpt = Array.from({length:24},(_,i)=>'<option value="'+i+'" '
    + (+c.hour===i?'selected':'')+'>'+String(i).padStart(2,'0')+'</option>').join('');
  const minOpt = [0,10,20,30,40,50].map(i=>'<option value="'+i+'" '
    + (+c.minute===i?'selected':'')+'>'+String(i).padStart(2,'0')+'</option>').join('');
  const seg = (key, opts, cur)=>'<div class="cfg-seg" data-seg="'+key+'">'
    + opts.map(([v,t])=>'<button data-v="'+v+'" class="'+(cur===v?'on':'')+'">'+t+'</button>').join('')
    + '</div>';

  let h = '<div class="cfg-row"><div class="cfg-k">启用</div><div class="cfg-v">'
    + '<label class="cfg-sw"><input type="checkbox" id="rcOn" '+(c.on?'checked':'')
    + '> 每天自动跑一次 T+1 日终积分对账</label></div></div>';

  h += '<div class="cfg-row"><div class="cfg-k">执行时间</div><div class="cfg-v">'
    + '<select id="rcHour">'+hourOpt+'</select>'
    + '<span style="color:var(--text3)">:</span>'
    + '<select id="rcMin">'+minOpt+'</select>'
    + '<span style="color:var(--text3)">默认 00:00 —— T 日结束即对账，'
      + '此时 T 日余额已定格，不会与当天还在跑的任务打架</span></div></div>';

  h += '<div class="cfg-row"><div class="cfg-k">目标系统</div><div class="cfg-v">'
    + seg('os', [['win','Windows'],['unix','macOS / Linux']], c.os)+'</div></div>';

  h += '<div class="cfg-row"><div class="cfg-k">对账口径</div><div class="cfg-v">'
    + '<span style="color:var(--text2);line-height:1.7">'
    + '日初 = 日收 + 上一日日结余额 · 日结 = 日初 − 日耗 · '
    + '偏差 = 账户余额 − 日结 · |偏差| ≤ 容差判「账实一致」</span></div></div>';

  h += '<div class="sec-t" style="margin-top:14px">对账脚本</div>'
    + '<div class="cfg-note" style="margin:0 0 7px">'
    + '脚本只调用本机 <code>hosts/commands/recon_daily.py</code>，读的是已经采集好的本地数据，'
    + '<b>不唤醒 WorkBuddy、不新建任务，所以不消耗账面积分</b>。'
    + '执行结果写到 <code>out/recon-daily.json</code> 与 <code>out/recon.log</code>。</div>'
    + '<div class="cfg-row"><div class="cfg-k">脚本路径</div><div class="cfg-v">'
      + '<input class="cfg-txt" id="rcScriptPath" type="text" value="'+esc(reconScriptPath(c))+'">'
    + '</div></div>'
    + '<div class="cfg-editor"><textarea id="rcScript" spellcheck="false" rows="9">'
      + esc(reconScriptText(c))+'</textarea></div>'
    + '<div class="cfg-row" style="margin-top:8px"><div class="cfg-k">操作</div><div class="cfg-v">'
      + '<button class="btn sm" id="rcSaveScript">保存脚本到本机</button>'
      + '<button class="btn sm" id="rcCopyScript">复制脚本</button>'
    + '</div></div>';

  h += '<div class="sec-t" style="margin-top:12px">可执行命令</div>'
    + '<div class="cfg-cmd"><pre id="rcCmd">'+esc(reconCmdText(c))+'</pre></div>'
    + '<div class="cfg-note">把 <b>&lt;项目目录&gt;</b> 换成本机 workbuddy-cost-audit 的实际路径；'
    + 'Windows 用「以管理员身份」运行一次即可注册，'
    + (c.os==='unix'?'macOS / Linux 用 <code>crontab -e</code> 追加。':'')
    + '</div>';

  h += '<div class="sec-t" style="margin-top:12px">交给 WorkBuddy 创建</div>'
    + '<div class="cfg-cmd"><pre id="rcPrompt">'+esc(reconPromptText(c))+'</pre></div>'
    + '<div class="cfg-note">第一次创建时用一次提示语即可；之后由系统计划任务按脚本自己跑，'
    + '不再消耗积分。</div>';

  h += '<div class="cfg-note" style="margin-top:10px"><b>说明：</b>配置只保存在本机浏览器，'
    + '用于生成上面的脚本与命令，<b>不会</b>自行注册系统任务。</div>';
  return h;
}

function openReconCfg(){
  const body = document.getElementById('cfgBody');
  const foot = document.getElementById('cfgFoot');
  if (!body || !foot) return;
  body.__bound = false;
  body.innerHTML = reconCfgHTML();
  foot.innerHTML = '<button class="btn" id="rcCopyCmd">复制命令</button>'
    + '<button class="btn" id="rcCopyPrompt">复制提示语</button>'
    + '<span class="ml-auto" style="color:var(--text3);font-size:10.5px" id="rcState">'
    + (loadReconCfg().__saved ? '已保存' : '未保存')+'</span>'
    + '<button class="btn pri" id="rcSave">保存配置</button>';
  document.getElementById('cfgMask').classList.add('on');
  document.getElementById('cfgModal').classList.add('on');
  bindReconCfg();
}
function bindReconCfg(){
  const body = document.getElementById('cfgBody');
  if (!body || body.__rbound) return;
  body.__rbound = true;
  const g = id => document.getElementById(id);
  const repaint = ()=>{
    body.innerHTML = reconCfgHTML();
    body.__rbound = false;
    bindReconCfg();
  };
  body.addEventListener('change', e=>{
    const c = loadReconCfg();
    if (e.target.id === 'rcOn') { c.on = e.target.checked; saveReconCfg(c); }
    else if (e.target.id === 'rcHour'){ c.hour = +e.target.value; saveReconCfg(c); repaint(); }
    else if (e.target.id === 'rcMin'){ c.minute = +e.target.value; saveReconCfg(c); repaint(); }
    else if (e.target.id === 'rcScriptPath'){ c.scriptPath = e.target.value.trim(); saveReconCfg(c); }
  });
  body.addEventListener('click', e=>{
    if (e.target.id === 'rcCopyScript'){ copyText(g('rcScript').value, '对账脚本已复制'); return; }
    if (e.target.id === 'rcSaveScript'){
      const c = loadReconCfg();
      try {
        const blob = new Blob([g('rcScript').value], {type:'text/plain;charset=utf-8'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = reconScriptName(c);
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        setTimeout(()=>{ try{ URL.revokeObjectURL(url); }catch(e2){} }, 1500);
        toast('已下载 '+reconScriptName(c)+'，放到「脚本路径」指定位置即可');
      } catch(e2){ toast('下载失败，请用「复制脚本」手动保存'); }
      return;
    }
    const b = e.target.closest('[data-seg] button'); if (!b) return;
    const key = b.closest('[data-seg]').dataset.seg;
    const c = loadReconCfg();
    if (key === 'os'){ c.os = b.dataset.v; c.scriptPath = ''; saveReconCfg(c); repaint(); }
  });
  const foot = document.getElementById('cfgFoot');
  if (foot && !foot.__rbound){
    foot.__rbound = true;
    foot.addEventListener('click', e=>{
      const id = e.target.id;
      if (id === 'rcSave'){
        const c = loadReconCfg(); c.__saved = true; saveReconCfg(c);
        const st = g('rcState'); if (st) st.textContent = '已保存 · 下次打开自动带出';
        toast('T+1 日终对账配置已保存到本机浏览器');
        return;
      }
      if (id === 'rcCopyCmd' || id === 'rcCopyPrompt'){
        const el = g(id === 'rcCopyCmd' ? 'rcCmd' : 'rcPrompt');
        copyText(el ? el.textContent : '', id === 'rcCopyCmd' ? '命令已复制' : '提示语已复制');
      }
    });
  }
}

function copyText(text, okMsg){
  const done = ()=>toast(okMsg || '已复制');
  try {
    if (navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(text).then(done, ()=>fallbackCopy(text, done));
      return;
    }
  } catch(e){}
  fallbackCopy(text, done);
}
function fallbackCopy(text, done){
  try {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    done();
  } catch(e){ toast('复制失败，请手动选中文本复制'); }
}

document.addEventListener('keydown', e=>{
  if (e.key === 'Escape') closeAutoCfg();
});
$('#cfgMask').addEventListener('click', closeAutoCfg);
$('#cfgClose').addEventListener('click', closeAutoCfg);
