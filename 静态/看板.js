/* ============================================================
   加权（仓权）合约监控面板 · 前端逻辑
   · SSE 实时推送，无固定刷新间隔，持续跟随行情
   · 阈值：周期 × 均线周期 动态均值 + 偏移量 + 锁定/解锁
   · 门槛 60%~90% 可调；解锁状态不预警
   · Ctrl+Z 隐身
   ============================================================ */
'use strict';

var S = null;                 // 最近一次服务端数据
var CARDS = {};               // code -> {el, code, side, ...}
var SSE_ERR = 0;
var LAST_JSON = '';

/* ---------------- 工具 ---------------- */
function $(s, r) { return (r || document).querySelector(s); }
function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }

function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}
function num(v, d) {
  var f = parseFloat(v);
  if (isNaN(f)) return d || 0;
  return f;
}
function fmtPrice(v, digits) {
  var f = parseFloat(v);
  if (isNaN(f)) return '--';
  var d = (digits === undefined || digits === null) ? 2 : Math.max(0, Math.min(6, digits));
  return f.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtPct(v) {
  var f = parseFloat(v);
  if (isNaN(f)) return '--';
  return (f >= 0 ? '+' : '') + f.toFixed(2) + '%';
}
function toast(msg, kind) {
  var box = $('#toasts');
  var t = document.createElement('div');
  t.className = 'toast ' + (kind || '');
  t.textContent = msg;
  box.appendChild(t);
  setTimeout(function () {
    t.style.transition = 'opacity .25s, transform .25s';
    t.style.opacity = '0';
    t.style.transform = 'translateY(10px)';
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 300);
  }, 2600);
}
function post(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  }).then(function (r) { return r.json(); });
}

/* ---------------- 主题 ---------------- */
function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  $('#btnTheme').textContent = t === 'dark' ? '☀️ 浅色' : '🌙 深色';
  try { localStorage.setItem('wq_theme', t); } catch (e) {}
  if (S) post('/api/config', { config: { panel: { theme: t } } });
}

/* ---------------- 隐身 ---------------- */
var GHOST = false;
function setGhost(on) {
  GHOST = !!on;
  $('#ghost').classList.toggle('on', GHOST);
  document.title = GHOST ? '关于规范商品数据整理与归档工作的通知'
                         : '加权（仓权）合约监控面板';
  if (GHOST) $('#gDate').textContent = new Date().toLocaleDateString('zh-CN');
}
document.addEventListener('keydown', function (e) {
  if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z')) {
    e.preventDefault();
    setGhost(!GHOST);
    if (!GHOST) toast('已退出隐身', 'ok');
  }
  if (e.key === 'Escape') closeSet();
});

/* ---------------- 设置抽屉 ---------------- */
function openSet() {
  $('#mask').classList.add('on');
  $('#drawer').classList.add('on');
  if (S) fillSettings();
}
function closeSet() { $('#mask').classList.remove('on'); $('#drawer').classList.remove('on'); }

/* ============================================================
   卡片渲染
   ============================================================ */
function kindOptions(sel) {
  return (S.thrKinds || []).map(function (k) {
    return '<option value="' + k + '"' + (k === sel ? ' selected' : '') + '>' +
      esc(S.kindCn[k] || k) + '</option>';
  }).join('');
}
function maOptions(sel) {
  return (S.maChoices || []).map(function (m) {
    return '<option value="' + m + '"' + (String(m) === String(sel) ? ' selected' : '') + '>' +
      'MA' + m + '</option>';
  }).join('');
}
function offOptions(sel) {
  var arr = [{ v: 0, t: '无偏移' }].concat((S.offsetChoices || []).map(function (o) {
    return { v: o, t: o + '%' };
  }));
  return arr.map(function (o) {
    return '<option value="' + o.v + '"' +
      (String(o.v) === String(sel) ? ' selected' : '') + '>' + o.t + '</option>';
  }).join('');
}

function makeCard(it, side) {
  var cls = side === 'above' ? 'a' : 'b';
  var el = document.createElement('div');
  el.className = 'card ' + cls;
  el.dataset.code = it.code;
  el.dataset.side = side;

  el.innerHTML =
    '<div class="card-top">' +
      '<span class="rank"></span>' +
      '<span class="nm"><span class="mn">' + esc(it.name) + '</span>' +
        '<span class="exch">' + esc(it.exch) + '</span></span>' +
      '<span class="px"><span class="v"></span><span class="c"></span></span>' +
    '</div>' +

    '<div class="card-mid">' +
      '<div class="bar-wrap"><div class="bar"><i></i><span class="thr" style="display:none"></span></div></div>' +
      '<span class="pct"></span>' +
    '</div>' +

    '<div class="card-sub">' +
      '<span>加权 <b class="q-price">--</b></span>' +
      '<span>历史高 <b>' + fmtPrice(it.H, it.digits) + '</b></span>' +
      '<span>历史低 <b>' + fmtPrice(it.L, it.digits) + '</b></span>' +
      '<span>均值 <b>' + fmtPrice(it.M, it.digits) + '</b></span>' +
      '<span>持仓 <b class="q-oi">--</b></span>' +
      '<span class="q-delay">延时 <b>--</b></span>' +
    '</div>' +

    '<div class="thr-open">' +
      '<span class="tag">' + (side === 'above' ? '下阈' : '上阈') + '</span>' +
      '<select class="sel k-kind"></select>' +
      '<select class="sel k-ma"></select>' +
      '<select class="sel k-off"></select>' +
      '<select class="sel k-dir">' +
        '<option value="down">下偏移</option><option value="up">上偏移</option>' +
      '</select>' +
      '<button class="btn sm k-fill">填充均线</button>' +
    '</div>' +

    '<div class="thr-open" style="border-top:none;padding-top:2px">' +
      '<span class="lbl">当前阈值</span>' +
      '<input class="thr-input ' + (side === 'above' ? 'down' : 'up') +
        ' k-val" title="可直接输入价格切为手工阈值；清空即清除">' +
      '<button class="btn sm k-lock">锁定</button>' +
      '<button class="btn sm ghost k-clear">清除</button>' +
      '<span class="tag k-state">未配置</span>' +
      '<span class="lbl k-label"></span>' +
    '</div>' +

    '<div class="card-foot">' +
      '<button class="btn-view">📊 查看加权行情</button>' +
      '<span class="lbl k-mkt"></span>' +
      '<span class="hint k-hint"></span>' +
    '</div>' +
    '<span class="trig-badge" style="display:none"></span>';

  // 填充下拉
  $('.k-kind', el).innerHTML = kindOptions(S.thrKind);
  $('.k-ma', el).innerHTML = maOptions(S.thrMA);
  $('.k-off', el).innerHTML = offOptions(S.thrOffset);
  $('.k-dir', el).value = S.thrOffsetDir || 'down';

  // 查看行情 → 新窗口
  $('.btn-view', el).addEventListener('click', function () {
    window.open('/行情?code=' + encodeURIComponent(it.code), '_blank');
  });

  // 填充均线
  $('.k-fill', el).addEventListener('click', function () {
    var pct = num((CUR[it.code] || {}).percent, 0);
    if (pct < num(S.minPercent, 70)) {
      toast('偏离度 ' + pct.toFixed(2) + '% 未达门槛 ' + num(S.minPercent, 70) + '%，暂不可填充', 'warn');
      return;
    }
    var target = side === 'above' ? 'down' : 'up';
    post('/api/threshold', {
      code: it.code, side: target, action: 'set',
      mode: 'ma',
      kind: $('.k-kind', el).value,
      ma: parseInt($('.k-ma', el).value, 10),
      offPct: num($('.k-off', el).value, 0),
      offDir: $('.k-dir', el).value
    }).then(function (r) {
      if (r.ok) toast(it.name + ' 已按均线填充阈值', 'ok');
      else toast((r.message || '设置失败'), 'bad');
    });
  });

  // 锁定 / 解锁
  $('.k-lock', el).addEventListener('click', function () {
    var t = CUR[it.code] && CUR[it.code][side === 'above' ? 'down' : 'up'];
    if (!t || !t.mode) { toast('请先填充均线或输入阈值', 'warn'); return; }
    var target = side === 'above' ? 'down' : 'up';
    post('/api/threshold', {
      code: it.code, side: target, action: 'lock', locked: !t.locked
    }).then(function (r) {
      if (r.ok) {
        toast(it.name + (t.locked ? ' 已解锁（不预警）' : ' 已锁定（参与预警）'),
              t.locked ? 'warn' : 'ok');
      }
    });
  });

  // 手工输入阈值（切到手工模式；清空即清除）
  $('.k-val', el).addEventListener('change', function () {
    var v = String(this.value).replace(/[,，\s]/g, '');
    var target = side === 'above' ? 'down' : 'up';
    if (v === '') {
      post('/api/threshold', { code: it.code, side: target, action: 'clear' });
      return;
    }
    var f = parseFloat(v);
    if (isNaN(f)) { toast('请输入有效数字', 'warn'); return; }
    post('/api/threshold', {
      code: it.code, side: target, action: 'set',
      mode: 'manual', manual: f, offPct: 0
    }).then(function (r) {
      if (r.ok) toast(it.name + ' 已改为手工阈值 ' + f, 'ok');
    });
  });

  // 清除
  $('.k-clear', el).addEventListener('click', function () {
    var target = side === 'above' ? 'down' : 'up';
    post('/api/threshold', { code: it.code, side: target, action: 'clear' })
      .then(function (r) { if (r.ok) toast(it.name + ' 阈值已清除'); });
  });

  return el;
}

/* ---------------- 更新单卡 ---------------- */
var CUR = {};                 // code -> item
function patchCard(c, it) {
  var el = c.el;
  var side = c.side;
  var tSide = side === 'above' ? 'down' : 'up';
  var t = it[tSide] || {};
  var armed = !!t.armed;
  var pct = num(it.percent, 0);
  var hot = pct >= num(S.minPercent, 70);

  el.className = 'card ' + (side === 'above' ? 'a' : 'b') +
    (hot ? ' hot' : '') + (armed ? ' armed' : '') +
    (c.trig === 'up' ? ' trig-up' : (c.trig === 'down' ? ' trig-down' : ''));

  el.dataset.code = it.code;
  $('.rank', el).textContent = it.rank;
  $('.nm .mn', el).textContent = it.name;
  $('.nm .exch', el).textContent = it.exch;

  var chg = num(it.change, 0);
  var cEl = $('.px .c', el);
  cEl.className = 'c ' + (chg > 0 ? 'c-up' : (chg < 0 ? 'c-down' : 'c-flat'));
  cEl.textContent = fmtPct(it.pct);
  $('.px .v', el).textContent = fmtPrice(it.price, it.digits);
  $('.px .v', el).className = 'v ' + (chg > 0 ? 'c-up' : (chg < 0 ? 'c-down' : 'c-flat'));

  $('.pct', el).textContent = pct.toFixed(2) + '%';
  $('.pct', el).className = 'pct' + (hot ? ' hot' : '');
  var fill = Math.max(0, Math.min(100, pct));
  $('.bar i', el).style.width = fill + '%';

  // 阈值刻度
  var thr = $('.bar .thr', el);
  if (t.value) {
    var tv = num(t.value, 0);
    var M = num(it.M, 0), H = num(it.H, 0), L = num(it.L, 0);
    var tp;
    if (side === 'above') tp = H - M > 0 ? (tv - M) / (H - M) * 100 : 0;
    else tp = L - M < 0 ? (tv - M) / (L - M) * 100 : 0;
    thr.style.display = '';
    thr.style.left = Math.max(0, Math.min(100, tp)) + '%';
  } else {
    thr.style.display = 'none';
  }

  $('.q-price', el).textContent = fmtPrice(it.price, it.digits);
  $('.q-oi', el).textContent = (+it.oi || 0).toLocaleString('zh-CN');
  $('.q-delay', el).innerHTML = '延时 <b>' + esc(it.delayLabel) + '</b>';

  // 阈值控件
  var valIn = $('.k-val', el);
  if (document.activeElement !== valIn) {
    valIn.value = t.value === null || t.value === undefined ? '' : fmtPrice(t.value, it.digits);
  }
  var st = $('.k-state', el);
  if (!t.mode) {
    st.className = 'tag k-state'; st.textContent = '未配置';
  } else if (!t.locked) {
    st.className = 'tag k-state muted'; st.textContent = '已解锁 · 不预警';
  } else {
    st.className = 'tag k-state armed'; st.textContent = '已锁定 · 预警中';
  }
  $('.k-lock', el).textContent = t.locked ? '解锁' : '锁定';
  $('.k-lock', el).className = 'btn sm k-lock' + (t.locked ? '' : ' primary');
  $('.k-label', el).textContent = t.mode
    ? (t.label + (t.offsetLabel && t.offsetLabel !== '手工' && t.offsetLabel !== '-'
        ? ' · ' + t.offsetLabel : ''))
    : '';

  // 下拉回显
  if (document.activeElement !== $('.k-kind', el)) {
    if (t.kind) $('.k-kind', el).value = t.kind;
  }
  if (document.activeElement !== $('.k-ma', el)) {
    if (t.ma) $('.k-ma', el).value = String(t.ma);
  }
  if (document.activeElement !== $('.k-off', el)) {
    $('.k-off', el).value = String(t.offPct === null || t.offPct === undefined ? 0 : t.offPct);
  }
  if (document.activeElement !== $('.k-dir', el)) {
    if (t.offDir) $('.k-dir', el).value = t.offDir;
  }

  $('.k-fill', el).disabled = !hot;
  $('.k-fill', el).title = hot ? '按所选周期与均线填充动态阈值'
                               : ('偏离度需 ≥ ' + num(S.minPercent, 70) + '%');

  $('.k-mkt', el).textContent = it.mktOpen ? '交易中' : ('休市 · ' + (it.mktNote || ''));
  $('.k-hint', el).textContent = '行情时间 ' + (it.quoteTime || '--');

  var badge = $('.trig-badge', el);
  if (c.trig === 'up' || c.trig === 'down') {
    badge.style.display = '';
    badge.className = 'trig-badge ' + c.trig;
    badge.textContent = (c.trig === 'up' ? '上穿上阈' : '下穿下阈') +
      (c.mktOpen === false ? ' · 休市静默' : '');
  } else if (c.pending) {
    badge.style.display = '';
    badge.className = 'trig-badge mute';
    badge.textContent = '阈值外 · 休市静默';
  } else {
    badge.style.display = 'none';
  }
}

/* ---------------- 重排 / 全量渲染 ---------------- */
function render() {
  var colA = $('#listAbove'), colB = $('#listBelow');
  var a = S.above || [], b = S.below || [];
  CUR = {};
  S.above.concat(S.below).forEach(function (x) { CUR[x.code] = x; });

  // 触发态索引
  var trig = {};
  (S.alerts || []).forEach(function (al) {
    trig[al.code + ':' + al.state] = al;
  });

  var seenA = {}, seenB = {};
  a.forEach(function (it) {
    seenA[it.code] = 1;
    var c = CARDS[it.code];
    if (!c || c.side !== 'above') {
      if (c && c.el.parentNode) c.el.parentNode.removeChild(c.el);
      var el = makeCard(it, 'above');
      CARDS[it.code] = { el: el, side: 'above', code: it.code };
      colA.appendChild(el);
    }
    var cc = CARDS[it.code];
    var tg = trig[it.code + ':down'];
    cc.trig = tg ? 'down' : null;
    cc.pending = !tg && !!((it.down || {}).armed) && (it.price <= num((it.down || {}).value, -1e9));
    cc.mktOpen = it.mktOpen;
    patchCard(cc, it);
  });
  b.forEach(function (it) {
    seenB[it.code] = 1;
    var c = CARDS[it.code];
    if (!c || c.side !== 'below') {
      if (c && c.el.parentNode) c.el.parentNode.removeChild(c.el);
      var el = makeCard(it, 'below');
      CARDS[it.code] = { el: el, side: 'below', code: it.code };
      colB.appendChild(el);
    }
    var cc = CARDS[it.code];
    var tg = trig[it.code + ':up'];
    cc.trig = tg ? 'up' : null;
    cc.pending = !tg && !!((it.up || {}).armed) && (it.price >= num((it.up || {}).value, 1e9));
    cc.mktOpen = it.mktOpen;
    patchCard(cc, it);
  });

  Object.keys(CARDS).forEach(function (code) {
    if (!seenA[code] && !seenB[code]) {
      var c = CARDS[code];
      if (c.el.parentNode) c.el.parentNode.removeChild(c.el);
      delete CARDS[code];
    }
  });

  // 顺序
  reorder(colA, a.map(function (x) { return x.code; }));
  reorder(colB, b.map(function (x) { return x.code; }));
  adjustEmpty(colA, a.length, '高于平均值');
  adjustEmpty(colB, b.length, '低于平均值');

  // 统计
  var armed = 0;
  S.above.concat(S.below).forEach(function (it) {
    if ((it.up || {}).armed || (it.down || {}).armed) armed++;
  });
  $('#sTotal').innerHTML = S.total + '<small>个</small>';
  $('#sAbove').innerHTML = (S.stats.above) + '<small>个</small>';
  $('#sBelow').innerHTML = (S.stats.below) + '<small>个</small>';
  $('#sArmed').innerHTML = armed + '<small>个</small>';
  $('#sTrig').innerHTML = (S.alerts || []).length + '<small id="sMin">' +
    num(S.minPercent, 70) + '%</small>';
  $('#sDelay').textContent = S.delayLabel;
  $('#hAbove').textContent = S.stats.above + ' 个';
  $('#hBelow').textContent = S.stats.below + ' 个';
  $('#fUpd').textContent = S.updated;
  $('#fSrc').textContent = S.sourceLabel;
}

function reorder(col, codes) {
  var i = 0;
  codes.forEach(function (code) {
    var c = CARDS[code];
    if (!c) return;
    if (col.children[i] !== c.el) col.insertBefore(c.el, col.children[i] || null);
    i++;
  });
}
function adjustEmpty(col, n, label) {
  var e = col.querySelector('.empty');
  if (n === 0) {
    if (!e) {
      e = document.createElement('div');
      e.className = 'empty';
      e.innerHTML = '<div class="big">⚖</div><div>暂无「' + label + '」品种</div>';
      col.appendChild(e);
    }
  } else if (e) {
    e.parentNode.removeChild(e);
  }
}

/* ---------------- 顶栏 ---------------- */
function renderTop() {
  var conn = S.conn || {};
  var chip = $('#chipConn');
  if (conn.ok) {
    chip.className = 'chip ok';
    chip.innerHTML = '<span class="dot pulse"></span><span>已连接 · ' +
      esc(conn.user || '') + '</span>';
  } else {
    chip.className = 'chip bad';
    chip.innerHTML = '<span class="dot"></span><span>' +
      esc(conn.err || S.error || '未连接') + '</span>';
  }
  $('#chipDelay').innerHTML = '延时 <b>' + esc(S.delayLabel) + '</b>';
  $('#chipUpd').innerHTML = '更新 <b>' + esc(S.updated.split(' ')[1] || '--') + '</b>';
  var g = S.guard || {};
  var gc = $('#chipGuard');
  var cls = 'chip';
  if (g.on) cls += (g.sentDay > 0 ? ' warn' : ' ok');
  gc.className = cls;
  gc.innerHTML = '邮件 <b>' + (g.on ? '开' : '关') + '</b> · 今日 <b>' +
    (g.sentDay || 0) + '</b>' + (g.tradingOnly ? ' · 仅交易时段' : '');
  gc.title = '触发中 ' + (g.triggering || 0) + ' · 冷却抑制 ' +
    ((g.suppressed || {}).cooldown || 0) + ' · 配额抑制 ' + ((g.suppressed || {}).quota || 0) +
    ' · 闭市静默 ' + ((g.suppressed || {}).closed || 0);
}

/* ---------------- 设置面板回填 ---------------- */
function fillSettings() {
  var c = S.config || {};
  var p = c.panel || {}, m = c.mail || {}, t = c.tianqin || {};
  $('#cfgTqUser').value = t.user || '';
  $('#cfgTqPass').value = t.password || '';
  $('#cfgMailOn').checked = !!p.mailOn;
  $('#cfgTradingOnly').checked = p.mailTradingOnly !== false;
  $('#cfgHost').value = m.host || '';
  $('#cfgPort').value = m.port || 465;
  $('#cfgSsl').checked = m.ssl !== false;
  $('#cfgMailUser').value = m.user || '';
  $('#cfgAuth').value = m.authcode || '';
  $('#cfgFromName').value = m.from_name || '';
  $('#cfgTo').value = (m.to || []).join(', ');
  $('#cfgCc').value = (m.cc || []).join(', ');
  $('#cfgMin').value = num(p.minPercent, 70);
  $('#vMin').textContent = num(p.minPercent, 70) + '%';
  $('#cfgThrKind').innerHTML = kindOptions(p.thrKind);
  $('#cfgThrMA').innerHTML = maOptions(p.thrMA);
  $('#cfgThrOff').innerHTML = offOptions(p.thrOffset);
  $('#cfgThrDir').value = p.thrOffsetDir || 'down';
  $('#cfgCooldown').value = p.mailCooldown;
  $('#cfgPerHour').value = p.mailMaxPerHour;
  $('#cfgPerDay').value = p.mailMaxPerDay;
  var g = S.guard || {}, sp = g.suppressed || {};
  $('#guardLog').textContent =
    '邮件开关：' + (g.on ? '开启' : '关闭') + '\n' +
    '非交易时段静默：' + (g.tradingOnly ? '是' : '否') + '\n' +
    '判定方式：' + (g.candle === 'm1' ? '1 分钟K线穿越' : esc(g.candle)) + '\n' +
    '同品种冷却：' + g.cooldown + ' 秒\n' +
    '配额：' + g.sentHour + ' / ' + g.maxPerHour + ' 封（本小时）　' +
    g.sentDay + ' / ' + g.maxPerDay + ' 封（今日）\n' +
    '当前触发中：' + (g.triggering || 0) + ' 个品种\n' +
    '闭市静默抑制：' + (sp.closed || 0) + ' 次　冷却抑制：' + (sp.cooldown || 0) + ' 次\n' +
    '配额抑制：' + (sp.quota || 0) + ' 次　未锁定跳过：' + (sp.unlocked || 0) + ' 次';
  $('#mailLog').textContent = (S.mailLog || []).slice().reverse().join('\n') || '（暂无）';
  $('#noteLog').textContent = (S.notes || []).slice().reverse().join('\n') || '（暂无）';
  var st = (S.conn || {});
  $('#tqState').textContent = st.ok
    ? ('已连接 ' + (st.user || '') + ' · 连接于 ' + (st.connectedAt || '') +
       ' · 订阅 ' + (st.subs || 0))
    : (st.err || S.error || '未连接');
}

function cfgPayload() {
  return {
    panel: {
      minPercent: num($('#cfgMin').value, 70),
      thrKind: $('#cfgThrKind').value,
      thrMA: parseInt($('#cfgThrMA').value, 10),
      thrOffset: num($('#cfgThrOff').value, 0),
      thrOffsetDir: $('#cfgThrDir').value,
      mailOn: $('#cfgMailOn').checked,
      mailTradingOnly: $('#cfgTradingOnly').checked,
      mailCooldown: parseInt($('#cfgCooldown').value, 10) || 300,
      mailMaxPerHour: parseInt($('#cfgPerHour').value, 10) || 12,
      mailMaxPerDay: parseInt($('#cfgPerDay').value, 10) || 60
    }
  };
}
function mailPayload() {
  function arr(v) {
    return String(v || '').split(/[,，;；\s]+/).filter(function (s) { return s; });
  }
  return {
    mail: {
      on: $('#cfgMailOn').checked,
      host: $('#cfgHost').value.trim(),
      port: parseInt($('#cfgPort').value, 10) || 465,
      ssl: $('#cfgSsl').checked,
      user: $('#cfgMailUser').value.trim(),
      authcode: $('#cfgAuth').value.trim(),
      from_name: $('#cfgFromName').value.trim() || '加权合约监控',
      to: arr($('#cfgTo').value),
      cc: arr($('#cfgCc').value)
    },
    panel: {
      mailOn: $('#cfgMailOn').checked,
      mailTradingOnly: $('#cfgTradingOnly').checked
    }
  };
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  $('#btnTheme').addEventListener('click', function () {
    var cur = document.documentElement.getAttribute('data-theme');
    applyTheme(cur === 'dark' ? 'light' : 'dark');
  });
  $('#btnGhost').addEventListener('click', function () { setGhost(!GHOST); });
  $('#btnSet').addEventListener('click', function () { openSet(); });
  $('#btnCloseSet').addEventListener('click', closeSet);
  $('#mask').addEventListener('click', closeSet);

  $('#cfgMin').addEventListener('input', function () {
    $('#vMin').textContent = $('#cfgMin').value + '%';
  });

  $('#btnSaveTq').addEventListener('click', function () {
    var u = $('#cfgTqUser').value.trim(), p = $('#cfgTqPass').value;
    if (!u || !p) { toast('账号和密码都不能为空', 'warn'); return; }
    post('/api/config', { config: { tianqin: { user: u, password: p } } })
      .then(function (r) {
        toast(r.ok ? (r.reconnected ? '已用新账号重连天勤' : '账号已保存') : '保存失败',
              r.ok ? 'ok' : 'bad');
      });
  });

  $('#btnSaveMail').addEventListener('click', function () {
    post('/api/config', { config: mailPayload() }).then(function (r) {
      toast(r.ok ? '邮件配置已保存' : '保存失败', r.ok ? 'ok' : 'bad');
    });
  });

  $('#btnTestMail').addEventListener('click', function () {
    var btn = $('#btnTestMail');
    btn.disabled = true; btn.textContent = '发送中…';
    post('/api/mail/test', { config: mailPayload().mail }).then(function (r) {
      btn.disabled = false; btn.textContent = '发送测试邮件';
      var m = (r.result || {}).message || '';
      toast(r.ok ? ('测试邮件已发出：' + m) : ('发送失败：' + m), r.ok ? 'ok' : 'bad');
    }).catch(function () {
      btn.disabled = false; btn.textContent = '发送测试邮件';
      toast('发送失败', 'bad');
    });
  });

  $('#btnSavePanel').addEventListener('click', function () {
    post('/api/config', { config: cfgPayload() }).then(function (r) {
      toast(r.ok ? '面板参数已保存' : '保存失败', r.ok ? 'ok' : 'bad');
    });
  });
}

/* ---------------- SSE ---------------- */
function onData(d) {
  S = d;
  window.S = S;
  renderTop();
  render();
  if ($('#drawer').classList.contains('on')) fillSettings();
}
function connect() {
  var es = new EventSource('/api/stream');
  es.onmessage = function (ev) {
    try {
      var msg = JSON.parse(ev.data);
      if (msg.type === 'data') onData(msg.data);
    } catch (e) {}
    SSE_ERR = 0;
  };
  es.onerror = function () {
    SSE_ERR++;
    $('#chipConn').className = 'chip warn';
    $('#chipConn').innerHTML = '<span class="dot"></span><span>连接中断，重连中…</span>';
    if (SSE_ERR > 6) { es.close(); setTimeout(connect, 3000); }
  };
}

/* ---------------- 启动 ---------------- */
window.addEventListener('DOMContentLoaded', function () {
  var t = document.documentElement.getAttribute('data-theme') || 'light';
  applyTheme(t);
  bind();
  setGhost(false);
  fetch('/api/data').then(function (r) { return r.json(); }).then(function (r) {
    if (r.ok) onData(r.data);
  });
  connect();
});
