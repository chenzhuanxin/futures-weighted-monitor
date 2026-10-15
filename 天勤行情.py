# -*- coding: utf-8 -*-
"""
天勤（TqSdk）· 期货**加权（仓权）合约**行情数据层
=====================================================

统一使用天勤官方「指数合约」``KQ.i@交易所.品种`` —— 官方口径即**按持仓量加权**，
也就是需求中的「加权（仓权）合约」。**不再使用主连合约（KQ.m@）**。

支持的周期（全部走同一个数据层）
    timeshare 分时 · m1 1分钟 · m5 5分钟 · m15 15分钟 · m30 30分钟 ·
    h1 60分钟 · h2 120分钟 · d 日K

架构（tqsdk 的单线程约束）
    TqApi 的所有调用必须发生在创建它的线程里，因此这里起一个常驻子线程
    （TqActor）持有 TqApi 并循环 wait_update()，外部线程通过队列投递任务、
    用 Future 取回结果，从而在 HTTP 服务里安全使用。
"""

from __future__ import annotations

import collections
import concurrent.futures as _cf
import logging
import os
import queue
import threading
import time

import 品种表 as PT

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 周期 → 天勤秒数
KIND_SEC = {
    "m1": 60, "m5": 300, "m15": 900, "m30": 1800,
    "h1": 3600, "h2": 7200, "d": 86400,
}
KIND_CN = {
    "timeshare": "分时", "m1": "1分", "m5": "5分", "m15": "15分",
    "m30": "30分", "h1": "60分", "h2": "120分", "d": "日K",
}
# 图表页签顺序（需求 3）
KIND_ORDER = ["timeshare", "m1", "m5", "m15", "m30", "h1", "h2", "d"]
# 可一键设阈的周期（需求 4）
THR_KINDS = ["m5", "m15", "m30", "h1", "h2", "d"]
# 可选的均线周期（需求 4）
MA_CHOICES = [5, 10, 20, 30, 50, 80]
# 偏移量选项（需求 4）
OFFSET_CHOICES = [0.1, 0.2, 0.3, 0.5, 0.8, 1.0]

# K线申请根数
MAX_BARS = {"d": 3600, "h2": 4000, "h1": 4000, "m30": 4000,
            "m15": 4000, "m5": 4000, "m1": 2000}


def _silence_tqsdk():
    """彻底压制 tqsdk 的 INFO 级日志（连接通知、模拟账户资金/成交记录等）。"""
    try:
        logging.getLogger().setLevel(logging.WARNING)
        names = ["tqsdk", "tqsdk.api", "tqsdk.utils", "tqsdk.connect",
                 "tqsdk.baseApi", "tqsdk.tqwebhelper", "tqsdk.data_extension",
                 "tqsdk.tradeable", "tqsdk.objs", "tqsdk.channel",
                 "tqsdk.sim", "tqsdk.lib", "tqsdk.ta", "tqsdk.tafunc"]
        for name in names:
            lg = logging.getLogger(name)
            lg.setLevel(logging.WARNING)
            lg.propagate = False
            lg.handlers = [logging.NullHandler()]
        logging.raiseExceptions = False
    except Exception:                                               # noqa: BLE001
        pass


def _mute_all_loggers():
    """把「所有已存在的 logger」压到 WARNING，并清掉它们自带的 handler。

    tqsdk 在 TqApi.__init__ 里会调用 logging.basicConfig() 给自己挂上
    StreamHandler，所以必须在连接建立之后再压一次，否则仍会打印连接通知。
    """
    try:
        for name in list(logging.root.manager.loggerDict.keys()):
            lg = logging.getLogger(name)
            lg.setLevel(logging.WARNING)
            lg.propagate = False
            lg.handlers = [logging.NullHandler()]
        logging.getLogger().setLevel(logging.WARNING)
        for h in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(h)
    except Exception:                                               # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Actor 线程
# --------------------------------------------------------------------------- #
class TqActor(threading.Thread):
    """持有 TqApi 的常驻线程；外部通过 call() 投递任务。"""

    def __init__(self, user: str, password: str):
        super().__init__(daemon=True, name="TqActor")
        self.user = user
        self.password = password
        self.q: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.api = None
        self.err = ""
        self.alive = True
        self.started_at = 0.0
        self.last_update = 0.0
        self.subs: dict = {}                     # (sym, sec) -> DataFrame
        self._lock = threading.Lock()

    # ---------------- 线程主体 ---------------- #
    def run(self):
        _silence_tqsdk()
        try:
            from tqsdk import TqApi, TqAuth
        except Exception as exc:                                    # noqa: BLE001
            self.err = "未安装 tqsdk：%s" % exc
            self.ready.set()
            return
        try:
            self.api = TqApi(auth=TqAuth(self.user, self.password))
        except Exception as exc:                                    # noqa: BLE001
            self.err = "天勤登录失败：%s" % str(exc)[:200]
            self.ready.set()
            return
        self.started_at = time.time()
        _mute_all_loggers()
        self.ready.set()

        idle_since = time.time()
        while self.alive:
            # 1) 优先处理外部任务
            try:
                fn, args, fut = self.q.get_nowait()
            except queue.Empty:
                fn = None
            if fn is not None:
                if fn is _STOP:
                    break
                try:
                    fut.set_result(fn(self.api, *args))
                    self.last_update = time.time()
                except Exception as exc:                            # noqa: BLE001
                    fut.set_exception(exc)
                idle_since = time.time()
                continue
            # 2) 空闲时推进 wait_update（保持连接与数据新鲜）
            try:
                self.api.wait_update(deadline=time.time() + 0.05)
                self.last_update = time.time()
            except Exception as exc:                                # noqa: BLE001
                if "closed" in str(exc).lower() or "shutdown" in str(exc).lower():
                    self.err = "天勤连接已关闭"
                    break
                if time.time() - idle_since > 60:
                    self.err = str(exc)[:200]
                    idle_since = time.time()
        try:
            if self.api:
                self.api.close()
        except Exception:                                           # noqa: BLE001
            pass
        self.alive = False

    # ---------------- 外部调用入口 ---------------- #
    def call(self, fn, *args, timeout: float = 30.0):
        if not self.alive or self.err:
            raise RuntimeError(self.err or "天勤连接不可用")
        fut: _cf.Future = _cf.Future()
        self.q.put((fn, args, fut))
        return fut.result(timeout=timeout)

    def stop(self):
        self.alive = False
        try:
            self.q.put((_STOP, (), _cf.Future()))
        except Exception:                                           # noqa: BLE001
            pass


def _STOP(*_a):                                                     # noqa: ANN001
    return None


# --------------------------------------------------------------------------- #
# 在 actor 线程内执行的具体任务
# --------------------------------------------------------------------------- #
def _f(v, d=0.0):
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return d
        return f
    except (TypeError, ValueError):
        return d


def _quote_batch(api, syms):
    """批量订阅并返回行情快照。"""
    qs = api.get_quote_list(list(syms))
    for _ in range(60):
        if all(q.instrument_name for q in qs):
            break
        api.wait_update(deadline=time.time() + 0.25)
    out = []
    for q in qs:
        last = _f(q.last_price)
        presettle = _f(q.pre_settlement) or _f(q.pre_close)
        change = last - presettle if presettle else 0.0
        pct = (change / presettle * 100.0) if presettle else 0.0
        out.append({
            "symbol": q.instrument_id,
            "name": q.instrument_name,
            "last": last,
            "open": _f(q.open),
            "high": _f(q.highest) or last,
            "low": _f(q.lowest) or last,
            "preclose": _f(q.pre_close),
            "presettle": presettle,
            "change": change,
            "pct": pct,
            "volume": int(_f(q.volume)),
            "oi": int(_f(q.open_interest)),
            "oiChange": int(_f(q.open_interest) - _f(q.pre_open_interest)),
            "digits": int(_f(getattr(q, "price_decs", 2), 2)),
            "tick": _f(getattr(q, "price_tick", 0.0)),
            "datetime": q.datetime or "",
            "expired": bool(getattr(q, "expired", False)),
        })
    return out


def _kline(api, subs, sym, sec, n):
    """取 K 线（订阅式，同一 (sym,sec) 复用同一 DataFrame）。"""
    key = (sym, sec)
    kd = subs.get(key)
    if kd is None:
        kd = api.get_kline_serial(sym, sec, data_length=n)
        subs[key] = kd
    want = min(int(n), len(kd)) if len(kd) else int(n)
    for _ in range(120):
        if len(kd) > 0 and _f(kd.iloc[-1]["close"]) > 0 and \
                int((kd["close"] > 0).sum()) >= min(want, 50):
            break
        api.wait_update(deadline=time.time() + 0.25)
    return kd


def _kline_many(api, subs, jobs):
    """批量订阅同一周期的一批合约，一次 wait_update 统一填充，避免逐品种排队。"""
    arrs = []
    for sym, sec, n in jobs:
        key = (sym, sec)
        kd = subs.get(key)
        if kd is None:
            kd = api.get_kline_serial(sym, sec, data_length=n)
            subs[key] = kd
        arrs.append(kd)
    for _ in range(160):
        done = 0
        for kd, (_, _, n) in zip(arrs, jobs):
            if len(kd) > 0 and _f(kd.iloc[-1]["close"]) > 0 and \
                    int((kd["close"] > 0).sum()) >= min(int(n), 60):
                done += 1
        if done >= len(arrs):
            break
        api.wait_update(deadline=time.time() + 0.25)
    return arrs


def _bars_from_df(kd) -> list:
    """DataFrame → [{t(ms),day,o,h,l,c,v,oi}]，按时间升序。"""
    if kd is None or len(kd) == 0:
        return []
    try:
        dt = kd["datetime"].to_numpy()
        op = kd["open"].to_numpy()
        hi = kd["high"].to_numpy()
        lo = kd["low"].to_numpy()
        cl = kd["close"].to_numpy()
        vo = kd["volume"].to_numpy()
        oi = kd["close_oi"].to_numpy() if "close_oi" in kd.columns else None
    except Exception:                                               # noqa: BLE001
        return []
    out = []
    for i in range(len(cl)):
        c = _f(cl[i])
        o = _f(op[i])
        if c <= 0 and o <= 0:
            continue
        nsv = dt[i]
        ms = int(nsv // 1_000_000) if (nsv and nsv == nsv) else 0
        lt = time.localtime(ms / 1000.0) if ms else None
        out.append({
            "t": ms,
            "day": time.strftime("%Y/%m/%d", lt) if lt else "",
            "tm": time.strftime("%H:%M", lt) if lt else "",
            "o": o, "h": _f(hi[i]), "l": _f(lo[i]), "c": c,
            "v": _f(vo[i]),
            "oi": int(_f(oi[i])) if oi is not None else 0,
        })
    out.sort(key=lambda b: b["t"])
    return out


# --------------------------------------------------------------------------- #
# 对外数据源
# --------------------------------------------------------------------------- #
class TianqinSource:
    id = "tianqin"
    label = "天勤 TqSdk · 加权（仓权）合约 KQ.i@"

    def __init__(self, user: str, password: str):
        self.user = (user or "").strip()
        self.password = password or ""
        self.actor: TqActor | None = None
        self._start_lock = threading.Lock()
        self._quote_keys: set = set()
        self.err = ""

    # ---------------- 生命周期 ---------------- #
    @property
    def ok(self) -> bool:
        return bool(self.actor and self.actor.alive and not self.actor.err)

    def start(self) -> bool:
        with self._start_lock:
            if self.ok:
                return True
            if not self.user or not self.password:
                self.err = "未配置天勤（快期）账号或密码"
                return False
            try:
                import tqsdk                                          # noqa: F401
            except Exception as exc:                                # noqa: BLE001
                import sys as _sys
                self.err = (
                    "当前 Python 未安装天勤 tqsdk（%s: %s）。"
                    "当前解释器：%s —— 请执行：\"%s\" -m pip install tqsdk"
                    % (type(exc).__name__, exc, _sys.executable, _sys.executable))
                return False
            if self.actor and self.actor.alive:
                return True
            self.err = ""
            self.actor = TqActor(self.user, self.password)
            self.actor.start()
            if not self.actor.ready.wait(45):
                self.err = "天勤连接超时"
                return False
            if self.actor.err:
                self.err = self.actor.err
                return False
            return True

    def stop(self):
        if self.actor:
            self.actor.stop()
            self.actor = None

    def status(self) -> dict:
        a = self.actor
        return {
            "ok": self.ok,
            "err": self.err or (a.err if a else ""),
            "user": self.user,
            "connectedAt": (time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(a.started_at))
                            if a and a.started_at else ""),
            "subs": len(a.subs) if a else 0,
        }

    # ---------------- 实时行情 ---------------- #
    def quotes(self, codes=None) -> dict:
        if not self.start():
            return {}
        codes = [c for c in (codes or PT.codes_live()) if PT.TQ.get(c)]
        out = {}
        try:
            syms = [PT.TQ[c] for c in codes]
            res = self.actor.call(_quote_batch, syms, timeout=45)
            self._quote_keys |= set(syms)
        except Exception as exc:                                    # noqa: BLE001
            self.err = str(exc)[:200]
            return {}
        for c, q in zip(codes, res):
            if not q.get("name") or q.get("expired"):
                continue
            out[c] = {
                "code": c,
                "name": PT.NAME.get(c, ""),
                "showName": (q.get("name") or "").replace("指数", "") + "加权",
                "digits": q.get("digits", 2),
                "last": q.get("last") or 0.0,
                "open": q.get("open") or 0.0,
                "high": q.get("high") or 0.0,
                "low": q.get("low") or 0.0,
                "presettle": q.get("presettle") or 0.0,
                "prevclose": q.get("preclose") or q.get("presettle") or 0.0,
                "change": q.get("change") or 0.0,
                "pct": q.get("pct") or 0.0,
                "volume": q.get("volume") or 0,
                "oi": q.get("oi") or 0,
                "oiChange": q.get("oiChange") or 0,
                "ts": q.get("datetime") or "",
            }
        return out

    # ---------------- K 线 ---------------- #
    def kline(self, code: str, kind: str, limit: int | None = None) -> dict:
        if kind == "timeshare":
            return self.timeshare(code)
        sym = PT.TQ.get(code)
        sec = KIND_SEC.get(kind)
        if not sym or not sec:
            return {"kind": kind, "code": code, "bars": [], "count": 0}
        if not self.start():
            return {"kind": kind, "code": code, "bars": [], "count": 0,
                    "error": self.err}
        n = int(limit or MAX_BARS.get(kind, 3000))
        try:
            kd = self.actor.call(_kline, self.actor.subs, sym, sec, n, timeout=90)
            bars = _bars_from_df(kd)
        except Exception as exc:                                    # noqa: BLE001
            self.err = str(exc)[:200]
            return {"kind": kind, "code": code, "bars": [], "count": 0,
                    "error": self.err}
        return {"kind": kind, "code": code, "name": PT.NAME.get(code, ""),
                "bars": bars, "count": len(bars), "kindCn": KIND_CN.get(kind, kind)}

    def dayk(self, code: str) -> list:
        return self.kline(code, "d")["bars"]

    def bars_many(self, codes, kind: str = "d", limit: int | None = None) -> dict:
        """批量取同一周期的 K 线 → {code: bars}。"""
        if not self.start():
            return {}
        sec = KIND_SEC.get(kind)
        if not sec:
            return {}
        n = int(limit or MAX_BARS.get(kind, 3000))
        jobs, cs = [], []
        for c in codes:
            sym = PT.TQ.get(c)
            if sym:
                jobs.append((sym, sec, n))
                cs.append(c)
        if not jobs:
            return {}
        try:
            arrs = self.actor.call(_kline_many, self.actor.subs, jobs, timeout=300)
        except Exception as exc:                                    # noqa: BLE001
            self.err = str(exc)[:200]
            return {}
        return {c: _bars_from_df(a) for c, a in zip(cs, arrs)}

    # ---------------- 分时 ---------------- #
    def timeshare(self, code: str) -> dict:
        sym = PT.TQ.get(code)
        if not sym:
            return {"kind": "timeshare", "code": code, "bars": [], "count": 0}
        if not self.start():
            return {"kind": "timeshare", "code": code, "bars": [], "count": 0,
                    "error": self.err}
        try:
            kd = self.actor.call(_kline, self.actor.subs, sym, 60, 1200, timeout=90)
            allbars = _bars_from_df(kd)
        except Exception as exc:                                    # noqa: BLE001
            self.err = str(exc)[:200]
            return {"kind": "timeshare", "code": code, "bars": [], "count": 0,
                    "error": self.err}
        bars = _tail_session(allbars)
        cum_v = 0.0
        cum_pv = 0.0
        out = []
        for b in bars:
            v = b["v"] or 0.0
            cum_v += v
            cum_pv += (b["c"] or 0.0) * v
            out.append({"time": time.strftime("%m-%d %H:%M",
                                              time.localtime(b["t"] / 1000.0)),
                        "t": b["t"], "price": b["c"],
                        "avg": round(cum_pv / cum_v, 6) if cum_v > 0 else b["c"],
                        "v": v})
        return {"kind": "timeshare", "code": code, "name": PT.NAME.get(code, ""),
                "bars": out, "count": len(out), "kindCn": "分时"}


def _tail_session(bars: list, max_gap_min: int = 12) -> list:
    """从尾部截取最近一个连续交易时段。"""
    if not bars:
        return []
    out = [bars[-1]]
    for b in reversed(bars[:-1]):
        gap = (out[-1]["t"] - b["t"]) / 60000.0
        if gap > max_gap_min:
            break
        out.append(b)
    out.reverse()
    return out


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("用法：python 天勤行情.py <快期账号> <密码>")
        sys.exit(1)
    src = TianqinSource(sys.argv[1], sys.argv[2])
    t0 = time.time()
    print("启动天勤连接 ...")
    print("  available:", src.start(), src.err, "%.1fs" % (time.time() - t0))
    qs = src.quotes()
    print("实时行情: %d 个品种" % len(qs))
    for c in list(qs)[:6]:
        q = qs[c]
        print("  %-6s %-12s 最新 %-10s 涨跌 %+.2f%%"
              % (c, q["name"], q["last"], q["pct"]))
    for kind in KIND_ORDER:
        t0 = time.time()
        r = src.kline("CU", kind)
        b = r["bars"]
        print("  %-9s 条数 %-5d 用时 %.2fs  末 %s"
              % (kind, len(b), time.time() - t0,
                 time.strftime("%Y-%m-%d %H:%M",
                               time.localtime(b[-1]["t"] / 1000)) if b else "-"))
    src.stop()
