# -*- coding: utf-8 -*-
r"""
把 dist 里的 exe 发布到 CNB 仓库的 Release。

用法
----
    set CNB_TOKEN=你的访问令牌
    python 打包/发布Release.py            # 用 spec 里的默认版本号
    python 打包/发布Release.py v1.1.0     # 指定 tag

流程（CNB Release 附件是「预签名 URL」三步走）：
    1) POST /{repo}/-/releases                      → 创建 Release，拿到 id
    2) POST /{repo}/-/releases/{id}/asset-upload-url → 拿到 upload_url
    3) PUT  upload_url                              → 上传文件流
    4) POST verify_url                              → 确认落库，附件才可见

令牌只在环境变量里传，**不写进任何文件、不进仓库**（见 .gitignore 的 .env.* 规则）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

API = "https://api.cnb.cool"
REPO = "gongqizi0/futures-weighted-monitor"
EXE = ROOT / "dist" / "加权合约监控面板.exe"

# CNB 的 406 提示：Accept 与 Content-Type 都必须是这两种之一
HDR_JSON = {"Content-Type": "application/json",
            "Accept": "application/vnd.cnb.api+json"}
HDR_GET = {"Accept": "application/vnd.cnb.api+json"}


def req(method: str, url: str, body=None, headers=None, raw=None):
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if raw is not None:
        data = raw
    r = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=120) as fh:
            return fh.getcode(), fh.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    token = os.environ.get("CNB_TOKEN", "").strip()
    if not token:
        print("[错误] 请先设置环境变量 CNB_TOKEN（CNB 个人设置 → 访问令牌）")
        return 1

    tag = sys.argv[1] if len(sys.argv) > 1 else "v1.0.0"
    auth = {"Authorization": "Bearer " + token}

    if not EXE.is_file():
        print("[错误] 找不到 %s，请先执行打包" % EXE)
        return 1
    size = EXE.stat().st_size

    # 1) 创建 Release（若该 tag 已存在则直接复用，避免重复发版）
    code, blob = req("GET", "%s/%s/-/releases/tags/%s" % (API, REPO, tag),
                     None, dict(auth, **HDR_GET))
    if code == 200:
        rel = json.loads(blob.decode("utf-8"))
        rid = rel.get("id")
        print("[√] 已存在 Release：id=%s tag=%s（复用并覆盖附件）" % (rid, tag))
        return upload_asset(rid, token, tag)

    form = {
        "tag_name": tag,
        "target_commitish": "main",
        "name": "加权（仓权）合约监控面板 %s" % tag,
        "body": """## 加权（仓权）合约监控面板 {tag}

跑在本机的期货全品种监控看板（Python + HTML）。

### 核心能力

- **只用加权（仓权）合约** `KQ.i@交易所.品种`（按持仓量加权），不使用主连
- **偏离度量化**：以历史均价 M 为 0 基准，高侧 `(现价−M)/(H−M)×100`、低侧 `(M−现价)/(M−L)×100`
- **动态均线阈值**：周期 × 均线二级选择填充 + 0.1%~1% 上/下偏移 + 锁定（解锁不预警）；门槛 60%~90% 可调
- **1 分钟 K 线穿越预警**：上一根在阈内、当前根穿越到阈外才触发，同一根 K 线只报一次
- **卡片式邮件**：只发邮件，无弹窗 / 无声音；非交易时段静默
- **SSE 实时跟随**：事件驱动推送，无定时刷新，实时显示数据延时
- **Ctrl+Z 隐身**：整页变普通《通知》文档，标签标题同步改变
- **单实例保护**：重复启动复用已有实例，避免配置互相覆盖

### 安装

1. 下载下方 `加权合约监控面板.exe`（约 91 MB）
2. 放到任意文件夹，**双击运行**
3. 控制台窗口**不要关**（关闭即退出服务）；几秒后浏览器自动打开看板
4. 右上角「设置」填写天勤账号与邮件配置即可开始监控

> 也可源码运行：`pip install tqsdk` 后双击 `启动面板.bat`

### 数据源

- 实时行情与 K 线：**天勤 TqSdk 3.10.2**（上海信易信息科技股份有限公司）
- 历史基准 H/L/M：期货日 K 数据汇总（2026-10 导出），已内置于 `品种表.py`
- 碳酸锂 / 多晶硅 / 工业硅原始导出被截断，已自动改用加权合约全历史日 K 现算

详见仓库内 `docs/数据源说明.md`。

### 免责声明

行情数据版权归上海信易信息科技股份有限公司所有；本项目仅供个人学习研究，
禁止商业用途与原始数据二次分发。行情存在延时，**不构成任何投资建议**，
据此交易风险自负。

---

设计：**公歧子**　微信：**gongqizi0**
""".format(tag=tag),
        "draft": False,
        "prerelease": False,
        "make_latest": "true",
    }
    code, blob = req("POST", "%s/%s/-/releases" % (API, REPO), form,
                     dict(auth, **HDR_JSON))
    if code not in (200, 201):
        print("[失败] 创建 Release：HTTP %s\n%s" % (code, blob.decode("utf-8", "replace")[:800]))
        return 1
    rel = json.loads(blob.decode("utf-8"))
    rid = rel.get("id")
    print("[√] Release 已创建：id=%s tag=%s" % (rid, rel.get("tag_name")))
    return upload_asset(rid, token, tag)


def upload_asset(rid: str, token: str, tag: str) -> int:
    """预签名三步走：取地址 → PUT 文件 → 确认。"""
    auth = {"Authorization": "Bearer " + token}
    size = EXE.stat().st_size

    # 2) 取预签名上传地址
    url = "%s/%s/-/releases/%s/asset-upload-url" % (API, REPO, rid)
    code, blob = req("POST", url,
                     {"asset_name": EXE.name, "size": size,
                      "overwrite": True, "ttl": 0},
                     dict(auth, **HDR_JSON))
    if code not in (200, 201):
        print("[失败] 获取上传地址：HTTP %s\n%s" % (code, blob.decode("utf-8", "replace")[:800]))
        return 1
    info = json.loads(blob.decode("utf-8"))
    up = info.get("upload_url")
    verify = info.get("verify_url")
    print("[√] 已获取上传地址，开始上传 %.1f MB…" % (size / 1048576.0))

    # 3) PUT 文件流
    fh = open(EXE, "rb")
    try:
        r = urllib.request.Request(up, data=fh, method="PUT")
        r.add_header("Content-Type", "application/octet-stream")
        r.add_header("Content-Length", str(size))
        with urllib.request.urlopen(r, timeout=900) as resp:
            print("[√] 上传完成：HTTP %s" % resp.getcode())
    except urllib.error.HTTPError as exc:
        print("[失败] 上传：HTTP %s\n%s" % (exc.code, exc.read()[:500]))
        return 1
    finally:
        fh.close()

    # 4) 确认
    if verify:
        code, blob = req("POST", verify, {}, dict(auth, **HDR_GET))
        print("[√] 确认结果：HTTP %s %s" % (code, blob.decode("utf-8", "replace")[:200]))

    print("\n仓库：https://cnb.cool/%s" % REPO)
    print("Release：https://cnb.cool/%s/-/releases" % REPO)
    print("直链：https://cnb.cool/%s/-/releases/download/%s/%s"
          % (REPO, tag, urllib.parse.quote(EXE.name)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
