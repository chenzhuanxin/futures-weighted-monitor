# -*- coding: utf-8 -*-
r"""
启动器 —— 自动挑选「装了天勤 tqsdk」的 Python 解释器来运行 服务端.py

背景：本机往往装了多个 Python。PATH 里排在前面的那个未必装了 tqsdk，
直接 `python 服务端.py` 就会报「当前 Python 未安装天勤 tqsdk」。
本脚本会：

  1. 扫描本机所有可能的 Python 解释器
  2. 逐个试 `import tqsdk`，挑出能用的那个（优先本机已装的，不联网）
  3. 如果一个都没有：用当前解释器联网安装（多镜像依次重试）
  4. 用选中的解释器启动 服务端.py，并把命令行参数透传

真正的环境探测逻辑在 `环境自检.py`（服务端自己启动时也会用它自愈），
本文件只负责「找个能跑的解释器」这一层。

也可以直接用环境变量 PY_EXE 指定解释器：
    set PY_EXE=C:\Python314\python.exe
    python launch.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "服务端.py"
sys.path.insert(0, str(HERE))

import importlib                                                    # noqa: E402

HEAL = importlib.import_module("环境自检")


def main() -> int:
    args = sys.argv[1:]
    print("=" * 62)
    print(" 加权（仓权）合约监控面板 · 启动器")
    print("=" * 62)
    print("当前解释器：%s" % sys.executable)

    if not SERVER.is_file():
        print("[错误] 找不到 服务端.py（应与 launch.py 在同一目录）：%s" % SERVER)
        return 1

    if HEAL.here_has_tqsdk():
        chosen = sys.executable
        print("[√] 当前解释器已装天勤 tqsdk，直接用")
    else:
        print("当前解释器没有天勤 tqsdk，正在本机查找…")
        chosen = HEAL.find_other(log=print)
        if not chosen:
            print("\n[提示] 本机没有解释器装了 tqsdk，尝试自动安装…")
            target = sys.executable
            print("  安装到：%s" % target)
            if HEAL.try_install(target, log=print) and HEAL.has_tqsdk(target):
                chosen = target
            else:
                print("\n" + HEAL.fail_hint())
                try:
                    input("按回车键退出…")
                except Exception:                                   # noqa: BLE001
                    pass
                return 1

    print("\n使用解释器：%s" % chosen)
    print("启动服务端…\n" + "-" * 62)
    try:
        # 不加 CREATE_NO_WINDOW：让服务端共用同一个控制台，日志可见、Ctrl+C 可停
        r = subprocess.run([chosen, str(SERVER)] + args)
        return int(r.returncode or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
