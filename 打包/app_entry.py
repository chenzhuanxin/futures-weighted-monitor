# -*- coding: utf-8 -*-
r"""
PyInstaller 打包专用入口（文件名刻意用 ASCII，与 launch.py 同理）。

流程：
  1. multiprocessing.freeze_support() —— 冻结程序里子进程引导的保险丝
  2. 把 PyInstaller 解包目录（sys._MEIPASS）挂上 sys.path，
     让 服务端.py 能 import 到同为中文模块名的 环境自检/品种表/…（它们由
     spec 的 hiddenimports 显式收入，PyInstaller 无法静态分析 importlib 的
     字符串导入）
  3. import 服务端 并运行 main() —— 参数与 .bat 版完全一致
     （--port / --no-browser / --force）

注意：服务端.py 在模块导入期会执行 环境自检.ensure()；打包态下
（sys.frozen）该函数直接短路，不会尝试「换解释器重启」——那是源码
运行时代的自愈逻辑，在 exe 里没有意义。
"""
from __future__ import annotations

import multiprocessing
import os
import sys


def main() -> int:
    multiprocessing.freeze_support()

    here = getattr(sys, "_MEIPASS", None) \
        or os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    # 打包态诊断：tqsdk 若在 exe 里 import 失败，把真实原因打到控制台，
    # 否则只看得到「加载失败」四个字，无从排查
    if getattr(sys, "frozen", False):
        try:
            import tqsdk
            print("[环境] tqsdk %s 就绪" % getattr(tqsdk, "__version__", "?"))
        except Exception:                                       # noqa: BLE001
            print("[环境] tqsdk 导入失败，详细原因：")
            import traceback
            traceback.print_exc()
            print("-" * 62)

    import importlib
    srv = importlib.import_module("服务端")
    return int(srv.main() or 0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
