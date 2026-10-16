/* ============================================================
   加权合约行情详情页
   · 周期：分时 / 1分 / 5分 / 15分 / 30分 / 60分 / 120分 / 日K
   · 主图指标：MA、BOLL    副图指标：VOL、MACD、KDJ、RSI、WR
   · 一直跟随行情刷新（2 秒一次增量）
   ============================================================ */
'use strict';

var UP = '#e0403f', DOWN = '#12a150';
var KIND_CN = {};
var CHARTS = null;
var ST = {
  code: '', kind: 'timeshare', bars: [], digits: 2,
  inds: { MA: true, BOLL: false, VOL: true, MACD: true, KDJ: false, RSI: false, WR: false },
  timer: null, name: '', tq: '', live: null,
  sym: '',            // 空 = 加权（仓权）；否则为真实合约代码，如 SHFE.cu2611
  symName: '',        // 当前合约显示名
  contracts: null     // {weighted:{...}, items:[...]}
};

function $(s) { return document.querySelector(s); }
function num(v, d) { var f = parseFloat(v); return isNaN(f) ? (d || 0) : f; }
function fmt(v, d) {
  var f = parseFloat(v);
  if (isNaN(f)) return '--';
  var dd = (d === undefined || d === null) ? 2 : Math.max(0, Math.min(6, d));
  return f.toLocaleString('zh-CN', { minimumFractionDigits: dd, maximumFractionDigits: dd });
}
function shortNum(v) {
  var f = Math.abs(num(v, 0));
  if (f >= 1e8) return (num(v, 0) / 1e8).toFixed(2) + '亿';
  if (f >= 1e4) return (num(v, 0) / 1e4).toFixed(2) + '万';
  return String(Math.round(num(v, 0)));
}
function isDark() { return document.documentElement.getAttribute('data-theme') === 'dark'; }

/* ---------------- 指标计算 ---------------- */
function SMA(arr, n) {
  var out = [], sum = 0;
  for (var i = 0; i < arr.length; i++) {
    sum += arr[i];
    if (i >= n) sum -= arr[i - n];
    out.push(i >= n - 1 ? sum / n : null);
  }
  return out;
}
function EMA(arr, n) {
  var out = [], k = 2 / (n + 1), prev = null;
  for (var i = 0; i < arr.length; i++) {
    prev = prev === null ? arr[i] : arr[i] * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}
function MACD(closes, fast, slow, sig) {
  var ef = EMA(closes, fast), es = EMA(closes, slow);
  var dif = ef.map(function (v, i) { return v - es[i]; });
  var dea = EMA(dif, sig);
  var macd = dif.map(function (v, i) { return (v - dea[i]) * 2; });
  return { dif: dif, dea: dea, macd: macd };
}
function KDJ(bars, n, m1, m2) {
  var k = [], d = [], j = [], pk = 50, pd = 50;
  for (var i = 0; i < bars.length; i++) {
    var s = Math.max(0, i - n + 1);
    var hh = -1e18, ll = 1e18;
    for (var t = s; t <= i; t++) { hh = Math.max(hh, bars[t].h); ll = Math.min(ll, bars[t].l); }
    var rsv = hh === ll ? 50 : (bars[i].c - ll) / (hh - ll) * 100;
    pk = (m1 - 1) / m1 * pk + 1 / m1 * rsv;
    pd = (m2 - 1) / m2 * pd + 1 / m2 * pk;
    k.push(pk); d.push(pd); j.push(3 * pk - 2 * pd);
  }
  return { k: k, d: d, j: j };
}
function RSI(closes, n) {
  var out = [], up = 0, dn = 0;
  for (var i = 0; i < closes.length; i++) {
    if (i === 0) { out.push(null); continue; }
    var ch = closes[i] - closes[i - 1];
    var u = Math.max(ch, 0), dd = Math.max(-ch, 0);
    if (i <= n) { up += u; dn += dd; out.push(dd === 0 && up === 0 ? 50 : (up + dn === 0 ? 50 : up / (up + dn) * 100)); }
    else {
      up = (up * (n - 1) + u) / n; dn = (dn * (n - 1) + dd) / n;
      out.push(up + dn === 0 ? 50 : up / (up + dn) * 100);
    }
  }
  return out;
}
function WR(bars, n) {
  var out = [];
  for (var i = 0; i < bars.length; i++) {
    var s = Math.max(0, i - n + 1), hh = -1e18, ll = 1e18;
    for (var t = s; t <= i; t++) { hh = Math.max(hh, bars[t].h); ll = Math.min(ll, bars[t].l); }
    out.push(hh === ll ? 50 : (hh - bars[i].c) / (hh - ll) * 100);
  }
  return out;
}
function BOLL(closes, n, k) {
  var ma = SMA(closes, n), up = [], dn = [];
  for (var i = 0; i < closes.length; i++) {
    if (ma[i] === null) { up.push(null); dn.push(null); continue; }
    var s = Math.max(0, i - n + 1), sum = 0, c = 0;
    for (var t = s; t <= i; t++) { sum += Math.pow(closes[t] - ma[i], 2); c++; }
    var sd = Math.sqrt(sum / c);
    up.push(ma[i] + k * sd); dn.push(ma[i] - k * sd);
  }
  return { mid: ma, up: up, dn: dn };
}

/* ---------------- 页面 ---------------- */
function initTabs() {
  var box = $('#tabs');
  box.innerHTML = '';
  Object.keys(KIND_CN).forEach(function (k) {
    var b = document.createElement('button');
    b.className = 'tab' + (k === ST.kind ? ' on' : '');
    b.textContent = KIND_CN[k];
    b.dataset.kind = k;
    b.addEventListener('click', function () {
      ST.kind = k;
      Array.prototype.forEach.call(box.children, function (x) {
        x.classList.toggle('on', x.dataset.kind === k);
      });
      load(true);
    });
    box.appendChild(b);
  });
}

function bindInds() {
  Array.prototype.forEach.call(document.querySelectorAll('.chk input'), function (inp) {
    inp.addEventListener('change', function () {
      ST.inds[inp.dataset.ind] = inp.checked;
      inp.parentNode.classList.toggle('on', inp.checked);
      draw();
    });
  });
}

/* ---------------- 合约列表（加权 + 全部真实月份合约） ----------------
   口径参照 china-futures-data-terminal：一个品种下先列「加权（仓权）」，
   再列全部真实交易合约，主力按持仓量判定；点任意一行切换上方图表。 */
function loadContracts() {
  return fetch('/api/contracts?code=' + encodeURIComponent(ST.code))
    .then(function (r) { return r.json(); })
    .then(function (r) {
      if (!r || !r.ok) { renderContracts(null); return; }
      ST.contracts = r;
      renderContracts(r);
    })
    .catch(function () { renderContracts(null); });
}

/* 把服务端返回整理成统一的行数组：[{sym,name,attr,badge,quote...}] */
function contractRows(r) {
  if (!r) return [];
  var rows = [];
  var w = r.weighted || {};
  rows.push({
    sym: '', symShow: w.sym || '', isW: true, isMain: false,
    name: w.name || ((r.name || '') + '加权'),
    q: w
  });
  (r.items || []).forEach(function (it) {
    rows.push({
      sym: it.sym, symShow: it.sym, isW: false, isMain: !!it.isMain,
      name: it.name || it.month || it.sym,
      q: it
    });
  });
  return rows;
}

function renderContracts(r) {
  var rows = contractRows(r);
  var sel = $('#selCt'), body = $('#ctBody');

  if (!r || !rows.length) {
    sel.innerHTML = '<option>（合约列表加载失败）</option>';
    body.innerHTML = '<tr><td colspan="9" class="ct-empty">' +
      (r ? '该品种没有可显示的合约' : '合约列表加载失败，请确认天勤已连接') + '</td></tr>';
    $('#ctTitle').textContent = '--';
    $('#ctSum').textContent = '';
    return;
  }

  // ---- 下拉 ----
  var keep = ST.sym;
  sel.innerHTML = '';
  rows.forEach(function (row) {
    var o = document.createElement('option');
    o.value = row.sym;
    o.textContent = (row.isW ? '加权（仓权） · ' : '') + row.name +
      '　[' + (row.symShow || '加权') + ']' + (row.isMain ? '　主力' : '');
    sel.appendChild(o);
  });
  sel.value = keep;
  sel.onchange = function () { pickSym(sel.value); };

  // ---- 表格 ----
  body.innerHTML = '';
  rows.forEach(function (row) {
    var q = row.q || {};
    var dg = q.digits === undefined ? ST.digits : q.digits;
    var chg = num(q.change, 0), pct = num(q.pct, 0);
    var cls = chg > 0 ? 'cu' : (chg < 0 ? 'cd' : 'cf');
    var tr = document.createElement('tr');
    tr.className = (row.isW ? 'wrow' : '') + (row.sym === ST.sym ? ' sel' : '');
    tr.dataset.sym = row.sym;
    tr.dataset.key = ((row.symShow || '') + ' ' + (row.name || '')).toLowerCase();
    tr.innerHTML =
      '<td class="l">' + esc2(row.symShow || (row.isW ? '加权' : '--')) + '</td>' +
      '<td class="l nm">' + esc2(row.name) + '</td>' +
      '<td class="l">' +
        (row.isW ? '<span class="badge w">加权（仓权）</span>' : '<span class="badge">真实合约</span>') +
        (row.isMain ? ' <span class="badge main">主力</span>' : '') +
      '</td>' +
      '<td class="' + cls + '">' + fmt(q.last, dg) + '</td>' +
      '<td class="' + cls + '">' + (chg >= 0 ? '+' : '') + fmt(chg, dg) + '</td>' +
      '<td class="' + cls + '">' + (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%</td>' +
      '<td>' + shortNum(q.volume) + '</td>' +
      '<td>' + shortNum(q.oi) + '</td>' +
      '<td>' + esc2((q.ts || '--').slice(-8)) + '</td>';
    tr.title = (row.isW ? '加权（仓权）合约，按持仓量加权' : '真实交易合约') +
      (row.isMain ? ' · 当前主力（持仓量最大）' : '') + '　点击切换图表';
    tr.addEventListener('click', function () { pickSym(row.sym); });
    body.appendChild(tr);
  });

  var real = (r.items || []).length;
  $('#ctTitle').textContent = (r.name || '') + '　加权 1 个 + 真实合约 ' + real + ' 个';
  $('#ctSum').innerHTML = '加权 <b>1</b> · 真实合约 <b>' + real + '</b> · 主力 <b>' +
    esc2((r.main || '').split('.').pop() || '--') + '</b>';
  applyFilter();
}

/* 筛选框：只显示匹配的合约行（回车直接切到第一行） */
function applyFilter() {
  var kw = String(($('#ctFilter') || {}).value || '').trim().toLowerCase();
  Array.prototype.forEach.call($('#ctBody').children, function (tr) {
    if (!tr.dataset || tr.dataset.key === undefined) return;
    tr.style.display = (!kw || tr.dataset.key.indexOf(kw) >= 0) ? '' : 'none';
  });
}

function pickSym(sym) {
  sym = sym || '';
  if (ST.sym === sym) return;
  ST.sym = sym;
  var nm = '';
  if (ST.sym && ST.contracts) {
    (ST.contracts.items || []).forEach(function (it) {
      if (it.sym === ST.sym) nm = it.name || it.sym;
    });
  }
  ST.symName = nm;
  if (ST.contracts) renderContracts(ST.contracts);
  try {
    var u = new URL(location.href);
    if (ST.sym) u.searchParams.set('sym', ST.sym);
    else u.searchParams.delete('sym');
    history.replaceState(null, '', u.toString());
  } catch (e) {}
  load(true);
}

function esc2(s) { return String(s === undefined || s === null ? '' : s); }

function load(first) {
  var url = '/api/kline?code=' + encodeURIComponent(ST.code) +
            '&kind=' + encodeURIComponent(ST.kind) +
            (ST.sym ? '&sym=' + encodeURIComponent(ST.sym) : '');
  $('#cStatus').textContent = '加载中…';
  fetch(url).then(function (r) { return r.json(); }).then(function (r) {
    if (!r.ok) { $('#cStatus').innerHTML = '<span class="err">' + (r.message || '加载失败') + '</span>'; return; }
    var d = r.data;
    ST.bars = d.bars || [];
    ST.digits = d.digits || 2;
    ST.name = d.name || '';
    ST.tq = d.tq || '';
    ST.isWeighted = (d.isWeighted !== false);
    // 加权时 sym 保持空字符串（与合约列表行的取值一致），真实合约时回显其代码
    if (!ST.isWeighted && d.sym) ST.sym = d.sym;
    else if (ST.isWeighted) ST.sym = '';
    ST.symName = (d.symName && !ST.isWeighted) ? d.symName : (ST.symName || '');
    var selEl = $('#selCt');
    if (selEl && selEl.value !== ST.sym && selEl.options.length > 1) selEl.value = ST.sym;
    ST.kindCn = (d.kindCn || {})[ST.kind] || d.kindCn || '';
    renderHeader(d);
    draw();
    $('#cStatus').textContent = '已更新 ' + new Date().toLocaleTimeString('zh-CN');
  }).catch(function () {
    $('#cStatus').innerHTML = '<span class="err">网络错误</span>';
  });
}

function renderHeader(d) {
  var q = d.quote || {}, b = d.base || {};
  var dg = ST.digits;
  var isW = (d.isWeighted !== false);
  var symLabel = isW ? '加权（仓权）' : (ST.symName || d.sym || '真实合约');
  $('#hName').textContent = isW ? ((d.name || '') + ' 加权')
                                : ((d.name || '') + ' · ' + symLabel);
  $('#hCode').textContent = (isW ? (d.tq || '') : (d.sym || '')) + '　' + (d.exch || '');
  var isMain = false;
  if (!isW && ST.contracts) {
    (ST.contracts.items || []).forEach(function (it) {
      if (it.sym === ST.sym && it.isMain) isMain = true;
    });
  }
  var kb = $('#hKind');
  if (kb) {
    kb.className = 'badge' + (isW ? ' w' : (isMain ? ' main' : ''));
    kb.textContent = isW ? '加权（仓权）' : ('真实合约' + (isMain ? ' · 主力' : ''));
  }
  document.title = (d.name || '') + (isW ? ' 加权合约行情' : ' ' + symLabel + ' 行情');
  var last = num(q.last, 0);
  ST.live = last;
  $('#hPrice').textContent = fmt(last, dg);
  var chg = num(q.change, 0), pct = num(q.pct, 0);
  var el = $('#hChg');
  el.textContent = (chg >= 0 ? '+' : '') + fmt(chg, dg) + '　' + (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
  el.style.color = chg > 0 ? UP : (chg < 0 ? DOWN : 'var(--text-3)');
  $('#hPrice').style.color = chg > 0 ? UP : (chg < 0 ? DOWN : 'var(--text)');
  $('#hOpen').textContent = fmt(q.open, dg);
  $('#hHigh').textContent = fmt(q.high, dg);
  $('#hLow').textContent = fmt(q.low, dg);
  $('#hPre').textContent = fmt(q.presettle, dg);
  $('#hVol').textContent = shortNum(q.volume);
  $('#hOi').textContent = shortNum(q.oi);
  $('#hBH').textContent = fmt(b.H, dg);
  $('#hBL').textContent = fmt(b.L, dg);
  $('#hBM').textContent = fmt(b.M, dg);
  var M = num(b.M, 0), H = num(b.H, 0), L = num(b.L, 0), pc;
  if (last >= M) pc = (H - M) > 0 ? (last - M) / (H - M) * 100 : 0;
  else pc = (L - M) < 0 ? (last - M) / (L - M) * 100 : 0;
  var pe = $('#hPct');
  pe.textContent = num(pc, 0).toFixed(2) + '%';
  pe.style.color = last >= M ? UP : DOWN;
  $('#hTs').textContent = (q.ts || '--').replace('T', ' ');
  var tsv = new Date((q.ts || '').replace(/-/g, '/'));
  var delay = isNaN(tsv.getTime()) ? -1 : (Date.now() - tsv.getTime());
  $('#hDelay').textContent = delay < 0 ? '未知'
    : (delay < 1000 ? delay + ' 毫秒'
      : (delay < 60000 ? (delay / 1000).toFixed(1) + ' 秒'
        : (delay / 60000).toFixed(1) + ' 分钟'));
  $('#fName').textContent = (d.name || '--') + (isW ? '（加权）' : '（' + symLabel + '）');
  $('#fTq').textContent = isW ? (d.tq || '--') : (d.sym || '--');
  $('#fKind').textContent = (d.kindCn || {})[ST.kind] || ST.kind;
  $('#fBars').textContent = ST.bars.length + ' 根';
}

/* ---------------- 绘图 ---------------- */
function themeColors() {
  var d = isDark();
  return {
    text: d ? '#c8d4e0' : '#3f4b58',
    axis: d ? '#33414f' : '#dde3ea',
    split: d ? 'rgba(255,255,255,.045)' : 'rgba(20,30,45,.055)',
    bg: 'transparent',
    tooltipBg: d ? 'rgba(20,30,40,.94)' : 'rgba(255,255,255,.97)',
    tooltipFg: d ? '#e8eef5' : '#16202b'
  };
}
var MA_COLORS = ['#e8a33d', '#4f8cff', '#a855f7', '#12a150', '#ef4444', '#14b8a6'];

function baseTooltip(C) {
  return {
    trigger: 'axis',
    axisPointer: { type: 'cross', label: { backgroundColor: 'transparent' } },
    backgroundColor: C.tooltipBg,
    borderColor: C.axis,
    textStyle: { color: C.tooltipFg, fontSize: 12 },
    confine: true
  };
}

function draw() {
  if (!CHARTS) CHARTS = echarts.init($('#chart'), null, { renderer: 'canvas' });
  var C = themeColors();
  if (ST.kind === 'timeshare') return drawTimeshare(C);

  var bars = ST.bars;
  var cat = bars.map(function (b) { return b.day + ' ' + (b.tm || ''); });
  var ohlc = bars.map(function (b) { return [b.o, b.c, b.l, b.h]; });
  var closes = bars.map(function (b) { return b.c; });
  var vols = bars.map(function (b) { return b.v; });
  var subList = [];
  ['VOL', 'MACD', 'KDJ', 'RSI', 'WR'].forEach(function (k) { if (ST.inds[k]) subList.push(k); });

  var mainTop = 8, mainH = subList.length ? 50 : 76;
  var gap = 3, subTop0 = mainTop + mainH + 4;
  var subH = subList.length ? (96 - subTop0 - gap * (subList.length - 1)) / subList.length : 0;

  var grids = [{ left: 62, right: 62, top: mainTop + '%', height: mainH + '%' }];
  var xAxes = [{ type: 'category', data: cat, gridIndex: 0, boundaryGap: true,
                 axisLine: { lineStyle: { color: C.axis } }, axisLabel: { show: false },
                 axisTick: { show: false }, splitLine: { show: false },
                 axisPointer: { label: { show: false } } }];
  var yAxes = [{ scale: true, gridIndex: 0,
                 splitLine: { lineStyle: { color: C.split } },
                 axisLine: { show: false }, axisTick: { show: false },
                 axisLabel: { color: C.text, fontSize: 11 },
                 axisPointer: { label: { backgroundColor: C.axis, color: C.tooltipFg } } }];

  var series = [{
    name: 'K线', type: 'candlestick', data: ohlc,
    xAxisIndex: 0, yAxisIndex: 0,
    itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
    barMaxWidth: 14
  }];
  var legend = ['K线'];

  if (ST.inds.MA) {
    [5, 10, 20, 30, 60].forEach(function (n, i) {
      var v = SMA(closes, n);
      series.push({ name: 'MA' + n, type: 'line', data: v, smooth: true, showSymbol: false,
        xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1.2, color: MA_COLORS[i] },
        itemStyle: { color: MA_COLORS[i] } });
      legend.push('MA' + n);
    });
  }
  if (ST.inds.BOLL) {
    var bl = BOLL(closes, 20, 2);
    [['BOLL上轨', bl.up, '#a855f7'], ['BOLL中轨', bl.mid, '#8b95a3'], ['BOLL下轨', bl.dn, '#14b8a6']]
      .forEach(function (t) {
        series.push({ name: t[0], type: 'line', data: t[1], smooth: true, showSymbol: false,
          xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1.1, color: t[2], type: 'dashed' },
          itemStyle: { color: t[2] } });
        legend.push(t[0]);
      });
  }

  subList.forEach(function (k, i) {
    var top = subTop0 + i * (subH + gap);
    var gi = i + 1;
    grids.push({ left: 62, right: 62, top: top + '%', height: subH + '%' });
    xAxes.push({ type: 'category', data: cat, gridIndex: gi, boundaryGap: true,
                 axisLine: { lineStyle: { color: C.axis } }, axisTick: { show: false },
                 splitLine: { show: false },
                 axisLabel: { color: C.text, fontSize: 11, show: i === subList.length - 1,
                              hideOverlap: true },
                 axisPointer: { label: { show: i === subList.length - 1 } } });
    yAxes.push({ scale: true, gridIndex: gi, splitNumber: 2,
                 splitLine: { lineStyle: { color: C.split } },
                 axisLine: { show: false }, axisTick: { show: false },
                 axisLabel: { color: C.text, fontSize: 10 } });

    if (k === 'VOL') {
      series.push({
        name: '成交量', type: 'bar', xAxisIndex: gi, yAxisIndex: gi,
        data: vols.map(function (v, n) {
          return { value: v, itemStyle: { color: bars[n].c >= bars[n].o ? UP : DOWN, opacity: .72 } };
        }),
        barMaxWidth: 12
      });
      legend.push('成交量');
    } else if (k === 'MACD') {
      var m = MACD(closes, 12, 26, 9);
      series.push({ name: 'MACD', type: 'bar', xAxisIndex: gi, yAxisIndex: gi,
        data: m.macd.map(function (v) {
          return { value: v, itemStyle: { color: v >= 0 ? UP : DOWN, opacity: .8 } };
        }), barMaxWidth: 10 });
      series.push({ name: 'DIF', type: 'line', data: m.dif, showSymbol: false, smooth: true,
        xAxisIndex: gi, yAxisIndex: gi, lineStyle: { width: 1.2, color: '#e8a33d' }, itemStyle: { color: '#e8a33d' } });
      series.push({ name: 'DEA', type: 'line', data: m.dea, showSymbol: false, smooth: true,
        xAxisIndex: gi, yAxisIndex: gi, lineStyle: { width: 1.2, color: '#4f8cff' }, itemStyle: { color: '#4f8cff' } });
      legend.push('DIF', 'DEA', 'MACD');
    } else if (k === 'KDJ') {
      var kd = KDJ(bars, 9, 3, 3);
      [['K', kd.k, '#e8a33d'], ['D', kd.d, '#4f8cff'], ['J', kd.j, '#a855f7']].forEach(function (t) {
        series.push({ name: t[0], type: 'line', data: t[1], showSymbol: false, smooth: true,
          xAxisIndex: gi, yAxisIndex: gi, lineStyle: { width: 1.2, color: t[2] }, itemStyle: { color: t[2] } });
        legend.push(t[0]);
      });
    } else if (k === 'RSI') {
      [[6, '#e8a33d'], [12, '#4f8cff'], [24, '#12a150']].forEach(function (t) {
        series.push({ name: 'RSI' + t[0], type: 'line', data: RSI(closes, t[0]), showSymbol: false,
          smooth: true, xAxisIndex: gi, yAxisIndex: gi,
          lineStyle: { width: 1.2, color: t[1] }, itemStyle: { color: t[1] } });
        legend.push('RSI' + t[0]);
      });
    } else if (k === 'WR') {
      series.push({ name: 'WR14', type: 'line', data: WR(bars, 14), showSymbol: false, smooth: true,
        xAxisIndex: gi, yAxisIndex: gi, lineStyle: { width: 1.2, color: '#a855f7' }, itemStyle: { color: '#a855f7' } });
      legend.push('WR14');
    }
  });

  CHARTS.setOption({
    animation: false,
    backgroundColor: 'transparent',
    textStyle: { color: C.text },
    legend: { data: legend, top: 2, left: 62, itemWidth: 14, itemHeight: 8,
              textStyle: { color: C.text, fontSize: 11 }, inactiveColor: C.axis },
    tooltip: baseTooltip(C),
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    dataZoom: [
      { type: 'inside', xAxisIndex: xAxes.map(function (_, i) { return i; }),
        start: Math.max(0, 100 - 100 * Math.min(240, cat.length) / Math.max(1, cat.length)),
        end: 100 },
      { type: 'slider', xAxisIndex: xAxes.map(function (_, i) { return i; }),
        bottom: 4, height: 18, borderColor: C.axis,
        textStyle: { color: C.text, fontSize: 10 },
        start: Math.max(0, 100 - 100 * Math.min(240, cat.length) / Math.max(1, cat.length)),
        end: 100 }
    ],
    series: series
  }, true);
}

function drawTimeshare(C) {
  var bars = ST.bars;
  var cat = bars.map(function (b) { return b.time; });
  var price = bars.map(function (b) { return b.price; });
  var avg = bars.map(function (b) { return b.avg; });
  var vols = bars.map(function (b) { return b.v; });

  CHARTS.setOption({
    animation: false,
    backgroundColor: 'transparent',
    textStyle: { color: C.text },
    legend: { data: ['价格', '均价', '成交量'], top: 2, left: 62,
              textStyle: { color: C.text, fontSize: 11 } },
    tooltip: {
      trigger: 'axis',
      backgroundColor: C.tooltipBg, borderColor: C.axis,
      textStyle: { color: C.tooltipFg, fontSize: 12 }, confine: true,
      formatter: function (ps) {
        var s = ps[0].axisValue;
        ps.forEach(function (p) {
          var v = p.seriesName === '成交量' ? shortNum(p.value) : fmt(p.value, ST.digits);
          s += '<br/>' + p.marker + p.seriesName + '：<b>' + v + '</b>';
        });
        return s;
      }
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [{ left: 62, right: 62, top: '8%', height: '52%' },
           { left: 62, right: 62, top: '68%', height: '20%' }],
    xAxis: [
      { type: 'category', data: cat, gridIndex: 0, boundaryGap: false,
        axisLine: { lineStyle: { color: C.axis } }, axisLabel: { show: false },
        axisTick: { show: false }, splitLine: { show: false } },
      { type: 'category', data: cat, gridIndex: 1, boundaryGap: false,
        axisLine: { lineStyle: { color: C.axis } }, axisTick: { show: false },
        splitLine: { show: false }, axisLabel: { color: C.text, fontSize: 11, hideOverlap: true } }
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: C.split } },
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: C.text, fontSize: 11 } },
      { scale: true, gridIndex: 1, splitNumber: 2, splitLine: { lineStyle: { color: C.split } },
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: C.text, fontSize: 10,
                     formatter: function (v) { return shortNum(v); } } }
    ],
    series: [
      { name: '价格', type: 'line', data: price, showSymbol: false, smooth: false,
        xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1.6, color: '#2563eb' },
        itemStyle: { color: '#2563eb' },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: 'rgba(37,99,235,.22)' },
                       { offset: 1, color: 'rgba(37,99,235,0)' }] } } },
      { name: '均价', type: 'line', data: avg, showSymbol: false, smooth: true,
        xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1.3, color: '#e8a33d' },
        itemStyle: { color: '#e8a33d' } },
      { name: '成交量', type: 'bar', data: vols, xAxisIndex: 1, yAxisIndex: 1,
        itemStyle: { color: 'rgba(37,99,235,.55)' }, barMaxWidth: 6 }
    ]
  }, true);
}

/* ---------------- 启动 ---------------- */
window.addEventListener('DOMContentLoaded', function () {
  var t = localStorage.getItem('wq_theme');
  if (t) document.documentElement.setAttribute('data-theme', t);
  var qs = new URLSearchParams(location.search);
  ST.code = qs.get('code') || 'CU';
  ST.sym = qs.get('sym') || '';
  var k = qs.get('kind');
  KIND_CN = { timeshare: '分时', m1: '1分', m5: '5分', m15: '15分',
              m30: '30分', h1: '60分', h2: '120分', d: '日K' };
  ST.kind = (k && KIND_CN[k]) ? k : 'timeshare';
  bindInds();
  initTabs();
  var f = $('#ctFilter');
  if (f) {
    f.addEventListener('input', applyFilter);
    f.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      var first = null;
      Array.prototype.forEach.call($('#ctBody').children, function (tr) {
        if (!first && tr.style.display !== 'none' && tr.dataset.sym !== undefined) first = tr;
      });
      if (first) pickSym(first.dataset.sym);
    });
  }
  load(true);
  loadContracts();
  ST.timer = setInterval(function () { load(false); }, 2000);
  // 合约列表（主力标记/持仓）每 60 秒刷新一次
  setInterval(loadContracts, 60000);
  window.addEventListener('resize', function () { if (CHARTS) CHARTS.resize(); });
});
