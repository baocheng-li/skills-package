/* ================= 审计控制（二级页面） =================
   四个二级页面里，执行监测在 p07_exec.js，本文件负责另外三个：

     任务设置  定时任务（按点触发）+ 预警任务（越线触发）。列表 + 弹窗编辑：
               新建 / 编辑 / 删除 / 启停都在弹窗与行内完成，页面只出配置，
               **不会真的注册系统任务**。
     收入配置  收入记录列表 + 人工录入（凭证哈希 + 哈希链留痕 + 与账本比对）。
     回收调整  异常处置的账面口径：已消耗不可追回，只做确认 / 止损 / 归因 / 留证。

   一个共同前提：报告是**离线单文件**，不能写盘、不能注册系统任务、不能联网。
   所以凡是「保存」，都是「存本机浏览器 + 导出配置」两步走，
   并在界面上把这句话写清楚，不让人以为点一下就生效。 */

/* ---------- 小工具 ---------- */
function ctlNote(text){
  return '<div class="ctl-note">'+text+'</div>';
}
function copyText(txt){
  const done = () => toast('已复制到剪贴板');
  if (navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(txt).then(done).catch(()=>fallback());
  } else fallback();
  function fallback(){
    const ta = document.createElement('textarea');
    ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch(e){ toast('复制失败，请手动选择文本'); }
    ta.remove();
  }
}
function lsGet(key, dft){
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : dft;
  } catch(e){ return dft; }
}
function lsSet(key, val){
  try { localStorage.setItem(key, JSON.stringify(val)); return true; }
  catch(e){ toast('本机存储不可用，改动仅在本次会话有效'); return false; }
}
function tsStamp(){
  const d = new Date(), p = n => String(n).padStart(2,'0');
  return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '
    + p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());
}
function todayISO(off){
  const d = new Date();
  if (off) d.setDate(d.getDate() + off);
  const p = n => String(n).padStart(2,'0');
  return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());
}
/* 弹窗由三处共用（定时更新 / 执行明细 / 任务编辑 / 异常处置），
   各自只负责填 #cfgBody 与 #cfgFoot，开关统一走这两个函数。 */
function ctlOpenModal(){
  const m = document.getElementById('cfgModal'), k = document.getElementById('cfgMask');
  if (m) m.classList.add('on');
  if (k) k.classList.add('on');
}
function ctlCloseModal(){
  const m = document.getElementById('cfgModal'), k = document.getElementById('cfgMask');
  if (m) m.classList.remove('on');
  if (k) k.classList.remove('on');
}
function ctlModalHead(title, sub){
  const m = document.getElementById('cfgModal');
  if (!m) return;
  const hd = m.querySelector('.modal-hd h3'), s = m.querySelector('.modal-hd .sub');
  if (hd) hd.textContent = title;
  if (s) s.innerHTML = sub || '';
}

/* ============================================================
   一、任务设置
   ------------------------------------------------------------
   数据分三层，缺一层就会出现「编辑了没生效、删了又回来」：
     1) 采集得到的定时任务（MON.configs）与内置预警规则 —— **只读基座**；
     2) 页面新建的任务（custom）—— 整条由页面持有；
     3) 对任意任务的改动（edit）与删除（del）—— 按 id 覆盖基座。
   保存时只写 2、3 两层，基座永远不动，所以下次采集进来新任务也不会被覆盖掉。 */
const TS = {kw:'', kind:'all', st:'all'};
const TS_KEY = 'wbc.taskset.v2';

function tsBlank(){ return {custom:[], edit:{}, del:[], scripts:{}, alerts:null}; }
function tsLoad(){
  const v = lsGet(TS_KEY, null);
  const st = (v && typeof v === 'object') ? v : tsBlank();
  st.custom = st.custom || [];
  st.edit = st.edit || {};
  st.del = st.del || [];
  st.scripts = st.scripts || {};
  st.alerts = st.alerts || null;
  return st;
}
function tsSave(v){ return lsSet(TS_KEY, v); }

/* 预警任务：本地没有配置时，用**当前阈值**派生三条，不凭空造规则 */
function tsDefaultAlerts(){
  const th = (BUDGET.thresholds || {});
  return [
    {id:'a-ratio', name:'预警比越线提醒', kind:'alert', metric:'spendRatio', op:'>=',
     value:(th.warn || 10), action:'重跑采集并标记', on:true,
     desc:'预警比（今日消耗 / 当前余额）越过阈值时提醒，用于当天止损'},
    {id:'a-bal', name:'可用余额下限提醒', kind:'alert', metric:'balance', op:'<=',
     value:100, action:'重跑采集并标记', on:true,
     desc:'当前余额跌破下限时提醒，避免任务跑到一半断供'},
    {id:'a-t1', name:'T+1 对账偏差提醒', kind:'alert', metric:'reconDiff', op:'>',
     value:(CRED.tolerance || 5), action:'生成对账工单', on:true,
     desc:'日终对账偏差超过容差时提醒，定位是漏记还是重复扣'}
  ].map(a=>Object.assign(a, {freq:a.metric+' '+a.op+' '+a.value}));
}
function tsAlerts(st){
  const list = (st && st.alerts && st.alerts.length) ? st.alerts : tsDefaultAlerts();
  return list.map(a=>Object.assign({}, a, {freq:a.freq || (a.metric+' '+a.op+' '+a.value)}));
}

/* 定时任务：把 MON.configs 与 SCHED.items 按名字合并，拿到「配置 + 实测」两面 */
function tsSchedRows(){
  const byName = {};
  (SCHED.items || []).forEach(x=>{ byName[x.name] = x; });
  return (MON.configs || []).map(c=>{
    const s = byName[c.name] || {};
    return {
      id:'s:' + c.name, name:c.name, kind:'sched',
      freq:c.freq || '—', rrule:c.rrule || '',
      status:c.status || '—',
      lastRun:c.lastRun || '—', model:c.model || '—',
      runs:s.runs != null ? s.runs : (c.runs || 0),
      credits:s.credits != null ? s.credits : (c.credits || 0),
      on:true, desc:'', custom:false
    };
  });
}

/* 系统计划任务（每日取数）——唯一一条真的由操作系统在跑的活儿。
   数据三份：config/schedule.json（打算几点跑）、out/schedule-state.json
   （系统里实际注册成什么）、out/credit-daily.jsonl（真跑过几天）。
   页面按调度层送来的 dailyCredit 原样呈现，不在前端重算任何一份。 */
const DC = (RAW.dailyCredit || {});
/* 系统任务的启用口径**只存在内存里**，不写 localStorage。
   写进去会留下一个坏结果：执行 --enable 之后 config 已经改了，页面上那条旧记录
   还压着，显示的正好是相反的结论。系统任务的权威在 config/schedule.json，
   页面最多表达「我现在想让它停」；重新打开回到系统口径，
   才不会记着一个还没落地的意图。 */
const DCOVER = {};
function dcLastRun(){
  const d = (DC.daily || {}).last || {};
  return d.at ? String(d.at).replace('T', ' ') : '—';
}
function tsDailyRows(){
  return (DC.tasks || []).map(t=>{
    const d = DC.daily || {};
    const id = 'd:' + t.id;
    return {
      id: id, name:t.name, kind:'sched', managed:true, custom:false,
      freq:t.freq || '每天 —', rrule:t.rrule || '', status:'ACTIVE',
      lastRun: t.lastRun || dcLastRun(),
      model: '系统计划任务', runs: d.n || 0, credits: 0,
      on: (DCOVER[id] !== undefined) ? DCOVER[id] : (t.enabled !== false),
      cfgEnabled: t.enabled !== false, desc: t.desc || '',
      at: t.at, systemTask: t.systemTask, scriptAbs: t.scriptAbs || '',
      scriptExists: !!t.scriptExists, installed: t.installed,
      sysState: t.state || '', nextRun: t.nextRun || '', atDrift: !!t.atDrift,
      registeredAt: t.registeredAt || '',
      /* sysEnabled 三态：true 已启用 / false 已停用 / null 没探测到。
         空值不能折成 true —— 「系统里是开的」和「不知道」是两件事。 */
      sysEnabled: (t.sysEnabled === undefined || t.sysEnabled === null) ? null : !!t.sysEnabled,
      registerCmd: t.registerCmd || '', cmd: (t.module || '') + ' ' + (t.args || ''),
      daily: d
    };
  });
}
function dcInstallTag(x){
  if (x.installed === true){
    /* 注册了但被停用，是个真实且容易看错的状态：任务还躺在任务计划程序里，
       只是不会到点拉起。这时候说「已注册、到点会自动执行」就是错的。 */
    return x.sysEnabled === false
      ? '<span class="tag gray sm" title="任务还在系统里，但已停用，不会到点触发">已注册·停用</span>'
      : '<span class="tag green sm" title="系统计划任务里已存在，到点会自动执行">已注册</span>';
  }
  if (x.installed === false) return '<span class="tag amber sm" title="系统里还没有这个任务，只有脚本，不会自动跑">未注册</span>';
  return '<span class="tag gray sm" title="采集时没探测到系统状态，无法判断">未探测</span>';
}

/* 改时间要把命令里的时间一起改掉，否则「页面上写着 10:30、复制出来还是 09:05」 */
function tsRetime(cmd, oldAt, newAt){
  if (!cmd || !newAt || newAt === oldAt) return cmd;
  const win = cmd.indexOf('/ST ' + oldAt) >= 0;
  if (win) return cmd.replace('/ST ' + oldAt, '/ST ' + newAt);
  const om = String(oldAt).split(':'), nm = String(newAt).split(':');
  if (om.length === 2 && nm.length === 2 && cmd.indexOf(om[1] + ' ' + om[0] + ' ') >= 0){
    return cmd.replace(om[1] + ' ' + om[0] + ' ', nm[1] + ' ' + nm[0] + ' ');
  }
  return cmd;
}

function tsAllRows(){
  const st = tsLoad();
  let list = tsDailyRows().concat(tsSchedRows()).concat(tsAlerts(st)).concat((st.custom || []).map(x=>Object.assign({}, x)));
  list = list.filter(x=> (st.del || []).indexOf(x.id) < 0);
  return list.map(x=>{
    const o = (st.edit || {})[x.id];
    const r = o ? Object.assign({}, x, o) : Object.assign({}, x);
    /* 老版本把系统任务的开关也写进了覆盖层，这些历史值今天必须忽略：
       它们记的是某一次点击，而 config 才是执行过命令之后的事实。 */
    if (x.managed) r.on = x.on;
    r.on = r.on !== false;
    r.status = r.on ? 'ACTIVE' : 'PAUSED';
    r.desc = r.desc || '';
    return r;
  });
}

function tsRows(){
  let list = tsAllRows();
  const kw = (TS.kw||'').trim().toLowerCase();
  if (kw) list = list.filter(x=>(x.name||'').toLowerCase().indexOf(kw) >= 0);
  if (TS.kind !== 'all') list = list.filter(x=>x.kind === TS.kind);
  if (TS.st !== 'all'){
    list = list.filter(x=> TS.st === 'on' ? x.on : !x.on);
  }
  return list;
}

function tsFind(id){ return tsAllRows().filter(x=>x.id===id)[0] || null; }

/* 命令生成：Windows / Linux 两套，直接可粘贴。页面不执行，只给文本。 */
function tsCmd(x, os){
  const out = '--out ./out --format all';
  const py = 'python -m hosts.commands.refresh_report ' + out;
  if (os === 'win'){
    const hm = (String(x.freq||'').match(/(\d{1,2}):(\d{2})/) || []);
    const t = hm.length ? (hm[1].padStart(2,'0')+':'+hm[2]) : '09:00';
    return 'schtasks /create /tn "CostAudit\\refresh" /tr "'+py+'" /sc daily /st '+t+' /f';
  }
  return '# crontab -e\n0 9 * * * cd ' + (x.cwd || '/path/to/workbuddy-cost-audit') + ' && ' + py;
}

/* ---------- 弹窗：新建 / 编辑 ---------- */
/* 运行状态：**所有任务共用这一块**，只是每格的值不同。
   以前只有系统任务有这块，普通任务点开是一片空白，同一个列表点出两种弹窗，
   看的人得先判断「这条为什么少几行」。现在字段与顺序完全一致，
   差别只在内容：系统任务多一列日账，普通任务写「仅本机配置」。 */
function taskStateHTML(x, isNew){
  const head = '<div class="sec-t" style="margin-top:12px">运行状态</div>';
  if (isNew){
    return head + '<div class="cfg-note" style="margin:0">新建的任务保存后才进列表，'
      + '此时还没有执行记录；下面的命令可把它注册成系统任务。</div>';
  }
  const d = x.daily || {};
  const sysTag = x.managed ? dcInstallTag(x)
    : '<span class="tag gray sm">仅本机配置</span>';
  const stateTxt = x.sysState ? ' <span class="ctl-hint">' + esc(x.sysState) + '</span>' : '';
  const next = x.nextRun
    ? (x.sysEnabled === false
        ? '<span style="color:var(--warn-11)">已停用，不会触发</span>'
        : esc(x.nextRun))
    : (x.managed ? '—' : '不适用（无系统触发器）');
  const span = (d.span && d.span[1])
    ? '（' + esc(d.span[0]) + ' → ' + esc(d.span[1]) + '）' : '';
  const bad = (d.gapN ? ' · <b style="color:var(--warn-11)">' + d.gapN + ' 天缺口</b>' : '')
    + (d.dupN ? ' · <b style="color:var(--danger-11)">' + d.dupN + ' 条同日重复</b>' : '');
  return head
    + '<div class="cfg-row"><div class="cfg-k">运行位置</div><div class="cfg-v">'
      + (x.managed
          ? '系统计划任务 <span class="mono" style="font-size:10.5px">'
            + esc(x.systemTask || '') + '</span>'
          : '本机配置（随报告打开即生效，不注册系统任务）')
      + '</div></div>'
    + '<div class="cfg-row"><div class="cfg-k">注册状态</div><div class="cfg-v">'
      + sysTag + stateTxt + '</div></div>'
    + '<div class="cfg-row"><div class="cfg-k">解释器 / 脚本</div>'
      + '<div class="cfg-v mono" style="font-size:10.5px">'
      + (x.managed
          ? (x.scriptExists ? esc(x.scriptAbs)
             : '<b style="color:var(--danger-11)">脚本缺失</b>：跑一次 '
               + 'python -m hosts.commands.daily_credit --write-script 生成')
          : (x.scriptAbs ? esc(x.scriptAbs) : '未指定（沿用默认采集脚本）'))
      + '</div></div>'
    + '<div class="cfg-row"><div class="cfg-k">上次执行</div>'
      + '<div class="cfg-v mono" style="font-size:10.5px">' + esc(x.lastRun || '—') + '</div></div>'
    + '<div class="cfg-row"><div class="cfg-k">下次执行</div>'
      + '<div class="cfg-v mono" style="font-size:10.5px">' + next + '</div></div>'
    + '<div class="cfg-row"><div class="cfg-k">执行 / 消耗</div><div class="cfg-v">'
      + (x.runs || 0) + ' 次 · ' + fmt0(x.credits || 0) + ' 积分'
      + (x.managed ? (' · 日账 ' + (d.n || 0) + ' 天' + span + bad) : '')
      + '</div></div>'
    + (x.atDrift
        ? '<div class="cfg-note" style="color:var(--warn-11)">'
          + '<b>配置与系统注册的时间不一致。</b>配置写 '
          + esc(x.at || '—') + '、系统注册 ' + esc(x.registeredAt || '—')
          + '，改完配置要重新注册一次才生效。</div>'
        : '');
}

/* 启停同步：系统任务的开关只改页面口径，系统那边要执行一条命令。
   命令由后端给（schedule.py 的 cmds），前端不拼字符串——
   前端拼一份、CLI 又演化出别的参数，页面上写的就会是一条跑不通的命令。 */
function syncCmdOf(x){
  const c = (DC.cmds || {});
  return x.on ? (c.enable || '') : (c.disable || '');
}

function taskSyncHTML(x){
  if (!x.managed) return '';
  const cmd = syncCmdOf(x);
  /* 页面口径与系统口径可能本来就一致。这时候还催人「执行一次」，
     就是让人做一件没有效果的事——先判断，再决定说什么。 */
  const same = (x.sysEnabled === null) ? null : (x.sysEnabled === x.on);
  const hint = same === true ? '系统里已是这个状态，重复执行也无副作用'
    : (same === false
        ? '系统里还是' + (x.on ? '停用' : '启用') + '，执行一次才同步'
        : '系统状态未探测，执行一次以页面为准');
  return '<div class="sec-t" style="margin-top:12px">启停同步</div>'
    + '<div class="cfg-note" style="margin:0 0 6px">上面的开关改的是<b>页面口径</b>；'
      + '系统任务要执行下面这条命令才会真的跟着停 / 跑。'
      + '一条命令同时改配置、同步系统、回写状态，不会出现两边说法不一。</div>'
    + '<div class="cfg-cmd"><pre id="tmSync">' + esc(cmd) + '</pre></div>'
    + '<div class="ctl-bar" style="margin-top:6px">'
      + '<button class="btn sm" id="tmSyncCopy">复制同步命令</button>'
      + '<button class="btn sm" id="tmSyncDl">下载 .bat</button>'
      + '<span class="ctl-hint" id="tmSyncHint">' + esc(hint) + '</span></div>';
}

function taskModalHTML(x, isNew){
  const kind = x.kind || 'sched';
  const on = x.on !== false;
  const st = tsLoad();
  const script = (st.scripts || {})[x.name] || '';
  const cmd0 = x.managed ? x.registerCmd : tsCmd(x, 'win');
  /* 弹窗里显示的时间是「本机这份配置」，命令里的时间是「系统里那份」。
     两者不一致时以显示的时间为准去改命令——否则用户改完时间点复制，
     粘贴出来的还是旧时间。 */
  const shownAt = (String(x.freq || '').match(/(\d{1,2}):(\d{2})/) || [])[0] || x.at || '';
  const cmd = x.managed ? tsRetime(cmd0, x.at, shownAt) : cmd0;
  const isAlert = kind === 'alert';
  return '<div class="cfg-row"><div class="cfg-k">任务名称</div><div class="cfg-v">'
      + '<input class="cfg-txt" id="tmName" value="'+esc(x.name||'')+'" placeholder="例如：每周积分对账复核"/></div></div>'
    + '<div class="cfg-row"><div class="cfg-k">任务类型</div><div class="cfg-v">'
      + '<select id="tmKind">'
      + [['sched','定时任务（按点触发）'],['alert','预警任务（越线触发）']].map(([k,t])=>
          '<option value="'+k+'"'+(kind===k?' selected':'')+'>'+t+'</option>').join('')
      + '</select></div></div>'
    + '<div class="cfg-row"><div class="cfg-k">触发规则</div><div class="cfg-v">'
      + '<input class="cfg-txt" id="tmFreq" value="'+esc(x.freq||'')+'" '
      + 'placeholder="每天 09:00 / 每 2 小时 / 每周一 08:30"></div></div>'
    + '<div class="cfg-row"><div class="cfg-k">任务控制</div><div class="cfg-v">'
      + '<label class="cfg-sw"><input type="checkbox" id="tmOn"'+(on?' checked':'')+'> '
      + '启用（关闭后不计入执行，也不参与预警）</label></div></div>'
    + '<div class="cfg-row"><div class="cfg-k">模型 / 动作</div><div class="cfg-v">'
      + '<input class="cfg-txt" id="tmModel" value="'+esc(x.model||'')+'" '
      + 'placeholder="定时任务填运行模型，预警任务填触发动作"></div></div>'
    + '<div class="cfg-row" style="align-items:flex-start"><div class="cfg-k" style="padding-top:6px">任务描述</div>'
      + '<div class="cfg-v"><textarea class="cfg-ta" id="tmDesc" rows="2" '
      + 'placeholder="说明任务的具体用途，便于审计时判断该不该存在">'+esc(x.desc||'')+'</textarea></div></div>'
    + taskStateHTML(x, isNew)
    + '<div class="sec-t" style="margin-top:12px">运行脚本（可选）</div>'
    + '<div class="cfg-note" style="margin:0 0 6px">定时任务可导入 / 粘贴 .py / .sh / .bat 脚本；'
      + '<b>导入只读文本，不上传、不执行</b>。</div>'
    + '<div class="cfg-editor"><textarea id="tmScript" rows="6" spellcheck="false" '
      + 'placeholder="留空则沿用默认的 refresh_report 脚本">'+esc(script)+'</textarea></div>'
    + '<div class="ctl-bar"><input type="file" id="tmFile" accept=".py,.sh,.bat,.cmd,.js,.txt" hidden/>'
      + '<button class="btn sm" id="tmImport">导入脚本</button>'
      + '<span class="ctl-hint">改动存本机浏览器；页面不会注册或触发任何系统任务</span></div>'
    + '<div class="sec-t" style="margin-top:12px">注册为系统任务</div>'
    + (isAlert
        ? '<div class="cfg-note" style="margin:0 0 6px">预警任务不注册独立触发器：'
          + '它随每日取数与报告打开时按阈值判定。要让它按点被判一次，'
          + '用下面的命令注册<b>采集任务</b>，采集跑完就会判。</div>' : '')
    + '<div class="cfg-cmd"><pre id="tmCmd" data-at="'+esc(shownAt)+'">'+esc(cmd)+'</pre></div>'
    + '<div class="ctl-bar" style="margin-top:6px">'
      + '<button class="btn sm" id="tmCopy">复制命令</button>'
      + (x.managed ? '<button class="btn sm" id="tmDl">下载注册脚本</button>' : '')
      + '<span class="ctl-hint" id="tmCmdHint">'
      + (x.managed ? '改上面的触发规则，命令里的时间会跟着变' : '把项目目录换成本机实际路径')
      + '</span></div>'
    + '<div class="cfg-note">Windows 在<b>普通权限</b>的终端里执行一次即可（写的是「当前用户」的计划任务，不需要管理员）；'
      + 'macOS / Linux 见 deploy/schedule/README.md 的 crontab 段落。</div>'
    + taskSyncHTML(x)
    + '<div class="cfg-note" style="margin-top:8px">'
    + (x.managed
        ? '<b>这是系统任务：</b>页面只能改本机这份配置；时间、启停、注册都要在上面执行一次命令，'
          + '才会同步到任务计划程序。'
        : (isNew ? '<b>新建任务</b>先写进本机浏览器；要让系统按点跑，执行上面的注册命令。'
                 : '<b>编辑</b>只改本机覆盖值，不改采集到的原始配置；'
                   + '启用状态立即影响列表、统计与预警判定。'))
    + '</div>';
}

function openTaskModal(id){
  const isNew = !id;
  const x = isNew
    ? {id:'', name:'', kind:'sched', freq:'每天 09:00', model:'', desc:'', on:true}
    : tsFind(id);
  if (!x) return;
  ctlModalHead(isNew ? '新建任务' : ('任务编辑 · ' + x.name),
    isNew ? '填写名称、类型、触发规则与用途；保存后出现在任务列表'
      : (x.managed ? '系统任务：配置、运行状态、同步命令都在这一页'
                   : '本机任务：配置存本机，启用状态立即生效'));
  const body = document.getElementById('cfgBody');
  const foot = document.getElementById('cfgFoot');
  if (!body || !foot) return;
  body.innerHTML = taskModalHTML(x, isNew);
  body.__tmId = id || '';
  foot.innerHTML = '<span class="ml-auto" style="color:var(--text3);font-size:10.5px">'
    + '只写本机配置</span>'
    + '<button class="btn" id="tmCancel">取消</button>'
    + '<button class="btn pri" id="tmSave">'+(isNew?'创建任务':'保存')+'</button>';
  ctlOpenModal();
  bindTaskModal();
}

function bindTaskModal(){
  const body = document.getElementById('cfgBody');
  if (!body || body.__tmbound) return;
  body.__tmbound = true;
  body.addEventListener('change', e=>{
    if (e.target.id === 'tmFile'){
      const f = e.target.files && e.target.files[0];
      if (!f) return;
      if (f.size > 512 * 1024){ toast('脚本超过 512 KB，请确认不是误选'); return; }
      const fr = new FileReader();
      fr.onload = () => {
        const ta = document.getElementById('tmScript');
        if (ta) ta.value = String(fr.result || '');
        toast('已读入 ' + f.name + '（' + Math.round(f.size/1024) + ' KB）· 保存后才会留在本机');
      };
      fr.readAsText(f);
    }
    if (e.target.id === 'tmOn'){
      /* 开关一动，同步命令就得跟着换：停在「停用」却给出启用命令，
         是那种照着做也不会出错的错——只是做了相反的事。 */
      const pre = document.getElementById('tmSync');
      const c = (DC.cmds || {});
      if (pre) pre.textContent = e.target.checked ? (c.enable || '') : (c.disable || '');
      const hint = document.getElementById('tmSyncHint');
      if (hint) hint.textContent = e.target.checked
        ? '执行后系统任务恢复为启用' : '执行后系统任务变为停用';
    }
  });
  body.addEventListener('input', e=>{
    if (e.target.id !== 'tmFreq') return;
    const pre = document.getElementById('tmCmd');
    if (!pre) return;
    const at = (String(e.target.value || '').match(/(\d{1,2}):(\d{2})/) || [])[0];
    if (!at) return;
    pre.textContent = tsRetime(pre.textContent, pre.dataset.at || at, at);
    pre.dataset.at = at;
    const hint = document.getElementById('tmCmdHint');
    if (hint) hint.textContent = '命令已改成 ' + at + '；执行一次才写入系统';
  });
  body.addEventListener('click', e=>{
    if (e.target.id === 'tmImport'){
      const f = document.getElementById('tmFile');
      if (f) f.click();
      return;
    }
    if (e.target.id === 'tmCopy'){
      const pre = document.getElementById('tmCmd');
      if (pre) copyText(pre.textContent);
      return;
    }
    if (e.target.id === 'tmDl'){
      const pre = document.getElementById('tmCmd');
      if (!pre) return;
      /* 只包一层 @echo off + pause：双击就能注册，出问题也停在那儿能看见报错。
         不写成 .ps1 —— 执行策略会拦，用户还得先改策略才能跑。 */
      downloadBat(pre.textContent, 'register-daily-credit.bat', DC.root);
      toast('已下载注册脚本 · 双击执行一次即可');
      return;
    }
    if (e.target.id === 'tmSyncCopy'){
      const pre = document.getElementById('tmSync');
      if (pre) copyText(pre.textContent);
      return;
    }
    if (e.target.id === 'tmSyncDl'){
      const pre = document.getElementById('tmSync');
      if (!pre) return;
      downloadBat(pre.textContent, 'daily-credit-sync.bat', DC.root);
      toast('已下载同步脚本 · 双击执行一次即可');
      return;
    }
  });
  const foot = document.getElementById('cfgFoot');
  if (foot && !foot.__tmbound){
    foot.__tmbound = true;
    foot.addEventListener('click', e=>{
      if (e.target.id === 'tmCancel'){ ctlCloseModal(); return; }
      if (e.target.id === 'tmSave') taskModalSave(body.__tmId || '');
    });
  }
}

function taskModalSave(id){
  const g = n => document.getElementById(n);
  const name = ((g('tmName')||{}).value || '').trim();
  if (!name){ toast('任务名称必填'); return; }
  const rec = {
    name: name,
    kind: (g('tmKind')||{}).value || 'sched',
    freq: ((g('tmFreq')||{}).value || '').trim(),
    model: ((g('tmModel')||{}).value || '').trim(),
    desc: ((g('tmDesc')||{}).value || '').trim(),
    on: !!(g('tmOn') && g('tmOn').checked)
  };
  const x0 = id ? tsFind(id) : null;
  const managed = !!(x0 && x0.managed);
  /* 系统任务的启用值不进覆盖层：它只对本轮查看有效，真值在 config 里。
     其它字段（名称 / 时间 / 描述）照常覆盖。 */
  const needSync = managed && rec.on !== x0.on;
  if (managed){
    if (needSync) DCOVER[id] = rec.on;
    delete rec.on;
  }
  const st = tsLoad();
  const isCustom = !!id && (st.custom || []).some(c=>c.id === id);
  if (isCustom){
    /* 本机新建的任务整条归页面所有，直接改自己那份，不再走覆盖层——
       否则同一个任务有两处真源，改完看列表还得先想「哪份生效」 */
    st.custom = (st.custom || []).map(c=> c.id === id ? Object.assign({}, c, rec) : c);
  } else if (id){
    st.edit[id] = Object.assign({}, (st.edit || {})[id] || {}, rec);
  } else {
    rec.id = 'u:' + Date.now().toString(36);
    rec.custom = true;
    rec.runs = 0; rec.credits = 0; rec.lastRun = '—';
    st.custom = (st.custom || []).concat([rec]);
  }
  const script = ((g('tmScript')||{}).value || '').trim();
  if (script) st.scripts[name] = script;
  tsSave(st);
  ctlCloseModal();
  toast(id ? ('已保存任务「'+name+'」' + (needSync ? ' · 启停要给系统一条命令才跟上' : ''))
           : ('已创建任务「'+name+'」· 仅存本机'));
  buildTaskSet();
  if (needSync) openSyncModal(id);
}

function taskDelete(id){
  const x = tsFind(id);
  if (!x) return;
  if (x.managed){
    /* 系统任务不从列表里藏：藏掉一个还在系统里跑的任务，比多一个按钮危险得多。
       这里给的是能真的把它拿掉的那条命令。 */
    const cmd = (DC.cmds || {}).uninstall
      || 'python -m hosts.commands.daily_credit --uninstall';
    if (!window.confirm('「' + x.name + '」是系统计划任务，报告里删不掉一个正在系统里注册的任务。\n\n'
      + '· 只想让它别跑 → 用行末的开关（会给同步命令）\n'
      + '· 彻底移除   → 执行卸载命令\n\n'
      + '点「确定」把卸载命令复制到剪贴板。')) return;
    copyText(cmd);
    return;
  }
  if (!window.confirm('删除任务「' + x.name + '」？\n\n只删除本机配置，不影响 WorkBuddy 里已注册的系统任务。')) return;
  const st = tsLoad();
  const ci = (st.custom || []).map(c=>c.id).indexOf(id);
  if (ci >= 0) st.custom.splice(ci, 1);
  else st.del = (st.del || []).concat([id]);
  delete (st.edit || {})[id];
  tsSave(st);
  toast('已删除「'+x.name+'」（本机配置）');
  buildTaskSet();
}

function taskToggle(id){
  const x = tsFind(id);
  if (!x) return;
  const next = !x.on;
  if (x.managed){
    /* 系统任务：页面口径只改这一次查看，不落盘。
       落盘的话，命令执行后 config 变了、页面上那条旧记录还压着，
       显示的正好是相反的结论。 */
    DCOVER[id] = next;
    buildTaskSet();
    openSyncModal(id);
    return;
  }
  const st = tsLoad();
  if ((st.custom || []).some(c=>c.id === id)){
    st.custom = st.custom.map(c=> c.id === id ? Object.assign({}, c, {on:next}) : c);
  } else {
    st.edit[id] = Object.assign({}, (st.edit || {})[id] || {}, {on: next});
  }
  tsSave(st);
  buildTaskSet();
  toast('已' + (next ? '启用' : '停用') + '「' + x.name + '」· 已生效，不计入执行与预警');
}

/* 把一段命令包成能双击的 .bat：cd 回项目根，跑完停住等回车。
   只在项目路径是纯 ASCII 时才写 cd —— 含中文的路径写进 .bat，
   cmd.exe 会按 OEM 代码页解析错行，与其生成一个跑不起来的脚本，不如不写。 */
function downloadBat(cmd, name, root){
  const lines = ['@echo off',
    'rem CostAudit scheduled task helper. Safe to run twice.',
    'rem ASCII only: cmd.exe parses .bat with the OEM code page.'];
  if (root && !/[^\x00-\x7F]/.test(root)) lines.push('cd /d "' + root + '"');
  lines.push(cmd.trim(), 'echo.', 'pause');
  const blob = new Blob([lines.join('\r\n') + '\r\n'], {type: 'application/octet-stream'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(()=>{ try{ URL.revokeObjectURL(a.href); }catch(e){} a.remove(); }, 120);
}

/* 同步弹窗：系统任务的开关点了之后出现的这一步。
   只回答三件事——页面现在是什么、系统现在是什么、执行哪条命令能让两边一致。 */
function openSyncModal(id){
  const x = tsFind(id);
  if (!x) return;
  const cmd = syncCmdOf(x);
  const sysTxt = x.sysEnabled === null
    ? '未探测（采集时没取到系统状态）'
    : (x.sysEnabled ? '启用（' + (x.sysState || 'Ready') + '）' : '停用（Disabled）');
  ctlModalHead('同步到系统 · ' + x.name, '页面口径已改，系统任务要执行一次命令才跟上');
  const body = document.getElementById('cfgBody');
  const foot = document.getElementById('cfgFoot');
  if (!body || !foot) return;
  body.innerHTML =
      '<div class="cfg-row"><div class="cfg-k">页面口径</div><div class="cfg-v">'
    + (x.on ? '<span class="tag green sm">启用</span>' : '<span class="tag gray sm">停用</span>')
    + ' <span class="ctl-hint">列表与统计已按这个口径算</span></div></div>'
    + '<div class="cfg-row"><div class="cfg-k">系统口径</div><div class="cfg-v">'
    + esc(sysTxt)
    + (x.nextRun && x.sysEnabled !== false
        ? ' <span class="ctl-hint">下次 ' + esc(x.nextRun) + '</span>' : '')
    + '</div></div>'
    + '<div class="cfg-row"><div class="cfg-k">执行后</div><div class="cfg-v">'
    + (x.on ? '系统任务恢复按点执行' : '系统任务立即停止触发')
    + '，状态回写 out/schedule-state.json</div></div>'
    + '<div class="cfg-cmd" style="margin-top:8px"><pre id="syncCmd">' + esc(cmd) + '</pre></div>'
    + '<div class="ctl-bar" style="margin-top:6px">'
      + '<button class="btn sm" id="syncCopy">复制命令</button>'
      + '<button class="btn sm" id="syncDl">下载 .bat</button>'
      + '<span class="ctl-hint">在项目目录执行一次即可</span></div>'
    + '<div class="cfg-note">这条命令同时改 config/schedule.json、同步任务计划程序、'
      + '回写 out/schedule-state.json —— 三处一次对齐，不会出现「配置说停了、系统还在跑」。</div>';
  foot.innerHTML = '<span class="ml-auto" style="color:var(--text3);font-size:10.5px">'
    + '页面不会自己执行命令</span>'
    + '<button class="btn pri" id="syncClose">知道了</button>';
  ctlOpenModal();
  bindSyncModal();
}

function bindSyncModal(){
  const body = document.getElementById('cfgBody');
  const foot = document.getElementById('cfgFoot');
  if (body && !body.__syncbound){
    body.__syncbound = true;
    body.addEventListener('click', e=>{
      const pre = document.getElementById('syncCmd');
      if (e.target.id === 'syncCopy'){ if (pre) copyText(pre.textContent); return; }
      if (e.target.id === 'syncDl'){
        if (!pre) return;
        downloadBat(pre.textContent, 'daily-credit-sync.bat', DC.root);
        toast('已下载同步脚本 · 双击执行一次即可');
      }
    });
  }
  if (foot && !foot.__syncbound){
    foot.__syncbound = true;
    foot.addEventListener('click', e=>{
      if (e.target.id === 'syncClose') ctlCloseModal();
    });
  }
}

const ICO = {
  ed:'<svg viewBox="0 0 14 14" aria-hidden="true">'
    + '<path d="M9.3 2.2l2.5 2.5-7 7H2.3V9.2l7-7z"/><path d="M8 3.5l2.5 2.5"/></svg>',
  del:'<svg viewBox="0 0 14 14" aria-hidden="true">'
    + '<path d="M2.3 4h9.4M5.4 4V2.3h3.2V4M4 4l.5 8.2h5L10 4M5.9 6.4v3.6M8.1 6.4v3.6"/></svg>'
};

/* 操作列：编辑 / 删除 / 启停，**每一行都齐全且同款**。
   系统任务的删除点下去不是把行藏起来，而是给出卸载命令——
   按钮一致、行为按事实说话：藏掉一个还在系统里跑的任务，是审计报告最不该干的事。 */
function tsOpsHTML(x, active){
  return '<div class="ts-ops">'
    + '<button class="icob" data-tse="'+esc(x.id)+'" title="编辑任务" aria-label="编辑">'
      + ICO.ed + '</button>'
    + '<button class="icob danger" data-tsd="'+esc(x.id)+'" title="'
      + (x.managed ? '删除（系统任务给出卸载命令）' : '删除任务') + '" aria-label="删除">'
      + ICO.del + '</button>'
    + '<button class="switch'+(active?' on':'')+'" data-tst="'+esc(x.id)+'" role="switch" '
      + 'aria-checked="'+(active?'true':'false')+'" title="'
      + (active?'已启用，点击停用':'已停用，点击启用')+'"></button>'
    + '</div>';
}

/* 状态列：页面口径的加 / 停 + 系统口径的注册状态。
   两边不一致时**不再并排显示两个标签**——那会让人以为其中一个是历史值，
   直接换成一个「待同步」，配 title 说清系统那边是什么。 */
function tsStatusCell(x, active){
  let h = '<span class="tag '+(active?'green':'gray')+' sm">'
    + (active?'启用':'禁用') + '</span>';
  if (x.managed){
    const drift = (x.sysEnabled !== null) && (x.sysEnabled !== active);
    h += drift
      ? '<span class="tag amber sm" title="页面口径与系统不一致：系统里仍是'
        + (x.sysEnabled?'启用':'停用') + '，执行同步命令后才生效">待同步</span>'
      : dcInstallTag(x);
  }
  return h;
}

function buildTaskSet(){
  const rows = tsRows();
  const all = tsAllRows();
  const schedN = all.filter(x=>x.kind==='sched').length;
  const alertN = all.filter(x=>x.kind==='alert').length;
  const onN = all.filter(x=>x.on !== false).length;

  let h = ctlNote('<b>每一行的操作都一样：编辑 / 删除 / 启停。</b>定时与预警任务的启停'
    + '<b>立即生效</b>——停用后不计入执行、不参与预警，KPI 与筛选同步变化。'
    + '「每日积分取数与日账」是唯一由操作系统在跑的：它的开关改的是<b>页面口径</b>，'
    + '点完会给一条同步命令，执行一次系统才真的跟着停 / 跑；两边不一致时状态列显示「待同步」。');

  h += '<div class="grid g4 ctl-kpi">'
    + kpiMini('定时任务', fmt0(schedN), '个', 'var(--warn-9)', '按触发规则定时执行')
    + kpiMini('预警任务', fmt0(alertN), '个', 'var(--iris-11)', '达到阈值后自动触发')
    + kpiMini('启用中', fmt0(onN), '个', 'var(--ok-11)', '禁用后不计入执行')
    + kpiMini('窗口消耗', fmt0((SCHED.credits||0)), '积分', 'var(--info-9)',
        '仅定时任务实测，不含预警触发的重跑')
    + '</div>';

  h += '<div class="card ctl-list"><div class="card-hd">'
    + '<div class="card-title">任务列表</div>'
    + '<div class="card-sub">定时按点跑 · 预警越线跑</div>'
    + '<button class="btn pri sm" id="tsNew" style="margin-left:auto">新建任务</button></div>'
    + '<div class="srow ctl-filter">'
      + '<input id="tsKw" class="fld-input" placeholder="搜索任务名" value="'+esc(TS.kw)+'"/>'
      + '<div class="seg" id="tsKind">'
        + [['all','全部'],['sched','定时'],['alert','预警']].map(([k,t])=>
            '<button data-k="'+k+'" class="'+(TS.kind===k?'on':'')+'">'+t+'</button>').join('')
      + '</div>'
      + '<div class="seg" id="tsSt">'
        + [['all','全部'],['on','启用'],['off','禁用']].map(([k,t])=>
            '<button data-s="'+k+'" class="'+(TS.st===k?'on':'')+'">'+t+'</button>').join('')
      + '</div>'
      + '<button class="btn sp" id="tsReset">清除筛选</button>'
    + '</div>';

  let tbl = '<div class="tbl-wrap"><table class="tbl ts-tbl"><thead><tr>'
    + '<th style="width:19%">任务名称</th><th style="width:8%">类型</th><th style="width:12%">触发规则</th>'
    + '<th style="width:13%">上次执行</th><th style="width:13%">状态</th>'
    + '<th class="num" style="width:7%">执行次数</th>'
    + '<th class="num" style="width:7%">消耗积分</th><th style="width:9%">模型 / 动作</th>'
    + '<th style="width:104px;text-align:left">操作</th>'
    + '</tr></thead><tbody>';
  if (!rows.length){
    tbl += '<tr><td colspan="9" style="text-align:center;color:var(--text3);padding:28px">'
      + '当前筛选下没有任务</td></tr>';
  }
  rows.forEach(x=>{
    const active = (x.status||'').toUpperCase() === 'ACTIVE';
    tbl += '<tr class="'+((x.custom?'is-custom':(x.managed?'is-system':'')) + (active?'':' is-off'))+'">'
      + '<td class="name" title="'+esc(x.name+(x.desc?' · '+x.desc:''))+'">'+esc(x.name)+'</td>'
      + '<td><span class="tag '+(x.kind==='sched'?'amber':'purple')+' sm">'
        + (x.kind==='sched'?'定时任务':'预警任务')+'</span></td>'
      + '<td class="mono" style="font-size:10.5px;color:var(--text2)" title="'+esc(x.freq||'')+'">'
        + esc(x.freq||'—')+'</td>'
      + '<td class="mono" style="font-size:10.5px;color:var(--text3)">'+esc(x.lastRun||'—')+'</td>'
      + '<td class="ts-state">' + tsStatusCell(x, active) + '</td>'
      + '<td class="num">'+(x.runs||0)+'</td>'
      + '<td class="num">'+fmt0(x.credits||0)+'</td>'
      + '<td class="mono" style="font-size:10.5px;color:var(--text3)">'+esc(x.model||'—')+'</td>'
      + '<td>' + tsOpsHTML(x, active) + '</td>'
      + '</tr>';
  });
  tbl += '</tbody></table></div>';
  h += tbl + '</div>';

  const el = document.getElementById('v-control-task');
  el.innerHTML = h;
  bindTaskSet(el);
}

function bindTaskSet(el){
  if (el.__bound) return;
  el.__bound = true;
  el.addEventListener('click', e=>{
    if (e.target.id === 'tsNew'){ openTaskModal(''); return; }
    if (e.target.id === 'tsReset'){
      Object.assign(TS, {kw:'', kind:'all', st:'all'});
      buildTaskSet(); return;
    }
    const k = e.target.closest('#tsKind button');
    if (k){ TS.kind = k.dataset.k; buildTaskSet(); return; }
    const s = e.target.closest('#tsSt button');
    if (s){ TS.st = s.dataset.s; buildTaskSet(); return; }
    const ed = e.target.closest('[data-tse]');
    if (ed){ openTaskModal(ed.dataset.tse); return; }
    const del = e.target.closest('[data-tsd]');
    if (del){ taskDelete(del.dataset.tsd); return; }
    const tg = e.target.closest('[data-tst]');
    if (tg){ taskToggle(tg.dataset.tst); return; }
  });
  el.addEventListener('keydown', e=>{
    if (e.target.id === 'tsKw' && e.key === 'Enter'){
      TS.kw = e.target.value; buildTaskSet();
    }
  });
}

/* ============================================================
   二、收入配置
   ------------------------------------------------------------
   人工录入的信任来自三件事，缺一条都只是「自己说自己对」：
     1) 凭证快照的 SHA256 —— 事后改一个字节就对不上；
     2) 哈希链 —— 每条记 prev_hash，删一条或改一个字，链就断；
     3) 与账本比对 —— 录的数要和采集到的收入流水对得上，对不上要显式标注。
   所以按钮不是「生成命令 / 导出 JSON」，而是「记录留痕」：
   先算哈希、再比对、最后落进本机链；要带走的是留痕包，不是一条裸 JSON。 */
const IC_SRC = ['购买','签到领取','任务领取','其他'];
/* 页面的「来源类型」是给人看的；落库按 income_entry.py 的三类走，
   映射写死在表里，不让人在两边各填一套。 */
const IC_TYPE = {'购买':'recharge','签到领取':'grant','任务领取':'grant','其他':'grant'};
const IC = {kw:'', from:'', to:'', st:'all', page:1,
            form:{date:'', amount:'', src:'购买', memo:'', shot:'', sha:'', bytes:0},
            src:{name:'', url:'', auth:'Bearer', token:'', map:'{"date":"date","amount":"credits"}'},
            cmp:null, chain:null};
const IC_KEY = 'wbc.income.v2';

function icLoad(){
  const v = lsGet(IC_KEY, null) || {};
  return {
    trail: Array.isArray(v.trail) ? v.trail : [],
    src: v.src || null,
    form: v.form || null
  };
}
function icPersist(patch){
  const cur = icLoad();
  lsSet(IC_KEY, Object.assign(cur, patch || {}));
}

function icRecords(){
  let list = (CRED.flow || []).filter(r=>r.dir === 'in');
  const kw = (IC.kw||'').trim().toLowerCase();
  if (kw) list = list.filter(r=>(r.name||'').toLowerCase().indexOf(kw) >= 0
    || (r.txn||'').toLowerCase().indexOf(kw) >= 0);
  if (IC.from) list = list.filter(r=>(r.date||'') >= IC.from);
  if (IC.to) list = list.filter(r=>(r.date||'') <= IC.to);
  if (IC.st !== 'all') list = list.filter(r=>(r.entryStatus||'confirmed') === IC.st);
  return list.sort((a,b)=>(b.ts||'').localeCompare(a.ts||''));
}

function icQuality(){
  const Q = CRED.incomeQuality || {};
  const n = Q.n || 0;
  return [
    ['收入笔数', fmt0(n), '笔', 'var(--info-9)', '已入账 '+(Q.confirmed||0)+' · 草稿 '+(Q.draft||0)],
    ['凭证快照覆盖', n ? pct1((Q.snapshotRate||0)/100) : '—', '', 'var(--ok-11)',
     n ? ('有快照 '+(Q.hashed||0)+' 笔') : '台账为空'],
    ['哈希可复算', n ? pct1((Q.evidenceRate||0)/100) : '—', '', 'var(--iris-11)',
     n ? ('复核通过 '+(Q.evidenceOk||0)+' 笔') : '—'],
    ['留痕覆盖', n ? pct1((Q.trailRate||0)/100) : '—', '', 'var(--warn-9)',
     Q.tampered ? ('⚠ 哈希不符 '+Q.tampered) : (Q.missing ? ('⚠ 凭证缺失 '+Q.missing) : '无异常')]
  ];
}

/* ---------- 哈希链 ---------- */
function icSha256(text){
  if (!(window.crypto && crypto.subtle && crypto.subtle.digest))
    return Promise.reject(new Error('no-subtle'));
  const buf = new TextEncoder().encode(text);
  return crypto.subtle.digest('SHA-256', buf).then(b=>
    Array.prototype.map.call(new Uint8Array(b), x=>('00'+x.toString(16)).slice(-2)).join(''));
}
/* 规范化：字段顺序与格式固定，否则「同样的内容」会算出不同哈希 */
function icCanon(r){
  return [r.seq, r.at, r.date, (+r.credits).toFixed(2), r.source, r.memo || '',
          r.file || '', r.sha || ''].join('\u0001');
}
function icHashOf(prev, r){
  return icSha256(String(prev || 'GENESIS') + '\u0002' + icCanon(r));
}
/* 校验整条链：返回第一处断点，而不是只说「有问题」 */
function icVerifyChain(trail){
  let prev = 'GENESIS';
  let p = Promise.resolve({ok:true, bad:0});
  (trail || []).forEach((r, i)=>{
    p = p.then(acc=>{
      if (!acc.ok) return acc;
      if (r.prev_hash !== prev) return {ok:false, bad:i+1};
      return icHashOf(prev, r).then(h=>({ok: h === r.hash, bad: h === r.hash ? 0 : i+1}))
        .then(res=>{
          if (!res.ok) return res;
          prev = r.hash;
          return {ok:true, bad:0};
        });
    });
  });
  return p.catch(()=>({ok:false, bad:0, unsupported:true}));
}

/* ---------- 与账本比对 ----------
   比的是「同一天是否存在同一笔」，不是「当日合计相等」：
   一天可能有多笔收入，拿单笔去和合计比，永远对不上。
   对不上时不止说「没找到」，还给出当日结转证据（收入几笔、余额怎么变的），
   否则「不一致」三个字用户没法判断是漏记还是重复扣。 */
function icCompare(date, amount){
  const led = (CRED.flow || []).filter(r=>r.dir === 'in' && (r.date||'') === date);
  const sum = led.reduce((s,r)=>s+(r.credits||0),0);
  const same = led.filter(r=>Math.abs((r.credits||0) - amount) < 0.005);
  const day = (CRED.days || []).filter(d=>d.date === date)[0] || null;
  const r2 = n => Math.round((n||0)*100)/100;
  const out = {
    date:date, amount:r2(amount), n:led.length,
    sum:r2(sum), same:same.length,
    txn: same.length ? (same[0].txn || '') : '',
    diff: r2(amount - sum),
    day: day ? {income:r2(day.income), incomeN:day.incomeN||0,
                open:r2(day.open), close:r2(day.close),
                spend:r2(day.spend), delta:r2((day.close||0)-(day.open||0))} : null
  };
  if (same.length){
    out.verdict = 'ok';
    out.text = '与账本同一日期 '+same.length+' 笔等额流水一致'
      + (out.txn ? ('（流水号 '+out.txn+'）') : '');
  } else if (led.length){
    out.verdict = 'diff';
    out.text = '账本当日已有 '+led.length+' 笔、合计 '+c2(sum)+' 积分，但没有 '+c2(amount)+' 的对应流水';
  } else {
    out.verdict = 'none';
    out.text = '账本中没有 '+date+' 的收入记录，本次录入无法与流水对上'
      + (out.day
          ? ('；当日账面收入 '+c2(out.day.income)+' 积分，余额由 '+c2(out.day.open)
             +' 变为 '+c2(out.day.close)+'（'+c2(out.day.delta)+'）')
          : '');
  }
  return out;
}

function icCmpHTML(cmp){
  if (!cmp) return '';
  const tone = {ok:'ok', diff:'bad', none:'warn'}[cmp.verdict] || 'warn';
  const tag = {ok:'一致', diff:'不一致', none:'无对应流水'}[cmp.verdict] || '待核对';
  const d = cmp.day;
  return '<div class="ic-cmp '+tone+'">'
    + '<span class="tag '+({ok:'green',diff:'red',none:'amber'}[cmp.verdict]||'gray')+' sm">'+tag+'</span>'
    + '<div class="ic-cmp-t"><b>比对结果：</b>'+esc(cmp.text)+'</div>'
    + '<div class="ic-cmp-m">账本 '+cmp.n+' 笔 · '+c2(cmp.sum)+' 积分 · 本次 '
      + c2(cmp.amount)+' 积分 · 与当日合计差 '+c2(cmp.diff)
      + (d ? ('　｜　当日结转：账面收入 '+c2(d.income)+' · 日耗 '+c2(d.spend)
              +' · 余额 '+c2(d.open)+' → '+c2(d.close)) : '')
    + '</div>'
    + '</div>';
}

function icTrailHTML(trail){
  if (!trail || !trail.length){
    return '<div class="ic-trail-empty">还没有留痕记录。录入一笔后，'
      + '哈希链会把「谁在什么时候录了什么、凭证是哪张」串起来，改一个字就对不上。</div>';
  }
  const rows = trail.slice(-6).reverse().map(r=>{
    const v = (r.cmp||{}).verdict;
    const tag = {ok:['一致','green'], diff:['不一致','red'], none:['无流水','amber']}[v] || ['未比对','gray'];
    return '<tr>'
      + '<td class="mono" style="font-size:10px;color:var(--text3)">'+String(r.seq).padStart(2,'0')+'</td>'
      + '<td class="mono" style="font-size:10px">'+esc(r.date||'')+'</td>'
      + '<td class="num" style="color:var(--ok-10);font-weight:640">'+c2(r.credits)+'</td>'
      + '<td style="font-size:10.5px;color:var(--text2)">'+esc(r.source||'')+'</td>'
      + '<td><span class="tag '+tag[1]+' sm">'+tag[0]+'</span></td>'
      + '<td class="mono" style="font-size:10px;color:var(--text3)" title="'+esc(r.hash||'')+'">'
        + esc(String(r.hash||'').slice(0,10))+'…</td>'
      + '</tr>';
  }).join('');
  return '<div class="ic-trail"><table class="tbl dense"><thead><tr>'
    + '<th style="width:30px">#</th><th style="width:86px">日期</th>'
    + '<th style="text-align:right;width:64px">积分</th><th>来源</th>'
    + '<th style="width:64px">比对</th><th style="width:96px">链哈希</th>'
    + '</tr></thead><tbody>'+rows+'</tbody></table></div>';
}

function buildIncome(){
  const rows = icRecords();
  const page = pgSlice(rows, IC.page, PG_SIZE);
  const sum = rows.reduce((s,r)=>s+(r.credits||0), 0);
  const store = icLoad();
  if (store.src && !IC.src.name) IC.src = Object.assign({}, IC.src, store.src);
  /* 日期默认当天：录今天的收入是常态，默认值不该比用户多点一次 */
  if (!IC.form.date) IC.form.date = todayISO();

  let h = ctlNote('<b>收入只认「已入账」。</b>人工录入必须带凭证截图——页面把文件名、'
    + 'SHA256 与录入时刻写进<b>哈希链留痕</b>，并与账本中同日的收入流水比对；'
    + '链上任一条被改动都会在下次打开时暴露。<b>本页不能写盘</b>：留痕存本机，'
    + '导出留痕包后可交 <code>income_entry.py</code> 落库与复核。');

  h += '<div class="grid g4 ctl-kpi">'
    + icQuality().map(x=>kpiMini(x[0], x[1], x[2], x[3], x[4])).join('')
    + '</div>';

  h += '<div class="card ctl-list"><div class="card-hd">'
    + '<div class="card-title">收入记录</div>'
    + '<div class="card-sub">窗口合计 '+c2(sum)+' 积分 · 每页 '+PG_SIZE+' 行</div></div>'
    + '<div class="srow ctl-filter">'
      + '<input id="icKw" class="fld-input" placeholder="搜索摘要 / 流水号" value="'+esc(IC.kw)+'"/>'
      + '<input id="icFrom" type="date" class="fld-input" style="width:132px" value="'+esc(IC.from)+'"/>'
      + '<input id="icTo" type="date" class="fld-input" style="width:132px" value="'+esc(IC.to)+'"/>'
      + '<select id="icSt" class="fld-input" style="width:110px">'
        + [['all','全部状态'],['confirmed','已入账'],['draft','草稿'],['void','已退回']]
            .map(([k,t])=>'<option value="'+k+'"'+(IC.st===k?' selected':'')+'>'+t+'</option>').join('')
      + '</select>'
      + '<button class="btn sp" id="icReset">清除筛选</button>'
      + '<button class="btn sp" id="icExport" style="margin-left:auto">导出收入</button>'
    + '</div>';

  let tbl = '<div class="tbl-wrap"><table class="tbl"><thead><tr>'
    + '<th>日期</th><th>交易流水号</th><th style="width:30%">摘要</th>'
    + '<th style="text-align:right">积分</th><th>凭证</th><th>状态</th><th>留痕</th>'
    + '</tr></thead><tbody>';
  if (!rows.length){
    tbl += '<tr><td colspan="7" style="text-align:center;color:var(--text3);padding:26px">'
      + (CRED.incomeN ? '当前筛选下没有收入记录' : '窗口内没有已登记的积分收入——右侧可直接录入')
      + '</td></tr>';
  }
  page.rows.forEach(r=>{
    const ST = {confirmed:['已入账','green'], draft:['草稿','amber'], void:['已退回','gray']};
    const st = ST[r.entryStatus || 'confirmed'] || ST.confirmed;
    tbl += '<tr>'
      + '<td class="mono">'+esc(r.date||'')+'</td>'
      + '<td class="mono" style="font-size:10.5px">'+esc(r.txn||'—')+'</td>'
      + '<td class="name" title="'+esc(r.name)+'">'+esc(r.name||'—')+'</td>'
      + '<td class="num" style="font-weight:640;color:var(--ok-10)">'+c2(r.credits)+'</td>'
      + '<td class="mono" style="font-size:10px;color:var(--text3)">'
        + (r.sha ? esc(String(r.sha).slice(0,8)) : (r.entryStatus==='confirmed' ? '缺失' : '—'))+'</td>'
      + '<td><span class="tag '+st[1]+' sm">'+st[0]+'</span></td>'
      + '<td style="color:var(--text3);font-size:10px">'+esc(r.trailN ? (r.trailN+' 条') : '—')+'</td>'
      + '</tr>';
  });
  if (rows.length) tbl += padRows(page.rows.length, 7, PG_SIZE, '第 '+page.page+' 页共 '+page.rows.length+' 笔');
  tbl += '</tbody></table></div>' + pagerHTML(page, 'icp');
  h += tbl + '</div>';

  /* ---- 编辑板块：人工录入 + 数据源配置 ---- */
  const trail = store.trail;
  h += '<div class="ic-edit">'
    + '<div class="card ctl-editor"><div class="card-hd">'
      + '<div class="card-title">人工录入</div>'
      + '<div class="card-sub">凭证截图必填 · 记录留痕并与账本比对</div></div>'
      + '<div class="ctl-form">'
        + '<label class="fld"><span>日期</span><input id="icDate" type="date" value="'+esc(IC.form.date)+'"/></label>'
        + '<label class="fld"><span>积分</span><input id="icAmt" type="number" step="0.01" placeholder="0.00" value="'+esc(IC.form.amount)+'"/></label>'
        + '<label class="fld"><span>来源类型</span><select id="icSrc">'
          + IC_SRC.map(x=>'<option'+(IC.form.src===x?' selected':'')+'>'+x+'</option>').join('')
        + '</select></label>'
      + '</div>'
      + '<div class="ctl-form"><label class="fld wide"><span>说明</span>'
        + '<input id="icMemo" placeholder="例如：10 月购买 1000 积分" value="'+esc(IC.form.memo)+'"/></label></div>'
      + '<div class="ctl-form"><label class="fld wide"><span>凭证截图（必填 · 只在本机读取）</span>'
        + '<input type="file" id="icShot" accept="image/*"/>'
        + '<em class="ic-sha" id="icSha">'+(IC.form.sha ? ('SHA256 '+esc(IC.form.sha.slice(0,16))+'… · '+esc(IC.form.shot)) : '尚未选择截图')
        + '</em></label></div>'
      + '<div class="ctl-bar">'
        + '<button class="btn pri" id="icStamp">记录留痕（比对后入链）</button>'
        + '<button class="btn" id="icExportTrail">导出留痕包</button>'
        + '<span class="ctl-hint" id="icChainSt">链校验中…</span>'
      + '</div>'
      + '<div id="icCmp">'+icCmpHTML(IC.cmp)+'</div>'
      + '<div class="ic-trail-hd"><span>留痕（哈希链 · 最近 6 条）</span>'
        + '<em>共 '+trail.length+' 条</em></div>'
      + icTrailHTML(trail)
      + '<div class="cfg-note">留痕只存本机浏览器；导出留痕包含 <code>prev_hash</code> / <code>hash</code>，'
        + '可用 <code>income_entry.py verify</code> 的同一套规则复算。</div>'
    + '</div>'
    + '<div class="card ctl-editor"><div class="card-hd">'
      + '<div class="card-title">数据源配置</div>'
      + '<div class="card-sub">外部收入数据接入（API）· 采集层读取，报告本身不联网</div></div>'
      + '<div class="ctl-form">'
        + '<label class="fld"><span>名称</span><input id="dsName" placeholder="例如：财务系统积分流水" value="'+esc(IC.src.name)+'"/></label>'
        + '<label class="fld"><span>认证方式</span><select id="dsAuth">'
          + ['Bearer','Basic','Header','无'].map(x=>'<option'+(IC.src.auth===x?' selected':'')+'>'+x+'</option>').join('')
        + '</select></label>'
      + '</div>'
      + '<div class="ctl-form"><label class="fld wide"><span>接口地址</span>'
        + '<input id="dsUrl" placeholder="https://example.com/api/credits?from=2026-10-01" value="'+esc(IC.src.url)+'"/></label></div>'
      + '<div class="ctl-form"><label class="fld wide"><span>令牌 / 头部值（仅存本机浏览器，导出时默认剔除）</span>'
        + '<input id="dsToken" type="password" placeholder="sk-..." value="'+esc(IC.src.token)+'"/></label></div>'
      + '<div class="ctl-form"><label class="fld wide"><span>字段映射（JSON）</span>'
        + '<textarea id="dsMap" rows="3">'+esc(IC.src.map)+'</textarea></label></div>'
      + '<div class="ctl-bar">'
        + '<button class="btn pri" id="dsSave">保存配置</button>'
        + '<button class="btn" id="dsExport">导出配置</button>'
        + '<span class="ctl-hint">导出默认不含令牌</span>'
      + '</div>'
      + '<div class="cfg-note">数据源只描述「去哪儿取数」，取数在采集层完成；'
        + '报告本身不联网，也不会在这里发起请求。</div>'
    + '</div>'
    + '</div>';

  const el = document.getElementById('v-control-income');
  el.innerHTML = h;
  bindIncome(el);
  icChainCheck(store.trail);
}

/* 打开页面时复算一遍链：断在哪一条要能指出来，否则「不可篡改」只是一句话 */
function icChainCheck(trail){
  const out = document.getElementById('icChainSt');
  if (!out) return;
  if (!trail || !trail.length){ out.textContent = '尚无留痕'; return; }
  icVerifyChain(trail).then(res=>{
    if (res.unsupported){ out.textContent = '当前环境不支持本地哈希计算 — 需在 https / localhost 下打开'; return; }
    out.innerHTML = res.ok
      ? ('<span class="ic-chain-ok">链完整</span> · '+trail.length+' 条记录逐条复算通过')
      : ('<span class="ic-chain-bad">链在第 '+res.bad+' 条断开</span> — 该条之后的内容已不可信');
  });
}

function bindIncome(el){
  if (el.__bound) { return; }
  el.__bound = true;
  el.addEventListener('click', e=>{
    if (e.target.id === 'icReset'){
      Object.assign(IC, {kw:'', from:'', to:'', st:'all', page:1});
      buildIncome(); return;
    }
    const pg = e.target.closest('[data-icp]');
    if (pg){ IC.page = Math.max(1, +pg.dataset.icp); buildIncome(); return; }
    if (e.target.id === 'icExport'){ exportCSV('income.csv',
      ['日期','交易流水号','摘要','积分','状态','凭证SHA256'],
      icRecords().map(r=>[r.date||'', r.txn||'', r.name||'', c2(r.credits),
        r.entryStatus||'confirmed', r.sha||''])); return; }
    if (e.target.id === 'icStamp'){ icStamp(); return; }
    if (e.target.id === 'icExportTrail'){ icExportTrail(); return; }
    if (e.target.id === 'dsSave'){ dsSave(); return; }
    if (e.target.id === 'dsExport'){ dsExport(); return; }
  });
  el.addEventListener('keydown', e=>{
    if (e.target.id === 'icKw' && e.key === 'Enter'){ IC.kw = e.target.value; IC.page = 1; buildIncome(); }
  });
  el.addEventListener('change', e=>{
    if (e.target.id === 'icDate'){ IC.form.date = e.target.value || todayISO(); }
    if (e.target.id === 'icAmt'){ IC.form.amount = e.target.value || ''; }
    if (e.target.id === 'icMemo'){ IC.form.memo = e.target.value || ''; }
    if (e.target.id === 'icSrc'){ IC.form.src = e.target.value || IC_SRC[0]; }
    if (e.target.id === 'icFrom'){ IC.from = e.target.value; IC.page = 1; buildIncome(); }
    if (e.target.id === 'icTo'){ IC.to = e.target.value; IC.page = 1; buildIncome(); }
    if (e.target.id === 'icSt'){ IC.st = e.target.value; IC.page = 1; buildIncome(); }
    if (e.target.id === 'icShot') icHash(e.target.files && e.target.files[0]);
  });
}

/* 截图哈希：crypto.subtle 在 file:// 下通常可用；不可用时**明说**，
   而不是给一个看起来像哈希的假值。 */
function icHash(file){
  if (!file) return;
  IC.form.shot = file.name;
  IC.form.sha = '';
  const out = document.getElementById('icSha');
  const done = txt => { if (out) out.textContent = txt; };
  if (!(window.crypto && crypto.subtle && crypto.subtle.digest)){
    done('当前环境不支持本地哈希计算 — 请用 income_entry.py 计算并留存');
    return;
  }
  done('正在计算 SHA256…');
  const fr = new FileReader();
  fr.onload = () => {
    crypto.subtle.digest('SHA-256', fr.result).then(buf=>{
      const hex = Array.prototype.map.call(new Uint8Array(buf),
        b => ('00' + b.toString(16)).slice(-2)).join('');
      IC.form.sha = hex;
      IC.form.bytes = file.size;
      done('SHA256 ' + hex.slice(0,16) + '… · ' + file.name + ' · ' + Math.round(file.size/1024) + ' KB');
    }).catch(()=>done('哈希计算失败 — 请用 income_entry.py 计算'));
  };
  fr.readAsArrayBuffer(file);
}

/* 记录留痕：校验 → 比对 → 入链。三步都是「先做后说」，失败就不入链。 */
function icStamp(){
  const g = n => document.getElementById(n);
  const date = ((g('icDate')||{}).value || '').trim() || todayISO();
  const amtRaw = (g('icAmt')||{}).value || '';
  const src = (g('icSrc')||{}).value || IC_SRC[0];
  const memo = (g('icMemo')||{}).value || '';
  const amount = Math.round(parseFloat(amtRaw) * 100) / 100;

  if (!(amount > 0)){ toast('积分必须填正数'); return; }
  if (!IC.form.shot || !IC.form.sha){
    toast('凭证截图必填 —— 没有凭证哈希，留痕就没有意义');
    return;
  }
  /* 先比对：比对结论要一起进链，事后不能「补一个一致上去」 */
  const cmp = icCompare(date, amount);
  const store = icLoad();
  const seq = store.trail.length + 1;
  const prev = seq > 1 ? store.trail[seq-2].hash : 'GENESIS';
  const rec = {
    seq: seq, at: tsStamp(), date: date, credits: amount,
    source: src, type: IC_TYPE[src] || 'grant', memo: memo,
    file: IC.form.shot, sha: IC.form.sha, bytes: IC.form.bytes || 0,
    by: (META && META.user) || '本机',
    cmp: {verdict: cmp.verdict, n: cmp.n, sum: cmp.sum, same: cmp.same, txn: cmp.txn, diff: cmp.diff},
    prev_hash: prev, hash: ''
  };
  const btn = g('icStamp');
  if (btn){ btn.disabled = true; btn.textContent = '计算哈希…'; }
  icHashOf(prev, rec).then(hash=>{
    rec.hash = hash;
    const trail = store.trail.concat([rec]);
    icPersist({trail: trail});
    /* 入链后清空金额 / 摘要 / 凭证：同一张截图连点两下会变成两笔收入，
       凭证跟着走反而更容易误录；日期与来源保留，连续录同类收入少点两次。 */
    IC.form = {date: date, amount: '', src: src, memo: '', shot: '', sha: '', bytes: 0};
    IC.cmp = cmp;
    buildIncome();
    toast(cmp.verdict === 'ok'
      ? '已记录留痕 · 第 '+seq+' 条，与账本流水一致'
      : '已记录留痕 · 第 '+seq+' 条，比对结论已一并写入链');
  }).catch(()=>{
    if (btn){ btn.disabled = false; btn.textContent = '记录留痕（比对后入链）'; }
    toast('当前环境不支持本地哈希计算 — 请在 https / localhost 下打开报告');
  });
}

/* 导出留痕包：带 schema / 头哈希 / 全量链，交 CLI 复核或归档 */
function icExportTrail(){
  const store = icLoad();
  if (!store.trail.length){ toast('还没有留痕记录'); return; }
  icVerifyChain(store.trail).then(res=>{
    const pack = {
      schema: 'wbc.income.manual-trail.v1',
      exportedAt: tsStamp(),
      by: (META && META.user) || '本机',
      count: store.trail.length,
      head: store.trail[store.trail.length-1].hash,
      chainOk: !!res.ok,
      chainBadAt: res.ok ? 0 : res.bad,
      note: '每条记录含 prev_hash / hash，可逐条复算；比对结论（cmp）记录的是录入当时账本的状态，'
          + '不随后续采集变化而改写。',
      records: store.trail
    };
    try {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([JSON.stringify(pack, null, 2)], {type:'application/json'}));
      a.download = 'income-trail-' + todayISO() + '.json';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(()=>{ try{ URL.revokeObjectURL(a.href); }catch(e){} }, 1200);
      toast('已导出留痕包（'+store.trail.length+' 条，链'+(res.ok?'完整':'第 '+res.bad+' 条断开')+'）');
    } catch(e){ toast('导出失败，请检查浏览器下载权限'); }
  });
}

function dsSave(){
  IC.src = {
    name:(document.getElementById('dsName')||{}).value || '',
    url:(document.getElementById('dsUrl')||{}).value || '',
    auth:(document.getElementById('dsAuth')||{}).value || 'Bearer',
    token:(document.getElementById('dsToken')||{}).value || '',
    map:(document.getElementById('dsMap')||{}).value || '{}'
  };
  try { JSON.parse(IC.src.map); }
  catch(e){ toast('字段映射不是合法 JSON'); return; }
  icPersist({src: IC.src});
  toast('数据源配置已存本机 · 采集层从 config/income-source.json 读取，需导出后放置');
}
function dsExport(){
  dsSave();
  const data = {name:IC.src.name, type:'api', url:IC.src.url, auth:IC.src.auth,
    map:JSON.parse(IC.src.map || '{}'), enabled:true,
    note:'令牌不导出。请手动填 token 后放入 config/income-source.json，由 adapters 读取。'};
  const blob = new Blob([JSON.stringify(data, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'income-source.json';
  document.body.appendChild(a); a.click();
  setTimeout(()=>{ URL.revokeObjectURL(a.href); a.remove(); }, 120);
  toast('已导出数据源配置（不含令牌）');
}

/* ============================================================
   三、回收调整
   ============================================================ */
/* 复用的处置流程（确认 / 止损 / 关闭 / 忽略 + 证据导出）在 p08_recycle.js 里，
   这里只换容器与页头说明，不复制一套处置逻辑。 */
function renderRecycle(){
  const el = document.getElementById('v-control-recycle');
  el.innerHTML = '<div class="ctl-note"><b>已消耗的积分不可追回。</b>'
    + '这一页做的是<b>账面回收调整</b>：把可归因的浪费标出来、确认归因、止损、留证，'
    + '为将来可能的厂商补偿留位置。<b>不会退回任何积分</b>，也不自动处置任务。</div>'
    + anomalyHTML(TASKS);
}
function buildRecycle(){ renderRecycle(); }
