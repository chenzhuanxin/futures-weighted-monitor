# -*- mode: python ; coding: utf-8 -*-
r"""
PyInstaller 打包配置 —— 加权（仓权）合约监控面板 · 单文件 exe

用法（在项目根目录）：
    <装了pyinstaller的python> -m PyInstaller --noconfirm --clean 加权合约监控面板.spec

要点：
  * 入口是 打包/app_entry.py（ASCII 文件名，见该文件头注释）；
  * 服务端.py 用 importlib.import_module 按字符串导入四个中文模块，
    PyInstaller 静态分析不到，必须在 hiddenimports 里显式点名；
  * tqsdk 及其伴生包（tqsdk_sm / tqsdk_ctpse）里既有数据文件又有
    顶层导入，用 collect_all 一网打尽；
  * onefile：EXE 直接吃下 binaries/datas，产出孤立的单个 exe；
  * 管理员的账号/授权码只在运行期的 缓存/*.json 里，不进包、不进仓库。
"""
from PyInstaller.utils.hooks import collect_all

datas = [
    ("静态", "静态"),            # 看板 / 行情页 / 本地 echarts
]
binaries = []
hiddenimports = [
    # 服务端.py 与 打包/app_entry.py 里 importlib 按字符串导入的中文模块
    "服务端",
    "环境自检",
    "品种表",
    "天勤行情",
    "邮件预警",
]

# tqsdk 全家桶：代码 + 数据（expired_quotes.json.lzma 等）+ 伴生二进制包
for _pkg in ("tqsdk", "tqsdk_sm", "tqsdk_ctpse",
             "sgqlc", "shinny_structlog"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# 与运行无关、只会白白膨胀体积的包
# ⚠ 不要排除 unittest：scipy 的 array_api_compat 会经 numpy.testing
#   动态 import 它，排掉后 tqsdk→scipy.stats 在运行期才炸，且极难排查
excludes = [
    "tkinter", "test",
    "openpyxl", "et_xmlfile", "docx", "pptx",
    "matplotlib", "IPython", "jedi",
    "pyinstaller", "PyInstaller", "pefile", "altgraph",
]

a = Analysis(
    ["打包/app_entry.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="加权合约监控面板",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,                 # 保留控制台：日志、看板地址、Ctrl+C 退出
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="打包/图标.ico",
)
