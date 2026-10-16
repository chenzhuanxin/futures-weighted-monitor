# -*- coding: utf-8 -*-
r"""
环境自检 / 自愈 —— 保证「当前正在运行的这个进程」一定能用天勤 tqsdk。

为什么需要它
------------
本机往往装了多个 Python（系统版、python.org 版、WorkBuddy 托管版、Store 版…）。
常见坑：用 A 装了 tqsdk，却用 B 启动了服务端 → 报「当前 Python 未安装天勤 tqsdk」。
而且 bat / 快捷方式 / IDE / 命令行……入口太多，光靠启动脚本挑解释器容易漏。

所以把自愈下沉到服务端本身：只要 `服务端.py` 一启动，就先做这件事：

  1. 当前解释器能 import tqsdk                → 什么也不做，直接往下跑
  2. 本机别的解释器能 import tqsdk            → 用那个解释器把自己整个重启（无缝接管，
                                                端口/参数/工作目录全部保留）
  3. 一个都没有                              → 用当前解释器联网安装（多镜像依次重试）

对外只用 `ensure()`。返回 True = 可以继续；返回 False = 真的没辙了，用 `fail_hint()`
拿到给用户看的「可直接复制」的命令。

此模块只用标准库，且 **不 import tqsdk**，所以在任何解释器里都能安全导入。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# 自愈标记：重启一次后置上，避免「A 让我换 B、B 又让我换 A」的死循环
MARK = "TQ_SELFHEALED"
# 记录自愈后的解释器，方便前端与日志展示
PY_ENV = "TQ_PY_EXE"

# 安装 tqsdk 的镜像顺序（清华源没有 tqsdk 包，放最后兜底）
MIRRORS = [
    ("阿里", "https://mirrors.aliyun.com/pypi/simple/", "mirrors.aliyun.com"),
    ("腾讯", "https://mirrors.cloud.tencent.com/pypi/simple/", "mirrors.cloud.tencent.com"),
    ("豆瓣", "https://pypi.doubanio.com/simple/", "pypi.doubanio.com"),
    ("官方", "https://pypi.org/simple/", "pypi.org"),
    ("清华", "https://pypi.tuna.tsinghua.edu.cn/simple/", "pypi.tuna.tsinghua.edu.cn"),
]

NO_WIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# 当前解释器自己的 tqsdk 探测结果缓存（None=未知）
_here_ok = None


def _no_win() -> dict:
    return {"creationflags": NO_WIN} if os.name == "nt" else {}


def norm(p) -> str:
    try:
        return os.path.normcase(os.path.normpath(str(p)))
    except Exception:                                           # noqa: BLE001
        return str(p)


def candidates() -> list:
    """本机可能的 Python 解释器（去重、保持优先级顺序）。"""
    out = []

    def add(p):
        if not p:
            return
        try:
            s = str(Path(p).resolve())
        except Exception:                                       # noqa: BLE001
            s = str(p)
        if s not in out:
            out.append(s)

    add(os.environ.get(PY_ENV))                 # 0) 显式指定
    add(sys.executable)                         # 1) 当前
    for n in ("python", "python3", "py"):       # 2) PATH
        try:
            import shutil
            add(shutil.which(n))
        except Exception:                                       # noqa: BLE001
            pass
    localapp = os.environ.get("LOCALAPPDATA", "")
    home = os.path.expanduser("~")
    for v in ("314", "313", "312", "311", "310", "39", "38"):
        dotted = "3." + v[1:]                   # 314 -> 3.14 ; 39 -> 3.9
        add("C:\\Python%s\\python.exe" % v)
        add("C:/Python%s/python.exe" % v)
        add("C:\\Program Files\\Python%s\\python.exe" % v)
        if localapp:
            for name in ("Python" + v, "Python" + dotted):
                add(os.path.join(localapp, "Programs", "Python", name, "python.exe"))
            # python.org 3.14 的「每用户安装」布局
            add(os.path.join(localapp, "Python", "pythoncore-%s-64" % dotted, "python.exe"))
            add(os.path.join(localapp, "Python", "pythoncore-%s-arm64" % dotted, "python.exe"))
            add(os.path.join(localapp, "Python", "bin", "python.exe"))
        add(os.path.join(home, "AppData", "Local", "Programs", "Python",
                         "Python" + dotted, "python.exe"))
    wb = Path(home) / ".workbuddy" / "binaries" / "python"   # 4) WorkBuddy 托管
    try:
        for p in wb.glob("versions/*/python.exe"):
            add(p)
        for p in wb.glob("envs/*/Scripts/python.exe"):
            add(p)
    except Exception:                                           # noqa: BLE001
        pass
    try:                                        # 5) py launcher
        r = subprocess.run(["py", "-0p"], capture_output=True, text=True,
                           timeout=8, **_no_win())
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("-") and ":\\" in line:
                add(line.split(" ", 1)[-1].strip())
    except Exception:                                           # noqa: BLE001
        pass

    good = []
    for p in out:
        try:
            if p and Path(p).is_file():
                good.append(p)
        except Exception:                                       # noqa: BLE001
            pass
    return good


def python_ok(exe) -> bool:
    """能正常跑起来（排除 Windows Store 占位程序、损坏的安装）。"""
    try:
        r = subprocess.run([str(exe), "-c", "import sys;sys.exit(0)"],
                           capture_output=True, timeout=25, **_no_win())
        return r.returncode == 0
    except Exception:                                           # noqa: BLE001
        return False


def has_tqsdk(exe) -> bool:
    try:
        r = subprocess.run([str(exe), "-c", "import tqsdk"],
                           capture_output=True, timeout=60, **_no_win())
        return r.returncode == 0
    except Exception:                                           # noqa: BLE001
        return False


def here_has_tqsdk(refresh: bool = False) -> bool:
    """当前解释器有没有 tqsdk（结果缓存）。"""
    global _here_ok
    if _here_ok is None or refresh:
        try:
            __import__("tqsdk")
            _here_ok = True
        except Exception:                                       # noqa: BLE001
            _here_ok = False
    return bool(_here_ok)


def _penalty(exe: str) -> int:
    """越小越优先。py.exe / python3.exe 之类是「中继程序」，编码与句柄传递易出问题，
    尽量直接选真正的 python.exe。"""
    name = os.path.basename(str(exe)).lower()
    if name in ("py.exe", "python3.exe", "pythonw.exe"):
        return 2
    if name != "python.exe":
        return 1
    return 0


def find_other(log=None) -> str | None:
    """在「别的」解释器里找一个装了 tqsdk 的。并发探测，几秒内出结果。"""
    me = norm(sys.executable)
    cs = [c for c in candidates() if norm(c) != me]
    if not cs:
        return None
    from concurrent.futures import ThreadPoolExecutor
    try:
        with ThreadPoolExecutor(max_workers=min(8, len(cs))) as ex:
            pairs = list(ex.map(lambda e: (e, has_tqsdk(e)), cs))
    except Exception:                                           # noqa: BLE001
        pairs = [(e, has_tqsdk(e)) for e in cs]
    hits = [(i, e) for i, (e, ok) in enumerate(pairs) if ok]
    if log:
        for _, e in hits:
            log("  [√] 发现可用解释器 %s" % e)
    if not hits:
        return None
    # 先按「是否真 python.exe」再按原始优先级排序
    hits.sort(key=lambda t: (_penalty(t[1]), t[0]))
    return hits[0][1]


def try_install(exe, log=None) -> bool:
    """给指定解释器联网安装 tqsdk，多镜像依次重试。"""
    for name, url, host in MIRRORS:
        if log:
            log("  · 尝试镜像 %s（%s）…" % (name, url))
        cmd = [str(exe), "-m", "pip", "install", "tqsdk",
               "-i", url, "--trusted-host", host,
               "--timeout", "60", "--disable-pip-version-check"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=600, **_no_win())
            if r.returncode == 0:
                if log:
                    log("  · 安装完成（%s）" % name)
                return True
            blob = (r.stdout or "") + (r.stderr or "")
            tail = [x for x in blob.splitlines() if x.strip()][-1:] or [""]
            if log:
                log("  · 失败：%s" % tail[0][:140])
        except subprocess.TimeoutExpired:
            if log:
                log("  · 超时，换下一个镜像")
        except Exception as exc:                                # noqa: BLE001
            if log:
                log("  · 异常：%s" % exc)
    return False


def _restart_with(exe: str, server_path: str, argv: list, log=None) -> bool:
    """用另一个解释器重新启动自己。

    用子进程而不是 os.execv：Windows 下 execv 会丢句柄，`py.exe` 这类中继程序
    还可能把进程链断开（实测表现为「服务没起来、控制台也没输出」）。
    """
    os.environ[MARK] = "1"
    os.environ[PY_ENV] = exe
    cmd = [exe, "-u", server_path] + list(argv)
    if log:
        log("[自愈] 当前 Python 没有天勤 tqsdk，改用：%s" % exe)
        log("[自愈] 重新启动服务端…")
    try:
        # 不加 CREATE_NO_WINDOW：让子进程继承同一个控制台，
        # 日志看得见、Ctrl+C 也能正常停服务
        r = subprocess.run(cmd)
        sys.exit(int(r.returncode or 0))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:                                    # noqa: BLE001
        if log:
            log("[自愈] 无法改用其它解释器：%s" % exc)
        return False


def ensure(server_path: str | None = None, argv: list | None = None,
           auto: bool = True, log=print) -> bool:
    """确保当前进程能 import tqsdk。True = 可以继续跑。"""
    if here_has_tqsdk():
        if os.environ.get(PY_ENV) and log:
            log("[环境] 已按自愈结果使用解释器：%s" % sys.executable)
        return True
    if not auto:
        return False
    # 已经自愈过一轮还是不行 → 不再折腾，交给上层报错
    if os.environ.get(MARK) == "1":
        return False

    if log:
        log("[环境] 当前解释器缺少天勤 tqsdk：%s" % sys.executable)
        log("[环境] 正在本机查找已安装天勤的 Python…")
    other = find_other(log)
    if other:
        srv = server_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "服务端.py")
        return _restart_with(other, srv, argv if argv is not None else sys.argv[1:],
                             log)

    if log:
        log("[环境] 本机没有可用的天勤环境，尝试自动安装到当前解释器…")
    if try_install(sys.executable, log):
        import importlib
        importlib.invalidate_caches()
        if here_has_tqsdk(refresh=True):
            if log:
                log("[环境] 安装成功，继续启动")
            return True
    return False


def fail_hint() -> str:
    """给用户看的、可直接复制粘贴的修复命令。"""
    exe = sys.executable
    return (
        "\n" + "=" * 66 + "\n"
        "[无法自动修复] 天勤 tqsdk 未就绪。请手动执行下面这条命令：\n\n"
        '    "%s" -m pip install tqsdk -i https://mirrors.aliyun.com/pypi/simple/\n\n'
        "说明：必须用「启动面板时实际使用的那个」Python 来装（就是下面这个）：\n"
        "    %s\n"
        "装完重新双击 启动面板.bat 即可。\n"
        % (exe, exe) + "=" * 66 + "\n")


if __name__ == "__main__":
    print("当前解释器：%s" % sys.executable)
    print("当前有无 tqsdk：%s" % here_has_tqsdk(refresh=True))
    cs = candidates()
    print("扫描到 %d 个候选解释器：" % len(cs))
    for c in cs:
        print("   %s  tqsdk=%s" % ("[√]" if has_tqsdk(c) else "[ ]", c))
