# -*- coding: utf-8 -*-
"""
加权（仓权）合约监控面板 · 服务端（Python 标准库 + 天勤 TqSdk）
==================================================================

数据源：天勤 TqSdk 官方「指数合约」``KQ.i@交易所.品种``（持仓量加权 = 仓权合约）
        **不使用主连合约**。

核心能力（对应本次重构的 11 条需求）
  1  全部改用加权（仓权）合约行情
  2  百分比口径不变，历史 H/L/M 换用「汇总_全部品种.xlsx」的新数值
  3  每个品种下方「查看行情」按钮 → 新窗口展示 分时/1分/5分/15分/30分/60分/120分/日K + 技术指标
  4  偏离度 ≥ 门槛时可填「周期 × 均线周期」的动态均线阈值，支持偏移量与锁定/解锁
  5  只发邮件（卡片式），删除弹窗 / 新窗口 / 声音
  6  非交易时段不预警
  7  不设刷新间隔，持续跟随行情；界面显示数据延时
  8  用 1 分钟K线的穿越判定预警，天然避免持续轰炸
  9  天勤账号 / 邮件账号等全部可在界面配置
  10 界面美化
  11 Ctrl+Z 一键隐身

启动：
    python 服务端.py                    # 默认端口 8962，自动开浏览器
    python 服务端.py --port 9000 --no-browser
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _res_dir() -> str:
    """只读资源目录（打包后 = PyInstaller 解包目录）。"""
    return getattr(sys, "_MEIPASS", _HERE)


def _data_dir() -> str:
    """可写数据目录（配置 / 缓存）。"""
    if _is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return _HERE


BASE_DIR = _res_dir()
DATA_DIR = _data_dir()
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, DATA_DIR)

import importlib

# ---- 环境自检 / 自愈 --------------------------------------------------------- #
# 本机可能装了多个 Python。若「当前这个」没有天勤 tqsdk（典型的「用 A 装了、
# 用 B 启动」），这里会自动改用装了天勤的那个解释器重启自己；一个都没有就自动
# 联网安装。这样无论双击 bat、命令行、IDE 还是快捷方式启动，都不会再出现
# 「当前 Python 未安装天勤 tqsdk」。
_SELF = importlib.import_module("环境自检")
if not _SELF.ensure(server_path=os.path.abspath(__file__), argv=sys.argv[1:]):
    sys.stderr.write(_SELF.fail_hint())
    sys.stderr.flush()

PT = importlib.import_module("品种表")
TQ = importlib.import_module("天勤行情")
MAIL = importlib.import_module("邮件预警")

CACHE_DIR = os.path.join(DATA_DIR, "缓存")
os.makedirs(CACHE_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(CACHE_DIR, "配置.json")
THRESH_FILE = os.path.join(CACHE_DIR, "阈值设置.json")
ALERT_FILE = os.path.join(CACHE_DIR, "预警记录.json")
BASE_FILE = os.path.join(CACHE_DIR, "基准_HLM.json")
STATIC_DIR = os.path.join(BASE_DIR, "静态")

KIND_ORDER = TQ.KIND_ORDER
THR_KINDS = TQ.THR_KINDS
MA_CHOICES = TQ.MA_CHOICES
OFFSET_CHOICES = TQ.OFFSET_CHOICES

# --------------------------------------------------------------------------- #
# 默认配置（首次运行时写入 缓存/配置.json，之后以文件为准）
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG = {
    "panel": {
        "theme": "light",                # light | dark
        "minPercent": 70.0,              # 可设阈门槛（偏离度 %），可调 60~90
        "thrKind": "m15",                # 默认周期
        "thrMA": 20,                     # 默认均线周期
        "thrOffset": 0.2,                # 默认偏移量 %
        "thrOffsetDir": "down",          # 默认下偏移
        "showDelay": True,               # 显示数据延时
        "autoSort": True,                # 两栏按偏离度降序
        # ---- 预警（只发邮件） ---- #
        "mailOn": True,
        "mailTradingOnly": True,         # 非交易时段不预警
        "mailCooldown": 300,             # 同品种同方向冷却（秒）
        "mailMaxPerHour": 12,
        "mailMaxPerDay": 60,
        "alertCandle": "m1",             # 用 1 分钟K线穿越判定
        "alertOnStart": False,           # 启动时不补发存量触发
    },
    "mail": {
        "host": "smtp.126.com",
        "port": 465,
        "ssl": True,
        "user": "",                      # 发件邮箱，如 yourname@126.com（设置面板里填）
        "authcode": "",                  # SMTP 授权码（邮箱设置里生成，不是登录密码）
        "from_name": "加权合约监控",
        "to": [],
        "cc": [],
    },
    "tianqin": {
        "user": "",                      # 快期账号（设置面板里填，account.shinnytech.com 免费注册）
        "password": "",
    },
}


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
# 统计 / 指标
# --------------------------------------------------------------------------- #
def dynamic_ma(bars: list, n: int, live: float | None) -> float | None:
    """动态均线 = （最近 n−1 根已收盘 + 当前实时价）/ n。

    最后一根 K 线视为「当前未收盘 bar」，用实时价替换 —— 因此每个 tick 都在变。
    """
    cs = [b["c"] for b in bars if b.get("c", 0) > 0]
    if not cs:
        return None
    live = live if (live and live > 0) else cs[-1]
    if n <= 1:
        return round(live, 6)
    closed = cs[:-1] if len(cs) >= 2 else cs
    tail = closed[-(n - 1):] if closed else []
    vals = list(tail) + [live]
    return round(sum(vals) / len(vals), 6)


def quote_ts(q: dict):
    """从行情快照取「最新数据时刻」（秒级时间戳）。天勤返回 'YYYY-MM-DD HH:MM:SS.ffffff'。"""
    t = q.get("ts")
    if isinstance(t, (int, float)) and t > 0:
        return t / 1000.0 if t > 1e11 else float(t)
    if isinstance(t, str) and len(t) >= 16:
        try:
            return time.mktime(time.strptime(t[:19], "%Y-%m-%d %H:%M:%S"))
        except Exception:                                       # noqa: BLE001
            return None
    return None


# 各交易所日内交易时段（北京时间，分钟，自 00:00 起算；>1440 表示跨日）
EXCH_SESSIONS = {
    "上期所":   [(540, 615), (630, 690), (810, 900), (1260, 1500)],
    "上期能源": [(540, 615), (630, 690), (810, 900), (1260, 1500)],
    "大商所":   [(540, 615), (630, 690), (810, 900), (1260, 1380)],
    "郑商所":   [(540, 615), (630, 690), (810, 900), (1260, 1380)],
    "广期所":   [(540, 615), (630, 690), (810, 900)],
    "中金所":   [(570, 690), (780, 900)],
}


def in_session(exch: str, dt) -> bool:
    spans = EXCH_SESSIONS.get(exch)
    if not spans:
        return True
    m = dt.hour * 60 + dt.minute
    wd = dt.weekday()
    prev_wd = (wd - 1) % 7
    for a, b in spans:
        if b <= 1440:
            if wd < 5 and a <= m < b:
                return True
        else:
            if wd < 5 and m >= a:
                return True
            if prev_wd < 5 and m < (b - 1440):
                return True
    return False


def market_state(q: dict, exch: str, stale_sec: int, now: float | None = None):
    """判断该品种此刻是否处于「可发信的交易状态」。返回 (bool, 原因)。

    优先级：① 行情新鲜度（距最新行情 ≤ stale_sec 秒）→ 交易中
            ② 交易所时段表兜底
    行情时间戳天然就是最新交易时刻，闭市即冻结，因此自适应夜盘差异 / 节假日。
    """
    now = now or time.time()
    ts = quote_ts(q)
    if ts:
        age = now - ts
        if age < -180:
            pass                                    # 明显未来 → 不信任
        elif age <= stale_sec:
            return True, "行情 %.0f 秒前更新" % max(0.0, age)
        else:
            if age < 7200:
                return False, "行情已 %.1f 分钟未更新，视为闭市" % (age / 60.0)
            return False, "行情已 %.1f 小时未更新，视为闭市" % (age / 3600.0)
    ok = in_session(exch, time.localtime(now))
    return ok, ("交易所时段表：交易时段" if ok else "交易所时段表：非交易时段")


def _r(v, n=4):
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return 0.0


# 各周期向天勤**订阅**的根数（订阅一次就固定长度，之后按需截断返回）
SRC_LEN = {"timeshare": 1200, "m1": 400, "m5": 500, "m15": 500,
           "m30": 500, "h1": 500, "h2": 500, "d": 3600}


# --------------------------------------------------------------------------- #
# 应用主体
# --------------------------------------------------------------------------- #
class App:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = self._load_json(CONFIG_FILE, DEFAULT_CONFIG, merge=True)
        self.thresholds = self._load_json(THRESH_FILE, {}, merge=False)
        self.src = TQ.TianqinSource(self.config["tianqin"].get("user", ""),
                                    self.config["tianqin"].get("password", ""))
        self.quotes: dict = {}
        self.items: dict = {}
        self.alerts: list = []          # 当前处于触发价（面板展示）
        self.fired: list = []           # 最近发出的预警
        self.mail_log: list = []
        self.notes: list = []
        self.mail_sent: list = []       # 发信时间戳（配额）
        self.last_mail: dict = {}
        self.sup = {"closed": 0, "cooldown": 0, "quota": 0, "unlocked": 0,
                    "trading": 0}
        self.error = ""
        self.started = now_str()
        self.ready = False
        self.last_refresh = 0.0
        self.refresh_ms = 0
        self.delay_ms = -1
        self._bars: dict = {}           # (code, kind) -> (ts, bars)
        self._bl_lock = threading.RLock()
        self._sse: list = []            # SSE 客户端队列
        self._sse_lock = threading.Lock()
        self._cross: dict = {}          # (code, side) -> 已报过的 1分钟bar时间戳
        self._first_scan = True
        self._stop = False
        # ---- 历史基准（H/L/M）---- #
        self.base = {c: dict(PT.BASE[c]) for c in PT.BASE}
        self._base_lock = threading.RLock()
        self._base_done = False
        self._base_err = ""
        # 用户导入的基准（优先级最高，覆盖内置 Excel 值与自动计算值）
        self.base_override: dict = {}
        self.base_meta: dict = {}
        self.load_base_override()
        # 合约列表缓存 code -> (ts, data)
        self._contracts: dict = {}
        self._ct_lock = threading.RLock()

    # ---------------- 持久化 ---------------- #
    @staticmethod
    def _load_json(path: str, default, merge: bool):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
        except Exception:                                       # noqa: BLE001
            obj = None
        if obj is None:
            return json.loads(json.dumps(default, ensure_ascii=False))
        if merge:
            return deep_merge(json.loads(json.dumps(default, ensure_ascii=False)), obj)
        return obj if isinstance(obj, dict) else {}

    def _save(self, path: str, obj):
        try:
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=1)
            os.replace(path + ".tmp", path)
        except Exception:                                       # noqa: BLE001
            pass

    def save_config(self):
        self._save(CONFIG_FILE, self.config)

    def save_thresholds(self):
        self._save(THRESH_FILE, self.thresholds)

    def panel(self, k, d=None):
        return self.config.get("panel", {}).get(k, d)

    # ---------------- 历史基准（H/L/M） ---------------- #
    def load_base_override(self):
        """读取 缓存/基准_HLM.json（用户导入的历史 H/L/M），并覆盖到 self.base。"""
        obj = {}
        try:
            with open(BASE_FILE, "r", encoding="utf-8") as fh:
                obj = json.load(fh) or {}
        except Exception:                                       # noqa: BLE001
            obj = {}
        items = obj.get("items") if isinstance(obj, dict) else None
        norm = self._norm_base_items(items if items is not None else obj)
        with self._base_lock:
            self.base_override = norm
            self.base_meta = {"updatedAt": obj.get("updatedAt", ""),
                              "note": obj.get("note", ""),
                              "count": len(norm)}
            for c, v in norm.items():
                if c not in self.base:
                    v0 = PT.BY_CODE.get(c) or {}
                    self.base[c] = {"H": v["H"], "L": v["L"], "M": v["M"],
                                    "auto": False, "src": "import",
                                    "name": v0.get("name", c),
                                    "exch": v0.get("exch", "")}
                else:
                    self.base[c].update({"H": v["H"], "L": v["L"], "M": v["M"]})
                    self.base[c]["src"] = "import"
        if norm:
            print(" [基准] 已从 缓存/基准_HLM.json 载入 %d 个品种（覆盖内置值）"
                  % len(norm))

    @staticmethod
    def _norm_base_items(items) -> dict:
        """把各种写法的导入内容统一成 {code: {H, L, M}}，非法条目直接丢弃。"""
        out: dict = {}
        if isinstance(items, dict):
            it = items.items()
        elif isinstance(items, list):
            it = [(x.get("code"), x) for x in items if isinstance(x, dict)]
        else:
            return out
        for code, v in it:
            if not code or not isinstance(v, dict):
                continue
            code = str(code).strip().upper()
            try:
                H = float(v.get("H", v.get("h", v.get("high"))))
                L = float(v.get("L", v.get("l", v.get("low"))))
                M = float(v.get("M", v.get("m", v.get("mean", v.get("avg")))))
            except Exception:                                   # noqa: BLE001
                continue
            if not (H > 0 and L > 0 and M > 0) or H < L:
                continue
            out[code] = {"H": round(H, 6), "L": round(L, 6), "M": round(M, 6)}
        return out

    def base_export(self) -> dict:
        """导出当前**生效**的 H/L/M（含 Excel 内置值、自动计算值、已导入值）。"""
        items = []
        with self._base_lock:
            for v in PT.VARIETIES:
                c = v["code"]
                b = self.base.get(c) or {}
                H, L, M = b.get("H"), b.get("L"), b.get("M")
                if not (H and L and M):
                    continue
                src = "import" if c in self.base_override else (
                    "auto" if (b.get("auto") or v.get("auto")) else "excel")
                items.append({
                    "code": c, "name": v["name"], "exch": v["exch"],
                    "H": H, "L": L, "M": M,
                    "src": src,
                    "live": c not in PT.DEAD_CODES,
                })
        return {
            "type": "weighted-monitor-baseline",
            "version": 1,
            "exportedAt": now_str(),
            "source": "加权（仓权）合约监控面板",
            "usage": "修改各品种 H/L/M 后，用面板「设置 → 历史基准数据 → 导入」原样上传即可生效",
            "count": len(items),
            "items": items,
        }

    def base_import(self, body) -> dict:
        """导入 H/L/M。接受导出文件原样、或 {"items": [...]}、或 {code: {H,L,M}}。"""
        if isinstance(body, dict):
            items = body.get("items", body)
        else:
            items = body
        norm = self._norm_base_items(items)
        if not norm:
            return {"ok": False, "message": "未解析到任何有效的 H/L/M 数据"}
        unknown = [c for c in norm if c not in PT.BY_CODE]
        norm = {c: v for c, v in norm.items() if c in PT.BY_CODE}
        if not norm:
            return {"ok": False,
                    "message": "品种代码全部无法识别（示例：CU / RB / MA / IF）",
                    "unknown": unknown[:20]}
        meta = {"updatedAt": now_str(),
                "note": (body.get("note") if isinstance(body, dict) else "") or "手动导入",
                "count": len(norm)}
        self._save(BASE_FILE, {"updatedAt": meta["updatedAt"], "note": meta["note"],
                               "items": norm})
        with self._base_lock:
            self.base_override = norm
            self.base_meta = meta
            for c, v in norm.items():
                if c not in self.base:
                    v0 = PT.BY_CODE.get(c) or {}
                    self.base[c] = {"H": v["H"], "L": v["L"], "M": v["M"],
                                    "auto": False, "src": "import",
                                    "name": v0.get("name", c),
                                    "exch": v0.get("exch", "")}
                else:
                    self.base[c].update({"H": v["H"], "L": v["L"], "M": v["M"]})
                    self.base[c]["src"] = "import"
        self.note("已导入 %d 个品种的历史 H/L/M 基准" % len(norm))
        self.refresh()
        return {"ok": True, "count": len(norm), "unknown": unknown[:20],
                "message": "已导入并生效 %d 个品种" % len(norm)}

    def base_reset(self) -> dict:
        """丢弃导入值，恢复为「Excel 内置 + 自动计算」。"""
        try:
            if os.path.exists(BASE_FILE):
                os.remove(BASE_FILE)
        except Exception:                                       # noqa: BLE001
            pass
        with self._base_lock:
            self.base_override = {}
            self.base_meta = {}
            self.base = {c: dict(PT.BASE[c]) for c in PT.BASE}
            self._base_done = False
        self.build_baselines()
        self.note("已恢复内置历史基准（Excel 汇总值 + 自动计算值）")
        self.refresh()
        return {"ok": True, "message": "已恢复内置基准"}

    def build_baselines(self):
        """为「Excel 基准不可靠」的品种，按加权合约**全历史日K**自动计算 H/L/M。

        判据：品种表里 ``auto=True`` 的条目 —— 即本地日K导出被截断（交易日过少）
        或本次汇总未覆盖（铂 / 钯 / 胶版纸）的品种。
        """
        codes = [c for c in PT.AUTO_CODES
                 if c not in PT.DEAD_CODES and c not in self.base_override]
        if not codes:
            self._base_done = True
            return
        try:
            bm = self.src.bars_many(codes, "d", limit=3600) or {}
        except Exception as exc:                                # noqa: BLE001
            self._base_err = str(exc)[:160]
            bm = {}
        got = 0
        for c, bars in bm.items():
            if not bars:
                continue
            hs = [b["h"] for b in bars if b.get("h", 0) > 0]
            ls = [b["l"] for b in bars if b.get("l", 0) > 0]
            cs = [b["c"] for b in bars if b.get("c", 0) > 0]
            if not hs or not ls or not cs or max(hs) <= min(ls):
                continue
            with self._base_lock:
                self.base[c] = {"H": round(max(hs), 6), "L": round(min(ls), 6),
                                "M": round(sum(cs) / len(cs), 6),
                                "auto": True, "bars": len(bars),
                                "from": bars[0].get("day", "")}
            got += 1
        self._base_done = True
        print(" [基准] 自动计算 %d/%d 个品种（加权日K全历史）；其余使用 Excel 汇总值"
              % (got, len(codes)))

    def base_of(self, code: str) -> dict:
        with self._base_lock:
            b = self.base.get(code)
            return dict(b) if b else {"H": 0, "L": 0, "M": 0}

    # ---------------- 合约列表（加权 + 真实月份合约，带缓存） ---------------- #
    def contracts(self, code: str, force: bool = False) -> dict:
        with self._ct_lock:
            now = time.time()
            hit = self._contracts.get(code)
            if hit and not force and (now - hit[0]) < 30 and hit[1].get("ok"):
                return hit[1]
            d = self.src.contracts(code)
            if d.get("ok"):
                self._contracts[code] = (now, d)
            return d

    def contract_quote(self, code: str, sym: str) -> dict:
        """某个合约的最新报价（加权合约返回品种行情）。"""
        cq = self.contracts(code)
        if not cq.get("ok"):
            return {}
        w = cq.get("weighted") or {}
        if not sym or sym == w.get("sym"):
            return w
        for it in (cq.get("items") or []):
            if it.get("sym") == sym:
                return it
        return {}

    @staticmethod
    def _sym_allowed(code: str, sym: str) -> bool:
        """校验 sym 是否属于该品种（加权合约 或 该品种的真实月份合约）。"""
        if not sym:
            return False
        tq = PT.TQ.get(code) or ""
        if sym == tq:
            return True
        if "@" in sym or sym.startswith("KQ"):
            return False
        if tq and "@" in tq:
            tail = tq.split("@", 1)[1]           # SHFE.cu
            return sym.lower().startswith(tail.lower())
        return False


    # ---------------- 阈值结构 ---------------- #
    @staticmethod
    def _blank_side():
        return {"mode": None, "kind": None, "ma": None, "offPct": None,
                "offDir": None, "manual": None, "locked": False, "value": None}

    def thr(self, code: str, create: bool = False) -> dict:
        """取得某品种的阈值结构。

        create=False 时**不写入** ``self.thresholds`` —— 否则每轮刷新为全部品种
        取阈值会把 80+ 个空条目写进配置文件。
        """
        if create:
            t = self.thresholds.setdefault(code, {})
        else:
            t = self.thresholds.get(code)
            if not t:
                return {"up": self._blank_side(), "down": self._blank_side()}
        for side in ("up", "down"):
            if not isinstance(t.get(side), dict):
                t[side] = self._blank_side()
            else:
                t[side] = deep_merge(self._blank_side(), t[side])
        return t

    def thr_value(self, code: str, side: str, live: float | None):
        """计算某品种某侧当前阈值：返回 (value, label, offsetLabel)。

        · mode = ma      ：所选周期 K 线的动态均线（最后一根用实时价替代）
                          再按「现价 × 偏移%」做上/下偏移
        · mode = manual  ：手工输入的固定值
        """
        t = self.thr(code)
        s = t.get(side) or {}
        if s.get("mode") == "manual":
            v = s.get("manual")
            if v in (None, ""):
                return None, "-", "手工"
            return float(v), "手工输入", "手工"
        if s.get("mode") != "ma":
            return None, "-", "-"
        kind, n = s.get("kind"), s.get("ma")
        if kind not in THR_KINDS or int(n or 0) not in MA_CHOICES:
            return None, "-", "-"
        bars = self.bars(code, kind, limit=max(120, int(n) + 12))
        base = dynamic_ma(bars, int(n), live)
        if base is None:
            return None, "-", "-"
        off_pct = float(s.get("offPct") or 0.0)
        off_dir = s.get("offDir") or "down"
        cval = live if (live and live > 0) else base
        off = cval * off_pct / 100.0            # 偏移「C值（现价）」的百分比
        val = base + off if off_dir == "up" else base - off
        label = "%s MA%d" % (TQ.KIND_CN.get(kind, kind), int(n))
        off_label = ("无偏移" if off_pct <= 0
                     else "%s偏移 %.1f%%" % ("上" if off_dir == "up" else "下", off_pct))
        return _r(val, 6), label, off_label

    # ---------------- K 线缓存 ---------------- #
    def bars(self, code: str, kind: str, limit: int | None = None) -> list:
        """取某品种某周期的 K 线（服务端缓存 + 截断）。

        ⚠ 向天勤订阅的长度固定为 ``SRC_LEN[kind]`` —— tqsdk 的
        ``get_kline_serial`` 对同一 (合约, 周期) 只建立一次订阅且长度固定，
        若先用小根数订阅、之后又想要长历史，就拿不到更多数据了。
        所以这里统一按最大根数订阅，返回时再按 ``limit`` 截断。
        """
        ck = (code, kind)
        ttl = {"timeshare": 2, "m1": 2, "m5": 3, "m15": 3, "m30": 3,
               "h1": 3, "h2": 3, "d": 60}.get(kind, 3)
        now = time.time()
        with self._bl_lock:
            hit = self._bars.get(ck)
            if hit and now - hit[0] < ttl:
                b = hit[1]
                return b[-int(limit):] if limit else b
        try:
            b = self.src.kline(code, kind, SRC_LEN.get(kind, 500)).get("bars") or []
        except Exception:                                       # noqa: BLE001
            b = []
        if b:
            with self._bl_lock:
                self._bars[ck] = (now, b)
        return b[-int(limit):] if (limit and b) else b

    def _prefetch(self, jobs: dict):
        """jobs = {kind: codes} —— 批量预取，一次订阅填满缓存。"""
        for kind, codes in jobs.items():
            if not codes:
                continue
            need = []
            now = time.time()
            ttl = {"m1": 2, "m5": 3, "m15": 3, "m30": 3, "h1": 3,
                   "h2": 3, "d": 60}.get(kind, 3)
            with self._bl_lock:
                for c in codes:
                    hit = self._bars.get((c, kind))
                    if not (hit and now - hit[0] < ttl):
                        need.append(c)
            if not need:
                continue
            try:
                got = self.src.bars_many(need, kind, limit=SRC_LEN.get(kind, 500)) or {}
            except Exception:                                   # noqa: BLE001
                got = {}
            ts = time.time()
            with self._bl_lock:
                for c, b in got.items():
                    if b:
                        self._bars[(c, kind)] = (ts, b)

    # ---------------- 刷新 ---------------- #
    def refresh(self) -> dict:
        t0 = time.time()
        try:
            q = self.src.quotes()
        except Exception as exc:                                # noqa: BLE001
            q = {}
            self.error = "行情获取失败：%s" % exc
        if q:
            self.quotes = q
            self.error = ""
            self.ready = True
        elif not self.src.ok:
            self.error = self.src.status().get("err") or "天勤未连接"

        now = time.time()
        stale = 300
        it_delay = -1

        # ---- 组装面板数据 ---- #
        items = {}
        for code in PT.LIVE_CODES:
            v = PT.BY_CODE[code]
            qq = self.quotes.get(code)
            if not qq or not qq.get("last"):
                continue
            base = self.base_of(code)
            H, L, M = base.get("H", 0), base.get("L", 0), base.get("M", 0)
            if not M or H <= M or L >= M:
                continue                         # 基准缺失（收盘期未取到日K）→ 暂不展示
            p = float(qq["last"])
            if p >= M:
                den = H - M
                pct = (p - M) / den * 100.0 if den > 0 else 0.0
                above = True
            else:
                den = L - M
                pct = (p - M) / den * 100.0 if den < 0 else 0.0
                above = False
            ts = quote_ts(qq)
            delay = int((now - ts) * 1000) if ts else -1
            if delay >= 0:
                it_delay = delay if it_delay < 0 else min(it_delay, delay)
            mkt_ok, mkt_why = market_state(qq, v["exch"], stale, now)
            items[code] = {
                "code": code, "name": v["name"], "exch": v["exch"],
                "tq": v["tq"] or "-", "showName": qq.get("showName") or (v["name"] + "加权"),
                "digits": qq.get("digits", 2),
                "price": p, "open": _r(qq.get("open"), 6), "high": _r(qq.get("high"), 6),
                "low": _r(qq.get("low"), 6), "presettle": _r(qq.get("presettle"), 6),
                "prevclose": _r(qq.get("prevclose"), 6),
                "change": _r(qq.get("change"), 6), "pct": _r(qq.get("pct"), 4),
                "volume": int(qq.get("volume") or 0), "oi": int(qq.get("oi") or 0),
                "oiChange": int(qq.get("oiChange") or 0),
                "quoteTime": (qq.get("ts") or "")[:19].replace("T", " "),
                "delayMs": delay,
                "H": H, "L": L, "M": M, "percent": _r(pct, 2), "above": above,
                "baseSrc": "import" if code in self.base_override else (
                    "auto" if (base.get("auto") or PT.BY_CODE[code]["auto"])
                    else "excel"),
                "baseBars": base.get("bars") or PT.BY_CODE[code]["days"],
                "mktOpen": bool(mkt_ok), "mktNote": mkt_why,
                "up": None, "down": None,
            }
        self.items = items
        self.delay_ms = it_delay
        self.last_refresh = now
        self._compute_thresholds()
        self._check_alerts()
        self.refresh_ms = int((time.time() - t0) * 1000)
        return self.payload()

    def _compute_thresholds(self):
        """为所有已配置阈值的品种计算当前阈值（动态均线 + 偏移）。"""
        # 先把需要的高周期 K 线批量取回
        jobs: dict = {}
        for code, it in self.items.items():
            t = self.thresholds.get(code) or {}
            for side in ("up", "down"):
                s = t.get(side) or {}
                if s.get("mode") == "ma" and s.get("kind"):
                    jobs.setdefault(s["kind"], []).append(code)
        self._prefetch(jobs)

        for code, it in self.items.items():
            live = it["price"]
            for side in ("up", "down"):
                s = self.thr(code).get(side) or {}
                val, label, off_label = self.thr_value(code, side, live)
                it[side] = {
                    "mode": s.get("mode"), "kind": s.get("kind"),
                    "ma": s.get("ma"), "offPct": s.get("offPct"),
                    "offDir": s.get("offDir"), "manual": s.get("manual"),
                    "locked": bool(s.get("locked")),
                    "value": val, "label": label, "offsetLabel": off_label,
                    "armed": bool(s.get("locked") and val is not None),
                }

    # ---------------- 预警（1 分钟K线穿越 + 只发邮件） ---------------- #
    def _check_alerts(self):
        """核心预警判定。

        需求 8：交易时段内用 **1 分钟K线的穿越** 做判定 ——
            上穿：上一根 1分钟K线收盘 ≤ 阈值 且 当前根 > 阈值
            下穿：上一根 1分钟K线收盘 ≥ 阈值 且 当前根 < 阈值
        同一根 1 分钟K线内只报一次，因此「价格长期停在阈值外」不会重复发信 —— 
        从机制上就不可能持续轰炸。

        需求 4：阈值必须处于「已锁定」状态才参与预警；解锁即静默。
        需求 6：非交易时段不预警。
        """
        now = time.time()
        active, fired, stale_hits, unlocked_hits = [], [], 0, 0

        # 预取所有「已锁定」品种的 1 分钟K线（一次批量）
        need_m1 = [c for c, it in self.items.items()
                   if any(it[s]["armed"] for s in ("up", "down"))]
        if need_m1:
            self._prefetch({"m1": need_m1})

        for code, it in self.items.items():
            up, down = it.get("up") or {}, it.get("down") or {}
            live = it["price"]
            for side, th in (("up", up), ("down", down)):
                if not th.get("value"):
                    continue
                armed = th.get("armed")
                if not armed:
                    if th.get("locked"):
                        pass
                    elif th.get("mode"):
                        unlocked_hits += 1
                    self._cross.pop((code, side), None)
                    continue
                thr = float(th["value"])

                # ---- 需求 6：非交易时段静默（不置位、不报）----
                if not it.get("mktOpen", True):
                    stale_hits += 1
                    active.append(self._alert_item(it, side, thr, "pending"))
                    continue

                bars = self.bars(code, "m1", limit=8)
                if len(bars) >= 2:
                    prev_c = float(bars[-2]["c"])
                    cur_c = float(bars[-1]["c"])
                    bar_ts = int(bars[-1]["t"])
                    note = "1分钟K线穿越"
                else:
                    prev_c = cur_c = live
                    bar_ts = int(now // 60)
                    note = "实时价穿越（无1分钟K线，降级）"

                crossed = False
                if side == "up" and prev_c <= thr < cur_c:
                    crossed = True
                elif side == "down" and prev_c >= thr > cur_c:
                    crossed = True

                # 持续位于阈值外 → 面板展示（不重复发信）
                if side == "up" and live >= thr:
                    active.append(self._alert_item(it, side, thr, "crossed" if crossed else "holding"))
                elif side == "down" and live <= thr:
                    active.append(self._alert_item(it, side, thr, "crossed" if crossed else "holding"))

                if not crossed:
                    continue
                if self._cross.get((code, side)) == bar_ts:
                    continue                      # 同一根 1 分钟K线内已报过 → 跳过
                self._cross[(code, side)] = bar_ts

                # 冷却
                k = "%s:%s" % (code, side)
                cd = int(self.panel("mailCooldown", 300) or 0)
                if now - self.last_mail.get(k, 0) < cd:
                    self.sup["cooldown"] += 1
                    continue

                item = self._alert_item(it, side, thr, "crossed")
                item["triggerNote"] = note
                item["barTime"] = time.strftime("%Y-%m-%d %H:%M",
                                                time.localtime(bar_ts / 1000.0)) \
                    if bar_ts > 1e11 else ""
                if self._first_scan and not self.panel("alertOnStart", False):
                    continue
                fired.append(item)

        if stale_hits:
            self.sup["closed"] += stale_hits
            # 节流：同样的静默提示最多 5 分钟记一条，避免日志被刷屏
            if now - getattr(self, "_last_closed_note", 0) > 300:
                self._last_closed_note = now
                self.note("非交易时段静默：%d 个已锁定阈值的品种被跳过（不预警）"
                          % stale_hits)
        if unlocked_hits:
            self.sup["unlocked"] += unlocked_hits
        if self._first_scan:
            self._first_scan = False
            self.note("已建立预警基线（启动首轮不补发存量触发）")

        seen, uniq = set(), []
        for a in active:
            k = "%s:%s" % (a["code"], a["state"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(a)
        with self.lock:
            self.alerts = sorted(uniq, key=lambda x: x.get("percent") or 0,
                                 reverse=True)
        if fired:
            self._send_mail(fired)

    def _alert_item(self, it, side, thr, phase):
        digits = int(it.get("digits", 2) or 2)
        delay = it.get("delayMs", -1)
        return {
            "code": it["code"], "name": it["name"], "exch": it["exch"],
            "tq": it.get("tq", "-"), "state": side,
            "side": "上阈" if side == "up" else "下阈",
            "price": it["price"], "limit": _r(thr, 6),
            "diff": _r(it["price"] - thr, 6),
            "percent": it.get("percent"), "pct": it.get("pct"),
            "H": it.get("H"), "L": it.get("L"), "M": it.get("M"),
            "volume": it.get("volume"), "oi": it.get("oi"),
            "digits": digits,
            "thrLabel": (it[side] or {}).get("label") or "-",
            "offsetLabel": (it[side] or {}).get("offsetLabel") or "-",
            "quoteTime": it.get("quoteTime") or "-",
            "delayLabel": self._delay_label(delay),
            "at": now_str(), "phase": phase,
            "triggerNote": "1分钟K线穿越",
        }

    @staticmethod
    def _delay_label(ms: int) -> str:
        if ms is None or ms < 0:
            return "未知"
        if ms < 1000:
            return "%d 毫秒" % ms
        if ms < 60000:
            return "%.1f 秒" % (ms / 1000.0)
        if ms < 3600000:
            return "%.1f 分钟" % (ms / 60000.0)
        return "%.1f 小时" % (ms / 3600000.0)

    def note(self, msg: str):
        self.notes.append("%s %s" % (time.strftime("%H:%M:%S"), msg))
        if len(self.notes) > 14:
            self.notes = self.notes[-14:]

    # ---------------- 发信 ---------------- #
    def _quota_left(self, now: float) -> int:
        self.mail_sent = [t for t in self.mail_sent if now - t < 86400]
        per_h = max(0, int(self.panel("mailMaxPerHour", 12) or 0))
        per_d = max(0, int(self.panel("mailMaxPerDay", 60) or 0))
        h = len([t for t in self.mail_sent if now - t < 3600])
        return max(0, min(per_h - h, per_d - len(self.mail_sent)))

    def _send_mail(self, fired: list):
        if not self.panel("mailOn", True):
            return
        now = time.time()
        left = self._quota_left(now)
        if left <= 0:
            self.sup["quota"] += 1
            self.note("已达发信上限（≤%s 封/时、≤%s 封/天），本轮 %d 条预警被抑制"
                      % (self.panel("mailMaxPerHour", 12),
                         self.panel("mailMaxPerDay", 60), len(fired)))
            return
        pick = fired[:left]
        for a in pick:
            self.last_mail["%s:%s" % (a["code"], a["state"])] = now
        try:
            cfg = self.config["mail"]
            html = MAIL.build_alert_html(pick, source="天勤加权（仓权）合约")
            subj = "【加权合约预警】%s %s" % (
                "、".join(a["name"] for a in pick[:3]),
                " / ".join(sorted({a["side"] for a in pick})))
            res = MAIL.send_mail(cfg, subj, html)
        except Exception as exc:                                # noqa: BLE001
            res = {"ok": False, "message": str(exc)}
        if res.get("ok"):
            self.mail_sent.append(now)
            with self.lock:
                self.fired = (pick + self.fired)[:40]
        self.mail_log.append("%s %s → %s%s" % (
            now_str(), "邮件已发出" if res.get("ok") else "邮件失败",
            "、".join(a["name"] for a in pick),
            "" if res.get("ok") else "（%s）" % res.get("message", "")))
        if len(self.mail_log) > 30:
            self.mail_log = self.mail_log[-30:]

    # ---------------- 输出 ---------------- #
    def guard(self) -> dict:
        now = time.time()
        per_h = int(self.panel("mailMaxPerHour", 12) or 0)
        per_d = int(self.panel("mailMaxPerDay", 60) or 0)
        h = len([t for t in self.mail_sent if now - t < 3600])
        return {
            "on": bool(self.panel("mailOn", True)),
            "tradingOnly": bool(self.panel("mailTradingOnly", True)),
            "cooldown": int(self.panel("mailCooldown", 300) or 0),
            "maxPerHour": per_h, "maxPerDay": per_d,
            "sentHour": h, "sentDay": len(self.mail_sent),
            "triggering": len(self.alerts),
            "suppressed": dict(self.sup),
            "candle": self.panel("alertCandle", "m1"),
        }

    def payload(self) -> dict:
        items = list(self.items.values())
        mp = float(self.panel("minPercent", 70))
        above = sorted([x for x in items if x["above"]],
                       key=lambda x: x["percent"], reverse=True)
        below = sorted([x for x in items if not x["above"]],
                       key=lambda x: x["percent"], reverse=True)
        for i, x in enumerate(above, 1):
            x["rank"] = i
            x["side"] = "above"
        for i, x in enumerate(below, 1):
            x["rank"] = i
            x["side"] = "below"
        st = self.src.status()
        return {
            "ready": self.ready,
            "source": "tianqin",
            "sourceLabel": self.src.label,
            "basisName": "加权（仓权）合约",
            "date": time.strftime("%Y-%m-%d"),
            "updated": now_str(),
            "elapsedMs": self.refresh_ms,
            "delayMs": self.delay_ms,
            "delayLabel": self._delay_label(self.delay_ms),
            "total": len(items),
            "skipped": [PT.BY_CODE[c]["name"] for c in PT.DEAD_CODES],
            "above": above, "below": below,
            "stats": {
                "above": len(above), "below": len(below),
                "up70": len([x for x in above if x["percent"] >= mp]),
                "down70": len([x for x in below if x["percent"] >= mp]),
            },
            "minPercent": mp,
            "thrKind": self.panel("thrKind", "m15"),
            "thrMA": int(self.panel("thrMA", 20) or 20),
            "thrOffset": float(self.panel("thrOffset", 0.2) or 0),
            "thrOffsetDir": self.panel("thrOffsetDir", "down"),
            "thrKinds": THR_KINDS, "maChoices": MA_CHOICES,
            "offsetChoices": OFFSET_CHOICES,
            "kindOrder": KIND_ORDER,
            "kindCn": TQ.KIND_CN,
            "alerts": self.alerts,
            "fired": self.fired[:20],
            "mailLog": self.mail_log[-12:],
            "notes": self.notes[-12:],
            "guard": self.guard(),
            "conn": st,
            "config": self.config,
            "baseMeta": dict(self.base_meta, overrideCount=len(self.base_override)),
            "error": self.error,
            "serverTime": now_str(),
        }

    # ---------------- SSE ---------------- #
    def add_sse(self, q):
        with self._sse_lock:
            self._sse.append(q)

    def del_sse(self, q):
        with self._sse_lock:
            try:
                self._sse.remove(q)
            except ValueError:
                pass

    def broadcast(self, obj):
        dead = []
        with self._sse_lock:
            for q in self._sse:
                try:
                    q.put_nowait(obj)
                except Exception:                               # noqa: BLE001
                    dead.append(q)
            for q in dead:
                try:
                    self._sse.remove(q)
                except ValueError:
                    pass

    # ---------------- 后台循环 ---------------- #
    def loop(self, interval: float = 1.0):
        try:
            if not self._base_done:
                self.build_baselines()
        except Exception:                                       # noqa: BLE001
            traceback.print_exc()
        while not self._stop:
            t0 = time.time()
            try:
                self.refresh()
                self.broadcast({"type": "data", "data": self.payload()})
            except Exception:                                   # noqa: BLE001
                traceback.print_exc()
                time.sleep(2)
            time.sleep(max(0.3, interval - (time.time() - t0)))

    def start(self):
        threading.Thread(target=self.loop, daemon=True, name="loop").start()

    def reconnect(self, user: str, password: str):
        try:
            self.src.stop()
        except Exception:                                       # noqa: BLE001
            pass
        self.src = TQ.TianqinSource(user, password)
        self.error = ""
        self._first_scan = True
        self._cross.clear()
        with self._bl_lock:
            self._bars.clear()
        threading.Thread(target=self._warm, daemon=True).start()
        return True

    def _warm(self):
        try:
            self.refresh()
        except Exception:                                       # noqa: BLE001
            traceback.print_exc()


APP: App = None                                             # noqa: N816


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _decode_path(raw: str) -> str:
    """把请求行里的路径还原成可读字符串（支持中文目录名）。

    BaseHTTPRequestHandler 用 iso-8859-1 解码请求行，浏览器一般会做百分号编码，
    但个别客户端会直接发 UTF-8 原始字节，这里两种情况都兼容。
    """
    try:
        p = urllib.parse.unquote(raw)
    except Exception:                                           # noqa: BLE001
        p = raw
    if any("\x80" <= ch <= "\xff" for ch in p):
        try:
            p = p.encode("latin-1").decode("utf-8")
        except Exception:                                       # noqa: BLE001
            pass
    return p


class Handler(BaseHTTPRequestHandler):
    server_version = "WeightedPanel/3.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body: bytes, ctype: str, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:                                       # noqa: BLE001
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:                                       # noqa: BLE001
            return {}

    def _file(self, rel: str, ctype: str):
        p = os.path.join(STATIC_DIR, rel)
        try:
            with open(p, "rb") as fh:
                self._send(200, fh.read(), ctype)
        except Exception:                                       # noqa: BLE001
            self._send(404, b"not found", "text/plain; charset=utf-8")

    # ---------------- GET ---------------- #
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = _decode_path(parsed.path)               # 中文路径（/行情、/静态/…）需解码
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                return self._file("看板.html", "text/html; charset=utf-8")
            if path in ("/行情", "/hangqing", "/kline"):
                return self._file("行情.html", "text/html; charset=utf-8")
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if path.startswith("/静态/"):
                rel = urllib.parse.unquote(path[len("/静态/"):])
                ext = os.path.splitext(rel)[1].lower()
                ctype = {".js": "application/javascript; charset=utf-8",
                         ".css": "text/css; charset=utf-8",
                         ".html": "text/html; charset=utf-8",
                         ".png": "image/png", ".svg": "image/svg+xml",
                         ".woff2": "font/woff2"}.get(ext,
                                                     "application/octet-stream")
                return self._file(rel, ctype)
            if path == "/api/stream":
                return self._sse()
            if path == "/api/data":
                return self._json({"ok": True, "data": APP.payload()})
            if path == "/api/status":
                return self._json({"ok": True, "started": APP.started,
                                   "ready": APP.ready, "error": APP.error,
                                   "conn": APP.src.status(),
                                   "updated": now_str()})
            if path == "/api/config":
                return self._json({"ok": True, "config": APP.config})
            if path == "/api/thresholds":
                return self._json({"ok": True, "thresholds": APP.thresholds})
            if path == "/api/base/export":
                obj = APP.base_export()
                name = "基准HLM_%s.json" % time.strftime("%Y%m%d_%H%M")
                body = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
                return self._send(200, body, "application/json; charset=utf-8", {
                    "Content-Disposition":
                        "attachment; filename=\"baseline_%s.json\"; filename*=UTF-8''%s"
                        % (time.strftime("%Y%m%d_%H%M"),
                           urllib.parse.quote(name)),
                })
            if path == "/api/base":
                return self._json({"ok": True, "meta": APP.base_meta,
                                   "count": len(APP.base_override)})
            if path == "/api/contracts":
                code = (qs.get("code") or [""])[0]
                if code not in PT.BY_CODE:
                    return self._json({"ok": False, "message": "未知品种"}, 404)
                return self._json(APP.src.contracts(code))

            if path == "/api/kline":
                code = (qs.get("code") or qs.get("key") or [""])[0]
                kind = (qs.get("kind") or ["d"])[0]
                limit = (qs.get("limit") or [None])[0]
                sym = (qs.get("sym") or [""])[0]
                if code not in PT.BY_CODE:
                    return self._json({"ok": False, "message": "未知品种"}, 404)
                if kind not in KIND_ORDER:
                    return self._json({"ok": False, "message": "未知周期"}, 400)
                # 只允许本品种的合约（加权 或 真实月份合约），避免传入任意代码
                if sym and not App._sym_allowed(code, sym):
                    return self._json({"ok": False, "message": "合约不属于该品种"},
                                      400)
                d = APP.src.kline(code, kind, int(limit) if limit else None,
                                  sym=(sym or None))
                v = PT.BY_CODE[code]
                # 指定真实合约时，头部行情用该合约自己的报价
                q = (APP.contract_quote(code, sym) if sym else {}) or \
                    (APP.contract_quote(code, "") or {}) or {}
                d.update({
                    "name": v["name"], "exch": v["exch"], "tq": v["tq"],
                    "sym": d.get("sym") or v["tq"] or "",
                    "isWeighted": bool(d.get("isWeighted", not sym)),
                    "symName": q.get("name") or v["name"],
                    "digits": q.get("digits") or
                              (APP.quotes.get(code) or {}).get("digits", 2),
                    "base": ((lambda b: b if b.get("H") else
                              {"H": v["H"], "L": v["L"], "M": v["M"]})(
                                  APP.base_of(code))),
                    "quote": {"last": q.get("last"), "pct": q.get("pct"),
                              "change": q.get("change"), "high": q.get("high"),
                              "low": q.get("low"), "open": q.get("open"),
                              "presettle": q.get("presettle"),
                              "volume": q.get("volume"), "oi": q.get("oi"),
                              "ts": (q.get("ts") or "")[:19]},
                    "kinds": KIND_ORDER, "kindCn": TQ.KIND_CN,
                })
                return self._json({"ok": True, "data": d})
            return self._json({"ok": False, "message": "not found"}, 404)
        except Exception as exc:                                # noqa: BLE001
            traceback.print_exc()
            return self._json({"ok": False, "message": str(exc)}, 500)

    def _sse(self):
        import queue as _q
        q: _q.Queue = _q.Queue(maxsize=8)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        APP.add_sse(q)
        try:
            first = "data: %s\n\n" % json.dumps(
                {"type": "data", "data": APP.payload()}, ensure_ascii=False)
            self.wfile.write(first.encode("utf-8"))
            self.wfile.flush()
            last_beat = time.time()
            while True:
                try:
                    obj = q.get(timeout=2.0)
                    msg = "data: %s\n\n" % json.dumps(obj, ensure_ascii=False)
                except _q.Empty:
                    if time.time() - last_beat > 15:
                        msg = ": ping\n\n"
                        last_beat = time.time()
                    else:
                        continue
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
        except Exception:                                       # noqa: BLE001
            pass
        finally:
            APP.del_sse(q)

    # ---------------- POST ---------------- #
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self._body()
        try:
            if path == "/api/config":
                patch = body.get("config") or {}
                old_acc = (APP.config["tianqin"].get("user"),
                           APP.config["tianqin"].get("password"))
                APP.config = deep_merge(APP.config, patch)
                APP.save_config()
                new_acc = (APP.config["tianqin"].get("user"),
                           APP.config["tianqin"].get("password"))
                reconnected = False
                if new_acc != old_acc and all(new_acc):
                    APP.reconnect(*new_acc)
                    reconnected = True
                else:
                    APP.refresh()
                return self._json({"ok": True, "config": APP.config,
                                   "reconnected": reconnected})

            if path == "/api/base/import":
                res = APP.base_import(body)
                return self._json(res, 200 if res.get("ok") else 400)

            if path == "/api/base/reset":
                return self._json(APP.base_reset())

            if path == "/api/threshold":
                code = body.get("code")
                side = body.get("side")
                if code not in PT.BY_CODE or side not in ("up", "down"):
                    return self._json({"ok": False, "message": "参数错误"}, 400)
                t = APP.thr(code, create=True)
                s = t[side]
                act = body.get("action") or "set"
                if act == "clear":
                    t[side] = App._blank_side()
                elif act == "lock":
                    s["locked"] = bool(body.get("locked"))
                else:
                    for k in ("mode", "kind", "ma", "offPct", "offDir", "manual"):
                        if k in body:
                            s[k] = body[k]
                    if s.get("mode") == "ma" and s.get("ma") is not None:
                        s["ma"] = int(s["ma"])
                    if s.get("mode") == "manual" and body.get("manual") is not None:
                        s["manual"] = float(body["manual"])
                    if "locked" in body:
                        s["locked"] = bool(body["locked"])
                if all(not (t[x] or {}).get("mode") for x in ("up", "down")):
                    APP.thresholds.pop(code, None)      # 两侧全空则删掉条目
                APP.save_thresholds()
                APP.refresh()
                return self._json({"ok": True, "threshold": APP.thr(code),
                                   "item": APP.items.get(code)})

            if path == "/api/mail/test":
                cfg = deep_merge(APP.config["mail"], body.get("config") or {})
                html = MAIL.build_test_html(cfg)
                res = MAIL.send_mail(cfg, "【加权合约监控】测试邮件", html)
                return self._json({"ok": bool(res.get("ok")), "result": res})

            if path == "/api/reconnect":
                APP.reconnect(APP.config["tianqin"].get("user", ""),
                              APP.config["tianqin"].get("password", ""))
                return self._json({"ok": True})

            if path == "/api/refresh":
                APP.refresh()
                APP.broadcast({"type": "data", "data": APP.payload()})
                return self._json({"ok": True, "updated": now_str()})

            return self._json({"ok": False, "message": "not found"}, 404)
        except Exception as exc:                                # noqa: BLE001
            traceback.print_exc()
            return self._json({"ok": False, "message": str(exc)}, 500)


# --------------------------------------------------------------------------- #
def port_in_use(port: int) -> bool:
    s = socket.socket()
    try:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def pick_port(start: int, tries: int = 30) -> int:
    for p in range(start, start + tries):
        if not port_in_use(p):
            return p
    return start


def main() -> int:
    global APP                                             # noqa: PLW0603
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8962)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    print("=" * 70)
    print(" 加权（仓权）合约监控面板 · 天勤 TqSdk 版")
    print("=" * 70)

    APP = App()
    port = pick_port(args.port)
    url = "http://127.0.0.1:%d/" % port
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True

    print(" 数据源：%s" % APP.src.label)
    print(" 天勤账号：%s" % (APP.config["tianqin"].get("user") or "（未配置）"))
    print(" 预警邮件：%s → %s" % (APP.config["mail"].get("user") or "-",
                                  "、".join(APP.config["mail"].get("to") or []) or "-"))
    APP.start()
    print(" 看板地址：%s" % url)
    print(" 按 Ctrl+C 退出")
    print("=" * 70)

    if not args.no_browser:
        def open_later():
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:                                   # noqa: BLE001
                pass
        threading.Thread(target=open_later, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        APP._stop = True
        try:
            APP.src.stop()
        except Exception:                                       # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
