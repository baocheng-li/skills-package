/* ================= 视图六：运营分析 ================= */
/* 本页回答两个问题：① 不同「运行模式」的成本与质量差异；② 不同「模型」的性价比与去向。
   两条结论都必须能落到动作上，否则不如不放。 */

/* 「模式」按真实执行结构切分，而不是按业务名称编排：
     纯推理（无工具调用）/ 工具链（多步工具、一次跑通）/ 重试补跑（出现重试或中断后补跑）。
   这样分的原因是可行动——三种模式对应的止损动作完全不同。 */
const AM_MODE = [
  {k:'pure',  t:'纯推理模式', d:'无工具调用 · 单轮生成',  c:'var(--info-9)'},
  {k:'tool',  t:'工具链模式', d:'多步工具调用 · 一次跑通', c:'var(--ok-11)'},
  {k:'retry', t:'重试补跑模式', d:'出现重试 / 中断后补跑', c:'var(--bad-9)'}
];

function amBucket(){
  const b = {pure:[], tool:[], retry:[]};
  TASKS.forEach(t=>{
    if ((t.retry||0) > 0 || t.status === '失败') b.retry.push(t);
    else if ((t.nTools||0) > 0) b.tool.push(t);
    else b.pure.push(t);
  });
  return b;
}

function amAgg(list){
  const n = list.length;
  const credits = list.reduce((s,t)=>s+(t.credits||0),0);
  const dur = list.reduce((s,t)=>s+(t.duration||0),0);
  const ok = list.filter(t=>t.status==='成功').length;
  return {n:n, credits:credits, dur:dur, ok:ok,
    avg: n ? credits/n : 0, avgDur: n ? dur/n : 0, succ: n ? ok/n : 0};
}

function amModes(){
  const b = amBucket();
  const total = TASKS.reduce((s,t)=>s+(t.credits||0),0) || 1;
  return AM_MODE.map(m=>{
    const s = amAgg(b[m.k]);
    return Object.assign({}, m, s, {share: s.credits/total, tshare: TASKS.length ? s.n/TASKS.length : 0});
  });
}

/* 建议由数据条件触发，不写死文案：条件不成立就不出现，避免"永远正确"的空话 */
function amModeAdvice(modes){
  const pure = modes[0], tool = modes[1], retry = modes[2];
  const out = [];
  if (retry.n && (retry.share >= 0.08 || retry.succ < 0.5)){
    const times = tool.avgDur > 0 ? retry.avgDur/tool.avgDur : 0;
    out.push({c:'var(--bad-9)', t:'重试补跑是纯损耗，先止损再谈优化',
      d:'这 '+retry.n+' 个任务吃掉 '+fmt0(retry.credits)+' 积分（占全部支出的 '+pct1(retry.share)+'），'
        + '成功率 '+pct1(retry.succ)+'，平均耗时 '+(times >= 1.1
            ? ('是一次通过模式的 '+times.toFixed(1)+' 倍') : '与一次通过相当')
        + '。建议在工具调用前后各加一道前置校验（入参 schema、权限、超时），'
        + '单步异常即中断并把剩余步骤标为「待人工确认」——在错误前提上反复补跑，'
        + '只会把积分花在同一个失败上。',
      ev:'重试任务 '+retry.n+' 个 · 积分 '+fmt0(retry.credits)+' · 成功 '+retry.ok+' 个',
      save:retry.credits});
  }
  if (tool.n && tool.share >= 0.5){
    out.push({c:'var(--warn-9)', t:'工具链是主要支出，按步长设预算上限',
      d:'工具链模式承担 '+pct1(tool.share)+' 的积分、'+pct1(tool.tshare)+' 的任务量，'
        + '均 '+c2(tool.avg)+' 积分/任务、'+lgDur(tool.avgDur)+'/任务。'
        + '这类任务积分随步数近似线性增长，建议按复杂度设步长上限'
        + '（L3 ≤15 步 / L4 ≤25 步），超限时先收敛上下文再续跑，'
        + '而不是让它一直往上叠。',
      ev:'工具链 '+tool.n+' 个任务 · 均 '+c2(tool.avg)+' 积分'});
  }
  if (pure.tshare < 0.2){
    out.push({c:'var(--info-9)', t:'轻任务没被分流，白白走了工具链',
      d:'纯推理模式只占 '+pct1(pure.tshare)+' 的任务量（'+pure.n+' 个，均 '
        + c2(pure.avg)+' 积分），说明简单问答、格式转换也在走多步工具链。'
        + '建议对请求先做一次判型：单轮可完成的直接走轻量模型，'
        + '省掉工具往返与随之而来的上下文膨胀。',
      ev:'纯推理 '+pure.n+' 个任务 · 占 '+pct1(pure.tshare)});
  }
  if (!out.length){
    out.push({c:'var(--ok-10)', t:'模式分布均衡，暂无需干预',
      d:'三种模式的积分占比与成功率没有出现明显异常，保持当前策略即可；'
        + '等窗口积累到 30 天后用同样的表格复核一次。',
      ev:'本窗口 '+TASKS.length+' 个任务'});
  }
  return out;
}

function amModeCard(title, sub){
  const modes = amModes();
  let h = '<div class="am-modes">' + modes.map(m=>
      '<div class="am-mode" style="--rc:'+m.c+'">'
      + '<div class="am-mode-hd"><b>'+m.t+'</b></div>'
      + '<div class="am-mode-sub">'+m.d+'</div>'
      + '<div class="am-mode-num"><b>'+fmt0(m.n)+'</b><span>个任务</span>'
        + '<i>占积分 '+pct1(m.share)+'</i></div>'
      + '<div class="am-bar"><i style="width:'+Math.max(1.5, m.share*100).toFixed(1)+'%"></i></div>'
      + '<div class="am-mode-kv">'
        + '<span>合计 <b>'+c2(m.credits)+'</b></span>'
        + '<span>均 <b>'+c2(m.avg)+'</b>/任务</span>'
        + '<span>耗时 <b>'+lgDur(m.avgDur)+'</b></span>'
        + '<span>成功率 <b style="color:'+(m.succ>=0.85?'var(--ok-11)'
            :m.succ>=0.6?'var(--warn-soft)':'var(--bad-11)')+'">'+pct1(m.succ)+'</b></span>'
      + '</div></div>').join('') + '</div>';

  h += '<div class="am-adv">' + amModeAdvice(modes).map(a=>
      '<div class="rec" style="--rc:'+a.c+'">'
      + '<div class="rec-t">'+a.t+'</div>'
      + '<div class="rec-d">'+a.d+'</div>'
      + '<div class="rec-m"><span>依据 '+a.ev+'</span>'
      + (a.save?'<b>涉及 '+fmt0(a.save)+' 积分</b>':'')+'</div>'
      + '</div>').join('') + '</div>';

  return cardHTML(title || '运行模式对比',
    sub || '按真实执行结构切分 · 三种模式的积分 / 时长 / 成功率', h);
}

/* ---------- 模型维度：从 bench 汇总，保证与「性价比矩阵」同源 ---------- */
/* 不用 RAW.models[].credits：那个值是按单价折算的，单价未上报的模型会算成 0，
   直接把 78 积分的使用量抹掉。这里统一用步级上报积分（bench.totalCredits）。 */
function amModelRows(){
  const agg = {};
  BENCH_ROWS.forEach(r=>{
    const a = agg[r.model] || (agg[r.model] = {id:r.model, n:0, credits:0,
      succW:0, e2eW:0, stepsW:0, lvls:{}});
    a.n += r.n;
    a.credits += r.totalCredits || 0;
    /* bench.succ 是百分数（0—100），这里先统一折成 0—1，
       否则乘权重后再显示会变成 7388% 这种数字 */
    a.succW += r.n * (r.succ/100);
    a.e2eW += r.n * r.e2e;
    a.stepsW += r.n * r.steps;
    a.lvls[r.lvl] = (a.lvls[r.lvl] || 0) + r.n;
  });
  const total = Object.keys(agg).reduce((s,k)=>s+agg[k].credits,0) || 1;
  return Object.keys(agg).map(k=>{
    const a = agg[k], m = M[k] || {};
    return {id:k, name:modelName(k), color:modelColor(k),
      tier:m.tier || '—', tag:m.tag || 'gray', calls:m.calls || 0,
      n:a.n, credits:a.credits,
      avg: a.n ? a.credits/a.n : 0,
      succ: a.n ? a.succW/a.n : 0,
      e2e: a.n ? a.e2eW/a.n : 0,
      steps: a.n ? a.stepsW/a.n : 0,
      lvls: Object.keys(a.lvls).sort().join(' / '),
      share: a.credits/total};
  }).sort((x,y)=>y.credits-x.credits);
}

function amModelVerdict(r, medAvg, avgSucc){
  if (r.n < MIN_SAMPLE) return ['样本不足','gray'];
  /* 「主力」要同时满足三个条件：质量够、量够、均价不高。
     只看成功率会把「跑了 3 个 L3/L4 任务、均 1.5 积分」的小样本模型推成主力，
     那种推荐在审计场景里是不能落地的。 */
  if (r.succ >= 0.9 && r.share >= 0.15 && r.avg <= medAvg) return ['主力推荐','green'];
  if (r.share >= 0.25 && r.succ < avgSucc - 0.05) return ['支出集中·质量偏低','red'];
  if (r.succ >= 0.9 && r.avg > medAvg * 1.5) return ['贵但更稳','amber'];
  return ['观察','gray'];
}

function amModelCard(){
  const rows = amModelRows();
  if (!rows.length){
    return cardHTML('模型运行情况', '窗口内无可归因的模型调用',
      '<div class="empty" style="padding:26px 0;text-align:center;color:var(--text3)">'
      + '完成几个任务后自动补齐。</div>');
  }
  const nAll = rows.reduce((s,r)=>s+r.n,0) || 1;
  const avgSucc = rows.reduce((s,r)=>s+r.n*r.succ,0)/nAll;
  const sortedAvg = rows.map(r=>r.avg).sort((a,b)=>a-b);
  const medAvg = sortedAvg[Math.floor(sortedAvg.length/2)];

  /* 表头用短名 + title 写全称：这一格只有 600px 宽，11 列挤进来
     结果就是「档…」「覆…」——截断的表头等于没有表头。档位合并进模型名那一格。 */
  let tbl = '<div class="tbl-wrap" style="max-height:none"><table class="tbl">'
    + '<thead><tr><th>模型</th><th title="该模型覆盖过的复杂度层级">层级</th>'
    + '<th style="text-align:right" title="任务数">n</th>'
    + '<th style="text-align:right" title="调用步数（模型步 + 工具步）">步数</th>'
    + '<th style="text-align:right" title="窗口内该模型累计消耗积分">积分</th>'
    + '<th style="width:14%" title="占窗口总积分的比例">占比</th>'
    + '<th style="text-align:right" title="均积分 / 任务">均值</th>'
    + '<th style="text-align:right" title="均端到端时长">时长</th>'
    + '<th style="text-align:right" title="无模型报错且工具调用正常的任务占比">成功率</th>'
    + '<th>判定</th></tr></thead><tbody>';

  rows.forEach(r=>{
    const v = amModelVerdict(r, medAvg, avgSucc);
    tbl += '<tr>'
      + '<td class="name"><i class="dotm" style="background:'+r.color+'"></i>'+r.name
        + '<span class="tag '+r.tag+'" style="margin-left:4px;font-size:9px;height:15px;'
        + 'line-height:15px;padding:0 4px">'+r.tier+'</span></td>'
      + '<td style="color:var(--text2);font-size:10px">'+r.lvls+'</td>'
      + '<td class="num">'+r.n+'</td>'
      + '<td class="num" style="color:var(--text2)">'+fmt0(r.calls)+'</td>'
      + '<td class="num" style="font-weight:600">'+c2(r.credits)+'</td>'
      + '<td><span class="am-bar sm"><i style="width:'+Math.max(1.5, r.share*100).toFixed(1)
        +'%;background:'+r.color+'"></i></span>'
        + '<span style="font-size:10px;color:var(--text3)">'+pct1(r.share)+'</span></td>'
      + '<td class="num">'+c2(r.avg)+'</td>'
      + '<td class="num" style="color:var(--text2)">'+lgDur(r.e2e)+'</td>'
      + '<td class="num" style="color:'+(r.succ>=0.9?'var(--ok)'
          :r.succ>=0.8?'var(--text)':'var(--bad-11)')+';font-weight:600">'+pct1(r.succ)+'</td>'
      + '<td><span class="tag '+v[1]+' sm">'+v[0]+'</span></td>'
      + '</tr>';
  });
  tbl += '</tbody></table></div>';

  return cardHTML('模型运行情况',
    '按步级上报积分汇总 · 与下方性价比矩阵同源 · 判定阈值：成功率 ≥90% 且均积分 ≤ 中位数',
    tbl, 'mt');
}

/* 模型维度的一句话结论：只陈述数据能支撑的部分，并明说这张表不能拿来跨层级比性价比 */
function amModelConclusion(rows){
  if (rows.length < 2) return '窗口内只有 '+rows.length+' 个模型有可归因调用，无法比较。';
  const nAll = rows.reduce((s,r)=>s+r.n,0) || 1;
  const avgSucc = rows.reduce((s,r)=>s+r.n*r.succ,0)/nAll;
  const top = rows[0];
  const parts = [];
  parts.push('<b>结论：</b>积分最集中在 <b>'+top.name+'</b>（'+c2(top.credits)+' 积分，占 '
    + pct1(top.share)+'，成功率 '+pct1(top.succ)+'）。');

  const low = rows.filter(r=>r.n >= MIN_SAMPLE && r.succ < avgSucc - 0.05);
  if (low.length){
    parts.push('成功率低于全窗口均值（'+pct1(avgSucc)+'）的有 '
      + low.map(r=>r.name+'（'+pct1(r.succ)+' · '+r.n+' 个任务）').join('、')
      + '——这类差距多数来自重试与中断，先看上方「运行模式对比」里的重试损耗，'
      + '再判断是换模型还是修流程。');
  }
  parts.push('注意：本表的「均积分/任务」受覆盖层级影响（L4 与 L1 相差一个量级），'
    + '不能跨层级直接比性价比；选型请用下方按层级切分的对比。');
  return parts.join('');
}

/* ---------- 单层级对比（原性价比视图） ----------
   数据口径：本机真实观测（同一层级下不同模型的实际任务表现），
   不是受控 A/B 实验，因此所有结论都带样本量，样本不足的层级明确不给结论。 */
const CH_DEFAULT_LEVEL = (BENCH['L4'] && BENCH['L4'].rows.length) ? 'L4'
  : (BENCH['L3'] && BENCH['L3'].rows.length ? 'L3' : (BENCH_ROWS[0] ? BENCH_ROWS[0].lvl : 'L1'));
let CH_LEVEL = CH_DEFAULT_LEVEL;
const CH_ACTIVE = {m: MODELS.filter(m=>m.id!=='unlabeled').map(x=>x.id)};

function levelOptionLabel(lv){
  const meta = SCEN_META[lv] || {label:'—', samples:0};
  return lv + ' · ' + LEVEL_LABEL[lv] + ' · ' + meta.label + ' · n=' + meta.samples;
}

function buildAnalysis(){
  /* 一屏 3 列 × 4 行的仪表盘。顺序按「先看结论、再看依据」排：
       模式 → 模型 → 单层级性价比 → 建议与路由
     每格都是独立卡片，超长内容在卡片内部滚动，整页不再上下滚——
     审计的时候人是在「比对」，一屏看全才比得了。 */
  const T = RECS.tiers || {t1:[],t2:[],t3:[]};
  const tierMeta = [
    {k:'t1', t:'第一档 · 立即可做', s:'低风险', c:'var(--ok-10)'},
    {k:'t2', t:'第二档 · 需要验证', s:'先试点', c:'var(--warn-9)'},
    {k:'t3', t:'第三档 · 结构性优化', s:'改机制', c:'var(--iris-11)'}
  ];

  /* 层级切换条：塞进矩阵卡片的头部，不再单独占一整行 */
  const levelBar = '<div class="am-levelbar">'
    + '<select class="sel" id="b-tpl">'
      + LEVELS.map(lv=>'<option value="'+lv+'" '+(lv===CH_LEVEL?'selected':'')+'>'
        + levelOptionLabel(lv)+'</option>').join('')
    + '</select>'
    + '<div class="seg" id="b-lvl">'+LEVELS.map(lv=>
        '<button data-v="'+lv+'" class="'+(lv===CH_LEVEL?'on':'')+'">'+lv+'</button>').join('')+'</div>'
    + '<div class="am-chips">'
      + MODELS.filter(m=>m.id!=='unlabeled').map(m=>
        '<span class="tag '+(CH_ACTIVE.m.includes(m.id)?m.tag:'gray')+'" data-chip="'+m.id+'" '
        + 'style="cursor:pointer;height:20px">'
        + '<i class="dotm" style="background:'+m.color+'"></i>'+m.short+'</span>').join('')
    + '</div>'
    + '<button class="btn xs ml-auto" id="b-run">导出对比</button>'
    + '</div>';

  const tiersHTML = '<div class="am-tiers">' + tierMeta.map(tm=>{
    const items = T[tm.k] || [];
    const body = items.length ? items.slice(0,3).map(it=>
        '<div class="rec" style="--rc:'+tm.c+'">'
        + '<div class="rec-t">'+it.title+'</div>'
        + '<div class="rec-d">'+it.action+'</div>'
        + '<div class="rec-m"><span>依据 '+it.evidence+'</span>'
        + (it.windowSaving?'<b>止损 '+fmt0(it.windowSaving)+'</b>':'')+'</div>'
        + '</div>').join('')
      : '<div class="hint">本档暂无建议：样本不足或未触发阈值。</div>';
    return '<div class="tier"><div class="tier-hd">'
      + '<span class="dotlvl" style="background:'+tm.c+'"></span><b>'+tm.t+'</b>'
      + '<span class="tag gray sm" style="margin-left:auto">'+tm.s+'</span></div>'
      + body + '</div>';
  }).join('') + '</div>';

  const routeHTML = '<div class="am-routes">' + ROUTING.map(r=>
      '<div class="route" style="--rc:'+r.color+'"'+(r.pending?' data-pending="1"':'')+'>'
      + '<div class="route-hd"><span class="lvl '+r.lvl+'" style="width:24px">'+r.lvl+'</span><b>'+r.title+'</b></div>'
      + '<div class="route-main">'+r.body+'</div>'
      + '<div class="route-ft"><span>实测降幅 <b>'+(r.pending?'—':r.save)+'</b></span><span>'+r.note+'</span></div>'
      + '</div>').join('') + '</div>'
    + '<div class="am-note" title="① 每格标注样本量 n；② n &lt; '+MIN_SAMPLE+' 的格子只展示不参与结论；'
    + '③ 成功率 = 无模型报错且工具调用正常的任务占比；④ 总积分为任务全链路累计，含重试与上下文膨胀；'
    + '⑤ 只有成功率差 ≤3pp 的成本优势才计为真实性价比优势。">'
    + '本页为<b>本机真实观测</b>，非受控 A/B；n &lt; '+MIN_SAMPLE+' 只展示不下结论；'
    + '成功率差不超 3pp 的成本优势才算真优势。</div>';

  const note = '本机真实使用观测 · 非受控 A/B · 样本不足只展示不下结论';

  let h = '<section class="am-cell am-mode">'
      + amModeCard('运行模式对比', '按真实执行结构切分 · '+TASKS.length+' 个任务')
      + '</section>'
    + '<section class="am-cell am-model">'
      + amModelCard() + '</section>'
    + '<section class="am-cell am-matrix">'
      + cardHTML('单层级性价比矩阵', '横轴 <b>积分/任务</b> · 纵轴 <b>成功率</b> · 气泡 <b>样本量</b>',
          '<div class="chart" id="c-matrix"></div>' + levelBar, 'am-chart-card')
      + '</section>'
    + '<section class="am-cell am-time">'
      + cardHTML('端到端时长结构', '推理 / 工具等待 / 其他', '<div class="chart" id="c-time"></div>',
          'am-chart-card')
      + '</section>'
    + '<section class="am-cell am-rec">'
      + cardHTML('操作建议（按优先级）',
          '第一档窗口内实测止损 <b style="color:var(--ok-11)">'+fmt0(RECS.immediateWindowSaving||0)+'</b> 积分',
          tiersHTML, 'am-scroll')
      + '</section>'
    + '<section class="am-cell am-track">'
      + cardHTML('路由落地跟踪', '当前主用 → 建议模型', '<div id="benchTable"></div>', 'am-scroll')
      + '</section>'
    + '<section class="am-cell am-route">'
      + cardHTML('分级路由建议', note, routeHTML, 'am-route-card')
      + '</section>';

  $('#v-analysis').innerHTML = h;

  $('#b-tpl').addEventListener('change', e=>{ CH_LEVEL = e.target.value; syncBench(); });
  $('#b-lvl').addEventListener('click', e=>{
    const b = e.target.closest('button'); if(!b) return;
    CH_LEVEL = b.dataset.v; syncBench();
  });
  $('#v-analysis').addEventListener('click', e=>{
    const chip = e.target.closest('[data-chip]');
    if (chip){
      const id = chip.dataset.chip;
      const m = MODELS.find(x=>x.id===id);
      if (CH_ACTIVE.m.includes(id)){
        if (CH_ACTIVE.m.length>2) CH_ACTIVE.m = CH_ACTIVE.m.filter(x=>x!==id);
      } else CH_ACTIVE.m.push(id);
      chip.className = 'tag '+(CH_ACTIVE.m.includes(id)?m.tag:'gray');
      chip.style.cursor='pointer'; chip.style.height='22px';
      drawMatrix();
      return;
    }
    if (e.target.id==='b-run'){ $('#btnExport').click(); }
  });

  drawMatrix();
  drawTime();
  renderBenchTable();
}

function syncBench(){
  $$('#b-lvl button').forEach(b=>b.classList.toggle('on', b.dataset.v===CH_LEVEL));
  $('#b-tpl').value = CH_LEVEL;
  drawMatrix(); drawTime(); renderBenchTable();
}

function drawMatrix(){
  const rows = BENCH_ROWS.filter(r=>CH_ACTIVE.m.includes(r.model));
  const shape = {L1:'circle', L2:'roundRect', L3:'diamond', L4:'triangle'};
  const sizes = rows.map(r=>r.credits).filter(v=>v>0);
  const xmin = sizes.length ? Math.max(0.05, Math.min.apply(null, sizes)*0.6) : 0.1;
  const xmax = sizes.length ? Math.max.apply(null, sizes)*1.8 : 100;
  const succs = rows.map(r=>r.succ);
  const ymin = succs.length ? Math.max(0, Math.floor((Math.min.apply(null, succs)-10)/5)*5) : 60;

  const series = MODELS.filter(m=>CH_ACTIVE.m.includes(m.id)).map(m=>{
    const pts = rows.filter(r=>r.model===m.id).map(r=>{
      const on = r.lvl===CH_LEVEL;
      return {
        value:[r.credits, r.succ, r.n, r.lvl],
        symbol:shape[r.lvl] || 'circle',
        symbolSize: (on?9:6) + Math.sqrt(r.n)*(on?3.2:2.2),
        itemStyle:{color:m.color, opacity:on?0.95:0.3,
          borderColor: on?'var(--panel3)':'transparent', borderWidth:on?2:0,
          shadowBlur: on?14:0, shadowColor:m.color+'88'},
        label:{show:false}
      };
    });
    return {name:m.name, type:'scatter', data:pts, z:3, clip:false, emphasis:{scale:1.2}};
  });

  // 只在有足够样本时标注“高成功率低成本”参考区
  const solid = rows.filter(r=>r.n>=MIN_SAMPLE && r.lvl===CH_LEVEL);
  if (solid.length>=2 && series.length){
    const medCr = solid.slice().sort((a,b)=>a.credits-b.credits)[Math.floor(solid.length/2)].credits;
    series[0].markArea = {
      silent:true, itemStyle:{color:'rgba(53,208,127,.07)'},
      label:{show:false},
      data:[[{xAxis:xmin, yAxis:Math.max(ymin,95)},{xAxis:medCr, yAxis:100}]]
    };
  }

  chart('c-matrix', {
    animationDuration:600, tooltip:Object.assign({trigger:'item', formatter:p=>{
      const v = p.value;
      return '<b>'+p.seriesName+'</b><br/>复杂度 '+v[3]
        +'<br/>总积分/任务 '+fmt0(v[0])
        +'<br/>成功率 '+v[1]+'%'
        +'<br/>样本 '+v[2]+' 个任务';
    }}, tooltipBase),
    legend:{show:true, data:series.map(s=>s.name), right:0, bottom:0, itemWidth:8, itemHeight:8,
      itemGap:11, textStyle:{color:TT.text3, fontSize:9.5}, icon:'roundRect',
      formatter:n=>n.replace('DeepSeek ','').replace('混元 ','')},
    grid:baseGrid({left:4, right:16, top:16, bottom:26}),
    xAxis:{type:'log', logBase:10, min:xmin, max:xmax,
      axisLine:{lineStyle:{color:TT.line2}}, axisTick:{show:false},
      splitLine:{lineStyle:{color:'var(--grid-line)', type:'dashed'}},
      axisLabel:{color:TT.text3, fontSize:10, formatter:v=>v>=1000?Math.round(v/1000)+'k':v},
      name:'积分/任务（对数）', nameTextStyle:{color:TT.text3, fontSize:9.5}, nameLocation:'end', nameGap:6},
    yAxis:{type:'value', min:ymin, max:100,
      splitLine:{lineStyle:{color:'var(--grid-line)', type:'dashed'}},
      axisLine:{show:false}, axisTick:{show:false},
      axisLabel:{color:TT.text3, fontSize:10, formatter:'{value}%'}},
    series:series
  });
}

function drawTime(){
  const rows = BENCH_ROWS.filter(r=>r.lvl===CH_LEVEL && CH_ACTIVE.m.includes(r.model))
    .sort((a,b)=>a.credits-b.credits);
  if (!rows.length){
    chart('c-time', {title:{text:'该层级暂无可用样本', left:'center', top:'middle',
      textStyle:{color:TT.text3, fontSize:11, fontWeight:400}}});
    return;
  }
  const names = rows.map(r=>(M[r.model]||{short:r.model}).short);
  const noTiming = r => (r.infer + r.wait) === 0;
  const other = rows.map(r=>noTiming(r) ? 0 : Math.max(0, Math.round((r.e2e - r.infer - r.wait)*10)/10));

  chart('c-time', {
    animationDuration:700, grid:baseGrid({left:2, right:36, top:30, bottom:2}),
    tooltip:Object.assign({trigger:'axis', axisPointer:{type:'shadow'},
      valueFormatter:v=>v+' s'}, tooltipBase),
    legend:{data:['模型推理','工具等待/重试','其他开销','无耗时明细'], right:0, top:0, itemWidth:8, itemHeight:8,
      itemGap:12, textStyle:{color:TT.text3, fontSize:10}, icon:'roundRect'},
    xAxis:{type:'value', show:false},
    yAxis:{type:'category', data:names, axisLine:{show:false}, axisTick:{show:false},
      axisLabel:{color:TT.text2, fontSize:10.5}},
    series:[
      {name:'模型推理', type:'bar', stack:'t', barWidth:'54%', data:rows.map(r=>r.infer),
       itemStyle:{color:'var(--info-9)'}},
      {name:'工具等待/重试', type:'bar', stack:'t', data:rows.map(r=>r.wait),
       itemStyle:{color:'var(--ok-11)'}},
      {name:'其他开销', type:'bar', stack:'t', data:other,
       itemStyle:{color:'var(--line3)', borderRadius:[0,4,4,0]},
       label:{show:true, position:'right', color:TT.text3, fontSize:9.5,
         formatter:p=>rows[p.dataIndex].e2e+'s'}}
    ]
  });
}

function renderBenchTable(){
  const rows = BENCH_ROWS.filter(r=>r.lvl===CH_LEVEL && CH_ACTIVE.m.includes(r.model))
    .sort((a,b)=>a.credits-b.credits);

  if (!rows.length){
    $('#benchTable').innerHTML = cardHTML('对比明细', '复杂度 <b>'+CH_LEVEL+'</b>',
      '<div class="empty" style="padding:28px 0;text-align:center;color:var(--text3);font-size:12px">'
      + '该层级在窗口内没有可归因的模型调用。完成几个该层级任务后即可自动补齐。</div>');
    return;
  }

  const base = rows.reduce((a,b)=>a.credits<b.credits?a:b);

  function verdict(r){
    if (r.n < MIN_SAMPLE) return ['样本不足','gray'];
    const dom = rows.some(o=>o!==r && o.n>=MIN_SAMPLE && o.credits<=r.credits && o.succ>=r.succ
      && (o.credits<r.credits || o.succ>r.succ));
    if (dom) return ['被支配','gray'];
    if (r===base) return r.succ>=85 ? ['默认推荐','green'] : ['成功率不足','red'];
    if (r.mce===null) return ['基线','gray'];
    if (r.succ < base.succ + 1.5) return ['无显著增益','gray'];
    if (r.mce >= 8) return ['推荐升级','green'];
    if (r.mce >= 4) return ['仅关键步骤','amber'];
    return ['性价比低','red'];
  }

  let h = cardHTML('对比明细', '复杂度 <b>'+CH_LEVEL+'</b> · 本机真实观测 · 样本 <b>'+SCEN_META[CH_LEVEL].samples+'</b> 个任务',
    '<div class="tbl-wrap" style="max-height:none"><table class="tbl dense"><thead><tr>'
    + '<th>模型</th><th>定位</th><th style="text-align:right">样本</th><th style="text-align:right">成功率</th>'
    + '<th style="text-align:right">积分/任务</th><th style="text-align:right">端到端</th><th style="text-align:right">推理</th>'
    + '<th style="text-align:right">工具等待</th><th style="text-align:right">步数/任务</th>'
    + '<th style="text-align:right">成功率增益/百积分</th><th>判定</th>'
    + '</tr></thead><tbody>'
    + rows.map(r=>{
        const m = M[r.model] || {color:'var(--text2)', name:r.model, tag:'gray', tier:'—'};
        const v = verdict(r);
        const succColor = r.succ>=90?'var(--ok)':r.succ>=85?'var(--text)':'var(--bad-11)';
        return '<tr>'
          + '<td class="name"><i class="dotm" style="background:'+m.color+'"></i>'+m.name+'</td>'
          + '<td><span class="tag '+m.tag+' sm">'+m.tier+'</span></td>'
          + '<td class="num" style="color:'+(r.n>=MIN_SAMPLE?'var(--text2)':'var(--warn-soft)')+'">'+r.n+'</td>'
          + '<td class="num" style="color:'+succColor+';font-weight:600">'+r.succ.toFixed(1)+'%</td>'
          + '<td class="num">'+fmt0(r.credits)+'</td>'
          + '<td class="num">'+r.e2e+'s</td>'
          + '<td class="num" style="color:var(--text2)">'+r.infer+'s</td>'
          + '<td class="num" style="color:var(--text2)">'+r.wait+'s</td>'
          + '<td class="num" style="color:var(--text2)">'+r.steps+'</td>'
          + '<td class="num" style="color:'+(r.mce===null?'var(--text3)':r.mce>=8?'var(--ok)':r.mce>=4?'var(--warn-soft)':'var(--bad-11)')+'">'
            + (r.mce===null?'基线':r.mce.toFixed(2)+' pp')+'</td>'
          + '<td><span class="tag '+v[1]+' sm">'+v[0]+'</span></td>'
          + '</tr>';
      }).join('')
    + '</tbody></table></div>', 'mt');

  h += '<div class="hint '+(rows.some(r=>r.n<MIN_SAMPLE)?'warn':'')+'" style="margin-top:12px">'+conclusion(rows)+'</div>';
  $('#benchTable').innerHTML = h;
}

function conclusion(rows){
  const solid = rows.filter(r=>r.n>=MIN_SAMPLE);
  if (solid.length < 2){
    return '<b>结论：</b>'+CH_LEVEL+' 当前只有 '+solid.length+' 个模型达到 '+MIN_SAMPLE+' 个以上样本，'
      + '不具备可比性，本页不给换模型结论。建议对同类任务连续跑 '+MIN_SAMPLE+'—5 轮后再看。';
  }
  const cheap = solid.reduce((a,b)=>a.credits<b.credits?a:b);
  const best = solid.reduce((a,b)=>a.succ>b.succ?a:b);
  if (cheap === best){
    return '<b>结论：</b><b>'+cheap.model+'</b> 在 '+CH_LEVEL+' 同时做到最低积分（'+fmt0(cheap.credits)
      +'）与最高成功率（'+cheap.succ+'%），无需换模型；若追求更短时延可继续观察工具等待占比。';
  }
  const gap = Math.round((best.succ-cheap.succ)*10)/10;
  const times = cheap.credits>0 ? (best.credits/cheap.credits) : 0;
  if (gap <= 3){
    return '<b>结论：</b><b>'+cheap.model+'</b>（'+fmt0(cheap.credits)+' 积分 / '+cheap.succ+'%）'
      + '相比 <b>'+best.model+'</b>（'+fmt0(best.credits)+' 积分 / '+best.succ+'%）成功率仅低 '
      + gap+'pp，成本为 '+(times?times.toFixed(1)+' 分之 1':'更低')+'，落在 3pp 质量阈值内——'
      + '该类任务优先用低成本模型，仅把关键步骤留给高成功率模型。';
  }
  return '<b>结论：</b><b>'+best.model+'</b> 成功率高出 '+gap+'pp（'+best.succ+'% vs '+cheap.succ+'%），'
    + '但积分是 '+times.toFixed(1)+' 倍（'+fmt0(best.credits)+' vs '+fmt0(cheap.credits)+'），'
    + '超过 3pp 阈值，属于"用钱换成功率"。建议按步骤拆分：规划/校验用高成功率模型，抽取/格式化/汇总用低成本模型。';
}
