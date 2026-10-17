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

/* ---------------- 阈值基准合约下拉（加权 + 全部真实月份合约） ----------------
   86 个品种全部预取会很重，这里用后台小并发队列渐进加载：先到先 excerpts，
   用户点开任意卡片的「标的」下拉时基本已就绪；未就绪则立即插队。
   ---------------------------------------------------------------------------- */
var CT_CACHE = {};      // code -> api 结果（含 null 失败），仅「已加载」才有键
var CT_QUEUED = {};     // code -> 1，仅表示已排队避免重复
var CT_QUEUE = [];      // 待加载的 code
var CT_TIMER = null;
var CT_BUSY = 0;

function ctHas(code) {
  return Object.prototype.hasOwnProperty.call(CT_CACHE, code);
}

function loadCt(code, cb) {
  if (ctHas(code)) {
    cb(CT_CACHE[code]);
    return;
  }
  fetch('/api/contracts?code=' + encodeURIComponent(code))
    .then(function (r) { return r.json(); })
    .then(function (r) {
      CT_CACHE[code] = (r && r.ok) ? r : null;
      cb(CT_CACHE[code]);
    })
    .catch(function () { CT_CACHE[code] = null; cb(null); });
}

function ctQueue(code) {
  if (ctHas(code) || CT_QUEUED[code]) return;
  CT_QUEUED[code] = 1;
  CT_QUEUE.push(code);
  if (!CT_TIMER) CT_TIMER = setInterval(ctPump, 260);
}

function ctPump() {
  while (CT_BUSY < 3 && CT_QUEUE.length) {
    (function (code) {              // IIFE 捕获：var 无块级作用域，直接闭包会全指向最后一个
      CT_BUSY++;
      loadCt(code, function () {
        CT_BUSY--;
        ctApply(code);
      });
    })(CT_QUEUE.shift());
  }
  if (!CT_QUEUE.length && CT_BUSY === 0 && CT_TIMER) {
    clearInterval(CT_TIMER);
    CT_TIMER = null;
  }
}

/* 把已加载的合约列表写进该品种所有卡片的「标的」下拉 */
function ctApply(code) {
  var r = CT_CACHE[code];
  var sels = document.querySelectorAll('.k-sym');
  Array.prototype.forEach.call(sels, function (sel) {
    if (sel.dataset.code !== code) return;
    var dig = parseInt(sel.dataset.digits || '2', 10);
    ctFill(sel, r, dig);
  });
}

function ctFill(sel, r, digits) {
  var keep = sel.value;
  if (!r || !r.items || !r.items.length) {
    sel.innerHTML = '<option value="">标的·加权（仓权）</option>' +
      '<option value="" disabled>（无真实合约）</option>';
    sel.dataset.ready = '1';
    return;
  }
  var html = '<option value="">标的·加权（仓权）</option>';
  r.items.forEach(function (it) {
    var px = fmtPrice(it.last, digits);
    var pc = (it.pct === null || it.pct === undefined) ? '' :
      (' ' + (num(it.pct, 0) >= 0 ? '↑' : '↓') + Math.abs(num(it.pct, 2)).toFixed(2) + '%');
    html += '<option value="' + esc(it.sym) + '">' +
      esc((it.name || it.month || it.sym) + '　' + px + pc) +
      (it.isMain ? '（主力）' : '') + '</option>';
  });
  sel.innerHTML = html;
  if (keep) {
    sel.value = keep;
    if (sel.value !== keep) sel.value = '';
  }
  // 配置项里指定了合约，但列表刚加载完 → 这时才能回填选中
  if (sel.dataset.want && document.activeElement !== sel) {
    sel.value = sel.dataset.want;
    if (sel.value !== sel.dataset.want) sel.value = '';
    delete sel.dataset.want;
  }
  sel.dataset.ready = '1';
  sel.title = (r && r.items && r.items.length)
    ? ('阈值基准标的：已加载 ' + r.items.length + ' 个真实合约（含价格与涨跌）')
    : '阈值基准标的：加权（仓权）';
}

/* 绑定某张卡片的「标的」下拉 */
function bindSymSelect(sel, code, name, digits) {
  sel.dataset.code = code;
  sel.dataset.digits = digits;
  if (ctHas(code)) ctFill(sel, CT_CACHE[code], digits);
  else ctQueue(code);
  function ensure() {
    if (sel.dataset.ready === '1') return;
    loadCt(code, function (r) { ctFill(sel, r, digits); });
  }
  sel.addEventListener('mouseover', ensure);
  sel.addEventListener('mousedown', ensure);
  sel.addEventListener('focus', ensure);
  sel.addEventListener('change', function () {
    var opt = sel.options[sel.selectedIndex];
    sel.title = opt ? opt.textContent.trim() : '';
  });
}

/* ---------------- 卡片 ---------------- */
function makeCard(it, side) {
  var cls = side === 'above' ? 'a' : 'b';
  var el = document.createElement('div');
  el.className = 'card ' + cls;
  el.dataset.code = it.code;
  el.dataset.side = side;
  // 当前正在编辑的阈值方向：高栏默认「下阈」，低栏默认「上阈」，但两侧都可改
  el.dataset.tside = side === 'above' ? 'down' : 'up';

  el.innerHTML =
    '<div class="card-top">' +
      '<span class="rank"></span>' +
      '<span class="nm"><span class="mn">' + esc(it.name) + '</span>' +
        '<span class="exch">' + esc(it.exch) + '</span></span>' +
      '<span class="px"><span class="v"></span><span class="c"></span></span>' +
    '</div>' +

    '<div class="card-mid">' +
      '<div class="bar-wrap"><div class="bar"><i></i>' +
        '<span class="thr thr-down" style="display:none"></span>' +
        '<span class="thr thr-up" style="display:none"></span>' +
      '</div></div>' +
      '<span class="pct"></span>' +
    '</div>' +

    '<div class="card-sub">' +
      '<span>加权 <b class="q-price">--</b></span>' +
      '<span>历史高 <b class="q-h">' + fmtPrice(it.H, it.digits) + '</b></span>' +
      '<span>历史低 <b class="q-l">' + fmtPrice(it.L, it.digits) + '</b></span>' +
      '<span>均值 <b class="q-m">' + fmtPrice(it.M, it.digits) + '</b></span>' +
      '<span>持仓 <b class="q-oi">--</b></span>' +
      '<span class="q-delay">延时 <b>--</b></span>' +
    '</div>' +

    '<div class="thr-open">' +
      '<span class="tseg" title="选择要配置「下阈」还是「上阈」，两者可同时存在">' +
        '<button class="tsb" data-s="down" type="button">下阈</button>' +
        '<button class="tsb" data-s="up" type="button">上阈</button>' +
      '</span>' +
      '<select class="sel k-sym" title="阈值基准标的：默认加权（仓权）；也可选任一真实月份合约，取其指定周期的均线">' +
        '<option value="">标的·加权（仓权）</option>' +
      '</select>' +
      '<select class="sel k-kind"></select>' +
      '<select class="sel k-ma"></select>' +
      '<select class="sel k-off"></select>' +
      '<select class="sel k-dir">' +
        '<option value="down">下偏移</option><option value="up">上偏移</option>' +
      '</select>' +
      '<button class="btn sm primary k-fill">填充均线</button>' +
    '</div>' +

    '<div class="thr-open" style="border-top:none;padding-top:2px">' +
      '<span class="lbl">当前<b class="k-tname">下阈</b></span>' +
      '<input class="thr-input k-val" title="可直接输入价格切为手工阈值；清空即清除">' +
      '<button class="btn sm k-lock">锁定</button>' +
      '<button class="btn sm ghost k-clear">清除</button>' +
      '<span class="tag k-state">未配置</span>' +
      '<span class="lbl k-label"></span>' +
    '</div>' +

    '<div class="thr-both">' +
      '<span class="bside" data-s="down">下阈<b class="b-val">未配置</b></span>' +
      '<span class="bside" data-s="up">上阈<b class="b-val">未配置</b></span>' +
      '<span class="lbl b-tip">点上方「下阈 / 上阈」切换要配置的方向；两个阈值可同时锁定</span>' +
    '</div>' +

    '<div class="card-foot">' +
      '<button class="btn-view">📊 查看行情</button>' +
      '<span class="lbl k-mkt"></span>' +
      '<span class="hint k-hint"></span>' +
    '</div>' +
    '<span class="trig-badge" style="display:none"></span>';

  // 填充下拉
  $('.k-kind', el).innerHTML = kindOptions(S.thrKind);
  $('.k-ma', el).innerHTML = maOptions(S.thrMA);
  $('.k-off', el).innerHTML = offOptions(S.thrOffset);
  $('.k-dir', el).value = S.thrOffsetDir || 'down';
  bindSymSelect($('.k-sym', el), it.code, it.name, it.digits);

  function curSide() { return el.dataset.tside; }

  // 阈别切换（下阈 / 上阈）
  $$('.tsb', el).forEach(function (b) {
    b.addEventListener('click', function () {
      if (el.dataset.tside === b.dataset.s) return;
      el.dataset.tside = b.dataset.s;
      var cc = CARDS[it.code];
      if (cc && CUR[it.code]) patchCard(cc, CUR[it.code]);
    });
  });

  // 查看行情 → 新窗口
  $('.btn-view', el).addEventListener('click', function () {
    window.open('/行情?code=' + encodeURIComponent(it.code), '_blank');
  });

  // 填充均线（作用于当前选中的阈别）
  $('.k-fill', el).addEventListener('click', function () {
    var pct = num((CUR[it.code] || {}).percent, 0);
    if (pct < num(S.minPercent, 70)) {
      toast('偏离度 ' + pct.toFixed(2) + '% 未达门槛 ' + num(S.minPercent, 70) + '%，暂不可填充', 'warn');
      return;
    }
    var target = curSide();
    var selSym = $('.k-sym', el);
    var symV = selSym ? (selSym.value || '') : '';
    post('/api/threshold', {
      code: it.code, side: target, action: 'set',
      mode: 'ma',
      sym: symV,
      kind: $('.k-kind', el).value,
      ma: parseInt($('.k-ma', el).value, 10),
      offPct: num($('.k-off', el).value, 0),
      offDir: $('.k-dir', el).value
    }).then(function (r) {
      if (r.ok) {
        var src = symV && selSym && selSym.options[selSym.selectedIndex]
          ? selSym.options[selSym.selectedIndex].textContent.trim()
          : '加权（仓权）';
        toast(it.name + ' 已按「' + src + '」均线填充' +
          (target === 'down' ? '下阈' : '上阈'), 'ok');
      } else {
        toast((r.message || '设置失败'), 'bad');
      }
    });
  });

  // 锁定 / 解锁（作用于当前选中的阈别）
  $('.k-lock', el).addEventListener('click', function () {
    var target = curSide();
    var t = (CUR[it.code] || {})[target];
    if (!t || !t.mode) { toast('请先填充均线或输入阈值', 'warn'); return; }
    post('/api/threshold', {
      code: it.code, side: target, action: 'lock', locked: !t.locked
    }).then(function (r) {
      if (r.ok) {
        toast(it.name + (target === 'down' ? ' 下阈' : ' 上阈') +
              (t.locked ? ' 已解锁（不预警）' : ' 已锁定（参与预警）'),
              t.locked ? 'warn' : 'ok');
      }
    });
  });

  // 手工输入阈值（切到手工模式；清空即清除）
  $('.k-val', el).addEventListener('change', function () {
    var target = curSide();
    var v = String(this.value).replace(/[,，\s]/g, '');
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
      if (r.ok) {
        toast(it.name + (target === 'down' ? ' 下阈' : ' 上阈') + ' 已改为手工值 ' + f, 'ok');
      }
    });
  });

  // 清除
  $('.k-clear', el).addEventListener('click', function () {
    var target = curSide();
    post('/api/threshold', { code: it.code, side: target, action: 'clear' })
      .then(function (r) {
        if (r.ok) {
          toast(it.name + (target === 'down' ? ' 下阈' : ' 上阈') + ' 已清除');
        }
      });
  });

  return el;
}

/* ---------------- 更新单卡 ---------------- */
var CUR = {};                 // code -> item
function patchCard(c, it) {
  var el = c.el;
  var side = c.side;
  var tSide = el.dataset.tside || (side === 'above' ? 'down' : 'up');
  if (tSide !== 'up' && tSide !== 'down') { tSide = 'down'; el.dataset.tside = 'down'; }
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

  // 两条阈值刻度：下阈（绿）/ 上阈（红），都画出来
  [['down', it.down], ['up', it.up]].forEach(function (pair) {
    var el2 = $('.thr-' + pair[0], el);
    var v = pair[1] && pair[1].value;
    if (v) {
      var tv = num(v, 0);
      var M = num(it.M, 0), H = num(it.H, 0), L = num(it.L, 0);
      var tp;
      if (pair[0] === 'down') tp = (H - M) > 0 ? (tv - M) / (H - M) * 100 : 0;
      else tp = (M - L) > 0 ? (tv - M) / (M - L) * 100 : 0;
      el2.style.display = '';
      el2.style.left = Math.max(0, Math.min(100, tp)) + '%';
    } else {
      el2.style.display = 'none';
    }
  });

  $('.q-price', el).textContent = fmtPrice(it.price, it.digits);
  $('.q-h', el).textContent = fmtPrice(it.H, it.digits);
  $('.q-l', el).textContent = fmtPrice(it.L, it.digits);
  $('.q-m', el).textContent = fmtPrice(it.M, it.digits);
  $('.q-oi', el).textContent = (+it.oi || 0).toLocaleString('zh-CN');
  $('.q-delay', el).innerHTML = '延时 <b>' + esc(it.delayLabel) + '</b>';

  // 阈别切换按钮高亮
  $$('.tsb', el).forEach(function (b) {
    var isOn = b.dataset.s === tSide;
    b.className = 'tsb' + (isOn ? ' on' : '') +
      (b.dataset.s === 'down' ? ' sd' : ' su');
    var st2 = it[b.dataset.s] || {};
    b.title = (b.dataset.s === 'down' ? '下阈：价格跌破它预警' : '上阈：价格上穿它预警') +
      (st2.mode ? (st2.locked ? '（已锁定）' : '（已解锁）') : '（未配置）');
  });
  $('.k-tname', el).textContent = tSide === 'down' ? '下阈' : '上阈';

  // 阈值控件
  var valIn = $('.k-val', el);
  if (document.activeElement !== valIn) {
    valIn.value = t.value === null || t.value === undefined ? '' : fmtPrice(t.value, it.digits);
  }
  valIn.className = 'thr-input k-val ' + (tSide === 'down' ? 'down' : 'up');
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

  // 两侧概览
  $$('.bside', el).forEach(function (b) {
    var s2 = it[b.dataset.s] || {};
    var bv = $('.b-val', b);
    var txt, cls = 'bside ' + (b.dataset.s === 'down' ? 'sd' : 'su') +
      (b.dataset.s === tSide ? ' cur' : '');
    if (!s2.mode) { txt = '未配置'; cls += ' none'; }
    else {
      txt = fmtPrice(s2.value, it.digits) + (s2.locked ? ' ●已锁定' : ' ○已解锁');
      cls += s2.locked ? ' armed' : ' idle';
    }
    b.className = cls;
    bv.textContent = txt;
  });

  // 下拉回显
  if (document.activeElement !== $('.k-kind', el)) {
    if (t.kind) $('.k-kind', el).value = t.kind;
  }
  // 「标的」下拉：合约列表可能还没加载完 → 记住目标值，加载完后回填
  (function () {
    var sel = $('.k-sym', el);
    if (!sel) return;
    if (document.activeElement !== sel) {
      if (sel.dataset.ready === '1') {
        sel.value = t.sym || '';
        if (sel.value !== (t.sym || '')) sel.value = '';
      } else {
        sel.dataset.want = t.sym || '';
      }
    }
  })();
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
  $('.k-fill', el).title = hot ? '按所选周期与均线填充' + (tSide === 'down' ? '下阈' : '上阈')
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
/* 已锁定阈值且价格在阈值外（但还没有发生穿越事件）→ 显示「阈值外 · 休市静默」 */
function pendOut(it) {
  var d = it.down || {}, u = it.up || {};
  return (!!d.armed && it.price <= num(d.value, -1e9)) ||
         (!!u.armed && it.price >= num(u.value, 1e9));
}

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
    var tgd = trig[it.code + ':down'], tgu = trig[it.code + ':up'];
    cc.trig = tgd ? 'down' : (tgu ? 'up' : null);
    cc.pending = !cc.trig && pendOut(it);
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
    var tgd = trig[it.code + ':down'], tgu = trig[it.code + ':up'];
    cc.trig = tgd ? 'down' : (tgu ? 'up' : null);
    cc.pending = !cc.trig && pendOut(it);
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
  updateAlert(conn);
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

/* ---------------- 品种明细弹窗（已锁定阈值 / 触发中） ---------------- */
var PICKS_KIND = '';            // 'armed' | 'trig'（用于解锁后刷新弹窗内容）
var PICKS_BUSY = {};            // code|side -> true，防重复点击

function openPicks(title, rows, kind) {
  var box = $('#pickBox'), mask = $('#pickMask'), body = $('#pickBody');
  if (kind) PICKS_KIND = kind;
  $('#pickTitle').textContent = title;
  var foot = $('#pickFoot');
  if (foot) foot.style.display = rows.length ? '' : 'none';
  if (!rows.length) {
    body.innerHTML = '<div class="picks-empty">当前没有符合条件的品种</div>';
  } else {
    body.innerHTML = '';
    rows.forEach(function (r) {
      var el = document.createElement('div');
      el.className = 'pick';
      el.innerHTML =
        '<span class="nm">' + esc(r.name) + '</span>' +
        '<span class="ex">' + esc(r.exch || '') + '</span>' +
        (r.tags || []).map(function (t) {
          return '<span class="tg ' + (t.cls || '') + '">' + esc(t.text) + '</span>';
        }).join('') +
        '<span class="pv">' + esc(r.price) + '</span>' +
        '<span class="pc">' + esc(r.pct) + '</span>' +
        '<span class="acts"></span>';
      el.title = '点击整行打开该品种行情';
      el.addEventListener('click', function () {
        window.open('/行情?code=' + encodeURIComponent(r.code), '_blank');
      });
      // 行内「解锁」按钮：立即停止该侧阈值预警
      var acts = $('.acts', el);
      (r.acts || []).forEach(function (a) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'act ' + (a.side || '');
        b.textContent = a.text;
        b.title = a.title || '';
        if (!a.can) { b.disabled = true; }
        b.addEventListener('click', function (ev) {
          ev.stopPropagation();
          if (a.can) unlockThr(r, a);
        });
        acts.appendChild(b);
      });
      body.appendChild(el);
    });
  }
  mask.classList.add('on');
  box.classList.add('on');
}

/* 用服务端返回的 item 同步本地状态（S / CUR），再整体重绘 */
function syncItem(item) {
  if (!item || !item.code) return;
  ['above', 'below'].forEach(function (k) {
    var arr = (S && S[k]) || [];
    for (var i = 0; i < arr.length; i++) {
      if (arr[i].code === item.code) { arr[i] = item; break; }
    }
  });
  CUR[item.code] = item;
}

function unlockThr(r, a) {
  var key = r.code + '|' + a.side;
  if (PICKS_BUSY[key]) return;
  PICKS_BUSY[key] = true;
  post('/api/threshold', {
    code: r.code, side: a.side, action: 'lock', locked: false
  }).then(function (res) {
    delete PICKS_BUSY[key];
    if (!res.ok) { toast(res.message || '解锁失败', 'bad'); return; }
    if (res.item) {
      var it = res.item;
      var t = it[a.side] || {};
      t.locked = false; t.armed = false;   // 立即生效，不等下一轮计算
      syncItem(it);
    } else {
      var it2 = CUR[r.code];
      if (it2 && it2[a.side]) { it2[a.side].locked = false; it2[a.side].armed = false; }
    }
    render();
    toast(r.name + ' ' + (a.side === 'up' ? '上阈' : '下阈') + ' 已解锁（阈值保留，不再预警）', 'warn');
    refreshPicks();
  }).catch(function () {
    delete PICKS_BUSY[key];
    toast('解锁失败，请重试', 'bad');
  });
}

/* 解锁后按当前类别重绘弹窗内容 */
function refreshPicks() {
  if (!$('#pickBox').classList.contains('on')) return;
  if (PICKS_KIND === 'armed') {
    var rows = armedRows();
    openPicks('已锁定阈值的品种（' + rows.length + ' 个）', rows, 'armed');
  } else if (PICKS_KIND === 'trig') {
    var r2 = trigRows();
    openPicks('触发中的品种（' + r2.length + ' 个 · 门槛 ' +
      num(S.minPercent, 70) + '%）', r2, 'trig');
  }
}

function closePicks() {
  $('#pickMask').classList.remove('on');
  $('#pickBox').classList.remove('on');
}

/* 已锁定阈值的品种明细 */
function armedRows() {
  var rows = [];
  S.above.concat(S.below).forEach(function (it) {
    var tags = [], acts = [];
    ['up', 'down'].forEach(function (sd) {
      var t = it[sd] || {};
      if (!t.mode) return;
      var locked = !!t.locked;
      if (locked || t.armed) {
        tags.push({
          cls: sd,
          text: (sd === 'up' ? '上阈 ' : '下阈 ') + (t.value === null || t.value === undefined
            ? '--' : fmtPrice(t.value, it.digits)) +
            (locked ? ' 已锁定' : ' 未锁定')
        });
      }
      if (locked || t.armed) {
        acts.push({
          side: sd, can: locked,
          text: locked ? ('🔓 解锁' + (sd === 'up' ? '上阈' : '下阈')) : '已解锁',
          title: locked
            ? '解锁后该阈值立即退出预警（阈值保留，可再锁定）'
            : '该阈值当前已解锁，不参与预警'
        });
      }
    });
    if (!tags.length) return;
    rows.push({
      code: it.code, name: it.name, exch: it.exch, tags: tags, acts: acts,
      price: fmtPrice(it.price, it.digits), pct: num(it.percent, 0).toFixed(2) + '%'
    });
  });
  rows.sort(function (x, y) { return parseFloat(y.pct) - parseFloat(x.pct); });
  return rows;
}

/* 触发中的品种明细 */
function trigRows() {
  var byCode = {};
  S.above.concat(S.below).forEach(function (it) { byCode[it.code] = it; });
  var rows = [];
  (S.alerts || []).forEach(function (al) {
    var it = byCode[al.code] || {};
    var up = String(al.state || '') === 'up';
    var dg = it.digits === undefined ? (al.digits || 2) : it.digits;
    var tags = [{ cls: up ? 'up' : 'down', text: up ? '上穿上阈' : '下穿下阈' }];
    if (al.limit !== null && al.limit !== undefined) {
      tags.push({ cls: '', text: '阈值 ' + fmtPrice(al.limit, dg) });
    }
    if (al.thrLabel && al.thrLabel !== '-') tags.push({ cls: '', text: al.thrLabel });
    // 发信状态：说明这一条到底有没有发过邮件、为什么
    if (al.phaseLabel) {
      tags.push({
        cls: (al.phase === 'holding' ? '' : (up ? 'up' : 'down')),
        text: al.phaseLabel
      });
    }
    // 已锁定的阈值都可以在弹窗里直接解锁，解锁即停止后续预警
    var acts = [];
    ['up', 'down'].forEach(function (sd) {
      var t = it[sd] || {};
      if (!t.mode || !t.locked) return;
      acts.push({
        side: sd, can: true,
        text: '🔓 解锁' + (sd === 'up' ? '上阈' : '下阈'),
        title: '解锁后该阈值立即退出预警（阈值保留，可再锁定）'
      });
    });
    rows.push({
      code: al.code,
      name: it.name || al.name || al.code,
      exch: it.exch || al.exch || '',
      tags: tags,
      acts: acts,
      price: fmtPrice(al.price || it.price, dg),
      pct: (it.percent === undefined || it.percent === null
        ? '' : num(it.percent, 0).toFixed(2) + '%')
    });
  });
  return rows;
}

/* ---------------- 异常提示条 ---------------- */
var _alertOpen = '', _alertHidden = '';

function updateAlert(conn) {
  var bar = $('#alertBar');
  if (!bar) return;
  var err = conn.ok ? '' : String(conn.err || S.error || '');
  if (!err) {
    bar.style.display = 'none';
    _alertOpen = ''; _alertHidden = '';
    return;
  }
  if (err === _alertHidden) { bar.style.display = 'none'; return; }
  bar.style.display = '';
  if (err !== _alertOpen) {
    _alertOpen = err;
    $('#alertMsg').textContent = err;
    $('#alertCopy').style.display =
      /pip install tqsdk/.test(err) ? '' : 'none';
  }
}

/* 从错误文本里抠出「可直接复制」的修复命令 */
function fixCommand() {
  var m = /"([^"]+)"\s+-m\s+pip\s+install\s+tqsdk/.exec(_alertOpen);
  if (!m) return '';
  return '"' + m[1] + '" -m pip install tqsdk' +
         ' -i https://mirrors.aliyun.com/pypi/simple/';
}

function copyText(s) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(s);
  }
  try {
    var ta = document.createElement('textarea');
    ta.value = s;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    return Promise.resolve();
  } catch (e) { return Promise.reject(e); }
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
  var bm = S.baseMeta || {};
  var n = num(bm.overrideCount, 0);
  $('#baseState').innerHTML = n
    ? ('已导入 <b>' + n + '</b> 个品种的自定义基准 · 导入时间 <b>' +
       esc(bm.updatedAt || '--') + '</b>（优先级高于 Excel 内置值）')
    : '当前<b>未导入</b>自定义基准 —— 使用 Excel 汇总值 + 自动计算值。';
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

  // 统计卡点击 → 品种明细（行内可直接解锁）
  $('#cardArmed').addEventListener('click', function () {
    var rows = armedRows();
    openPicks('已锁定阈值的品种（' + rows.length + ' 个）', rows, 'armed');
  });
  $('#cardTrig').addEventListener('click', function () {
    var rows = trigRows();
    openPicks('触发中的品种（' + rows.length + ' 个 · 门槛 ' +
      num(S.minPercent, 70) + '%）', rows, 'trig');
  });
  $('#pickClose').addEventListener('click', closePicks);
  $('#pickMask').addEventListener('click', closePicks);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closePicks();
  });

  // 异常提示条
  $('#alertClose').addEventListener('click', function () {
    _alertHidden = _alertOpen;
    $('#alertBar').style.display = 'none';
  });
  $('#alertCopy').addEventListener('click', function () {
    var c = fixCommand();
    if (!c) { toast('没有可复制的命令', 'warn'); return; }
    copyText(c).then(function () {
      toast('修复命令已复制，粘到命令行执行即可', 'ok');
    }).catch(function () {
      toast('复制失败，请手动选中文字复制', 'warn');
    });
  });
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

  /* ---- 历史基准（H/L/M）导出 / 导入 / 恢复 ---- */
  $('#btnBaseExport').addEventListener('click', function () {
    var btn = $('#btnBaseExport');
    btn.disabled = true;
    fetch('/api/base/export')
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.blob();
      })
      .then(function (blob) {
        var d = new Date();
        var pad = function (x) { return (x < 10 ? '0' : '') + x; };
        var name = '基准HLM_' + d.getFullYear() + pad(d.getMonth() + 1) +
          pad(d.getDate()) + '_' + pad(d.getHours()) + pad(d.getMinutes()) + '.json';
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = name;
        document.body.appendChild(a);
        a.click();
        setTimeout(function () {
          URL.revokeObjectURL(a.href); a.remove();
        }, 1500);
        toast('已导出 ' + name, 'ok');
      })
      .catch(function (e) { toast('导出失败：' + e.message, 'bad'); })
      .then(function () { btn.disabled = false; });
  });

  $('#btnBaseImport').addEventListener('click', function () { $('#fileBase').click(); });

  $('#fileBase').addEventListener('change', function () {
    var f = this.files && this.files[0];
    this.value = '';
    if (!f) return;
    var rd = new FileReader();
    rd.onload = function () {
      var obj;
      try {
        obj = JSON.parse(String(rd.result).replace(/^\uFEFF/, ''));
      } catch (e) {
        toast('JSON 解析失败：' + e.message, 'bad');
        return;
      }
      var n = obj.items
        ? (Array.isArray(obj.items) ? obj.items.length : Object.keys(obj.items).length)
        : Object.keys(obj).length;
      if (!confirm('即将导入 ' + n + ' 条基准记录（H/L/M），\n' +
                   '导入值将覆盖 Excel 内置值并立即生效。\n\n确认导入？')) return;
      post('/api/base/import', obj).then(function (r) {
        var msg = (r.message || '');
        if (r.unknown && r.unknown.length) {
          msg += '；无法识别的代码 ' + r.unknown.length + ' 个：' +
            r.unknown.slice(0, 8).join('、');
        }
        toast(msg || (r.ok ? '导入完成' : '导入失败'), r.ok ? 'ok' : 'bad');
      });
    };
    rd.readAsText(f, 'utf-8');
  });

  $('#btnBaseReset').addEventListener('click', function () {
    if (!confirm('确定要丢弃已导入的基准，恢复为「Excel 汇总值 + 自动计算值」吗？')) return;
    post('/api/base/reset', {}).then(function (r) {
      toast(r.message || (r.ok ? '已恢复内置基准' : '恢复失败'), r.ok ? 'ok' : 'bad');
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
