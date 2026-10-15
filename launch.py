# -*- coding: utf-8 -*-
r"""
启动器 —— 自动挑选「装了天勤 tqsdk」的 Python 解释器来运行 服务端.py

背景：本机往往装了多个 Python。PATH 里排在前面的那个（例如 WorkBuddy 托管的
Python 3.13）未必装了 tqsdk，而系统 Python 3.14 装了。直接 `python 服务端.py`
就会报「天勤不可用」。本脚本会：

  1. 扫描本机所有可能的 Python 解释器
  2. 逐个试 `import tqsdk`，挑出能用的那个（优先本机已装的，不再联网）
  3. 如果一个都没有：用当前解释器联网安装（多镜像依次重试）
  4. 用选中的解释器启动 服务端.py，并把命令行参数透传

也可以直接用环境变量 PY_EXE 指定解释器：
    set PY_EXE=C:\Python314\python.exe
    python launch.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "服务端.py"

# 安装 tqsdk 时的镜像顺序（清华源没有 tqsdk，放最后）
MIRRORS = [
    ("阿里", "https://mirrors.aliyun.com/pypi/simple/", "mirrors.aliyun.com"),
    ("豆瓣", "https://pypi.doubanio.com/simple/", "pypi.doubanio.com"),
    ("官方", "https://pypi.org/simple/", "pypi.org"),
    ("腾讯", "https://mirrors.cloud.tencent.com/pypi/simple/", "mirrors.cloud.tencent.com"),
    ("清华", "https://pypi.tuna.tsinghua.edu.cn/simple/", "pypi.tuna.tsinghua.edu.cn"),
]

NO_WIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _flag_no_win():
    return {"creationflags": NO_WIN} if os.name == "nt" else {}


def candidates() -> list:
    """本机可能的 Python 解释器（去重、保持顺序）。"""
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

    # 0) 环境变量显式指定
    add(os.environ.get("PY_EXE"))
    # 1) 当前解释器
    add(sys.executable)
    # 2) PATH 里的
    for n in ("python", "python3", "py"):
        add(shutil.which(n))
    # 3) 常见安装路径（官方安装器给 Python314，Store/其它给 Python3.14）
    localapp = os.environ.get("LOCALAPPDATA", "")
    for v in ("314", "313", "312", "311", "310", "39", "38"):
        dotted = "3." + v[1:]           # 314 -> 3.14 ; 39 -> 3.9
        add("C:\\Python%s\\python.exe" % v)
        add("C:/Python%s/python.exe" % v)
        if localapp:
            for name in ("Python" + v, "Python" + dotted):
                add(os.path.join(localapp, "Programs", "Python", name, "python.exe"))
        # 3b) 直接把 Python 目录塞进过 PATH 的也算上
        add("C:\\Program Files\\Python%s\\python.exe" % v)
    # 4) WorkBuddy 托管解释器
    wb = Path(os.path.expanduser("~/.workbuddy/binaries/python"))
    for p in wb.glob("versions/*/python.exe"):
        add(p)
    for p in wb.glob("envs/*/Scripts/python.exe"):
        add(p)
    # 5) py launcher 列出的版本
    try:
        r = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=8,
                           **_flag_no_win())
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("-") and ":\\" in line:
                add(line.split(" ", 1)[-1].strip())
    except Exception:                                           # noqa: BLE001
        pass

    # 只保留真实存在且可执行的文件
    good = []
    for p in out:
        try:
            if p and Path(p).is_file():
                good.append(p)
        except Exception:                                       # noqa: BLE001
            pass
    return good


def has_tqsdk(exe: str) -> bool:
    try:
        r = subprocess.run([exe, "-c", "import tqsdk"],
                           capture_output=True, timeout=30, **_flag_no_win())
        return r.returncode == 0
    except Exception:                                           # noqa: BLE001
        return False


def python_ok(exe: str) -> bool:
    try:
        r = subprocess.run([exe, "-c", "import sys;sys.exit(0)"],
                           capture_output=True, timeout=20, **_flag_no_win())
        return r.returncode == 0
    except Exception:                                           # noqa: BLE001
        return False


def try_install(exe: str) -> bool:
    for name, url, host in MIRRORS:
        print("  · 尝试镜像 %s（%s）…" % (name, url))
        cmd = [exe, "-m", "pip", "install", "tqsdk", "-i", url,
               "--trusted-host", host, "--timeout", "60", "--disable-pip-version-check"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                               **_flag_no_win())
            if r.returncode == 0:
                print("  · 安装完成（%s）" % name)
                return True
            last = (r.stdout or "") + (r.stderr or "")
            tail = [x for x in last.splitlines() if x.strip()][-1:] or [""]
            print("  · 失败：%s" % tail[0][:140])
        except subprocess.TimeoutExpired:
            print("  · 超时，换下一个镜像")
        except Exception as exc:                                # noqa: BLE001
            print("  · 异常：%s" % exc)
    return False


def main() -> int:
    args = sys.argv[1:]
    print("=" * 62)
    print(" 加权（仓权）合约监控面板 · 启动器")
    print("=" * 62)

    cands = candidates()
    if not cands:
        print("[错误] 本机未找到任何 Python 解释器，请先安装 Python 3.9+ 并加入 PATH")
        return 1

    print("扫描到 %d 个候选解释器，检查 tqsdk…" % len(cands))
    usable, plain = [], []
    for exe in cands:
        if not python_ok(exe):
            continue
        plain.append(exe)
        if has_tqsdk(exe):
            usable.append(exe)
            print("  [√] %s" % exe)
            break                                    # 找到就停，避免逐个探测太慢

    chosen = usable[0] if usable else None
    if not chosen:
        print("\n[提示] 没有解释器装了 tqsdk，尝试自动安装…")
        target = plain[0] if plain else cands[0]
        print("  安装到：%s" % target)
        if try_install(target) and has_tqsdk(target):
            chosen = target
        else:
            # 装完没有，再扫一遍（可能别的解释器装成功了）
            for exe in cands:
                if python_ok(exe) and has_tqsdk(exe):
                    chosen = exe
                    break

    if not chosen:
        print("\n" + "=" * 62)
        print("[失败] 天勤 tqsdk 未安装成功。请手动执行：")
        print('       "%s" -m pip install tqsdk' % (plain[0] if plain else "python"))
        print("       若网络不通，可加国内镜像：")
        print('       -i https://mirrors.aliyun.com/pypi/simple/')
        print("       注意：须用「启动面板时实际使用的那个」Python 来装。")
        print("=" * 62)
        input("\n按回车键退出…")
        return 1

    print("\n使用解释器：%s" % chosen)
    print("启动服务端…\n" + "-" * 62)
    try:
        r = subprocess.run([chosen, str(SERVER)] + args, **_flag_no_win())
        return int(r.returncode or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
