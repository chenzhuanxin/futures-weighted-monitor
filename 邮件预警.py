# -*- coding: utf-8 -*-
"""
邮件预警 · 卡片式 HTML 邮件
=============================

把加权（仓权）合约的阈值预警以「卡片式」HTML 邮件发出。
需求 5：**删除弹窗 / 弹出网页 / 声音预警，只用邮件**；邮件须把内容说清楚
（时间、品种、现价、阈值、方向、偏离度、历史 H/L/M、涨跌幅、持仓、延时……）。

正文刻意写成**内联样式的表格卡片**（不用 <style> 标签、不用 flex/grid），
因为 QQ 邮箱 / 126 邮箱 / 微信内置浏览器对 CSS 的支持参差不齐，
只有 table + inline-style 的路子在各家客户端都能正常显示。
"""

from __future__ import annotations

import smtplib
import ssl
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

# 配色（中国习惯：涨=红，跌=绿）
C_UP = "#e0403f"        # 上穿上阈（看涨）
C_DOWN = "#12a150"      # 下穿下阈（看跌）
C_DARK = "#1f2a37"
C_GRAY = "#6b7280"
C_LINE = "#e5e7eb"
FONT = "-apple-system,'Segoe UI','Microsoft YaHei',sans-serif"


def fmt_price(v, digits: int = 2) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if abs(f) >= 10000:
        return "{:,.0f}".format(f)
    return ("{:,.%df}" % max(0, min(6, int(digits)))).format(f)


def _kv_row(label: str, value: str, color: str = C_DARK) -> str:
    return (
        '<tr>'
        '<td style="padding:3px 8px 3px 0;font-size:12px;color:%s;'
        'white-space:nowrap;font-family:%s;">%s</td>'
        '<td style="padding:3px 0;font-size:13px;color:%s;font-weight:600;'
        'font-family:%s;">%s</td>'
        '</tr>' % (C_GRAY, FONT, label, color, FONT, value)
    )


def _alert_card(a: dict) -> str:
    """单个触发品种的卡片。"""
    is_up = a.get("state") == "up"
    color = C_UP if is_up else C_DOWN
    bg = "#fff5f5" if is_up else "#f2fbf6"
    border = "#ffd9d9" if is_up else "#cdeedb"
    arrow = "▲" if is_up else "▼"
    title = "上穿上阈" if is_up else "下穿下阈"
    digits = int(a.get("digits", 2) or 2)

    price = fmt_price(a.get("price"), digits)
    limit = fmt_price(a.get("limit"), digits)
    diff = a.get("diff")
    diff_txt = ("%+.*f" % (digits, diff)) if isinstance(diff, (int, float)) else "-"
    pct = a.get("pct")                        # 当日涨跌幅
    pct_txt = ("%+.2f%%" % pct) if isinstance(pct, (int, float)) else "-"
    pct_color = C_UP if (isinstance(pct, (int, float)) and pct >= 0) else C_DOWN
    percent = a.get("percent")                # 历史偏离度
    percent_txt = ("%.2f%%" % percent) if isinstance(percent, (int, float)) else "-"

    body = "".join([
        _kv_row("触发时间", str(a.get("at", ""))),
        _kv_row("交易所", "%s（%s）" % (a.get("exch", "-"), a.get("tq", "-"))),
        _kv_row("加权现价", price, color),
        _kv_row("触发阈值", "%s = %s" % (a.get("side", ""), limit), color),
        _kv_row("穿越幅度", diff_txt, color),
        _kv_row("当日涨跌", pct_txt, pct_color),
        _kv_row("历史偏离度", percent_txt),
        _kv_row("历史 高/低/均",
                "%s / %s / %s" % (fmt_price(a.get("H"), digits),
                                  fmt_price(a.get("L"), digits),
                                  fmt_price(a.get("M"), digits))),
        _kv_row("成交量 / 持仓", "{:,} / {:,}".format(
            int(a.get("volume") or 0), int(a.get("oi") or 0))),
        _kv_row("阈值基准",
                "%s（%s）" % (a.get("thrLabel", "-"), a.get("offsetLabel", "无偏移"))),
        _kv_row("行情时间 / 延时", "%s · %s" % (a.get("quoteTime", "-"),
                                                a.get("delayLabel", "-"))),
        _kv_row("突破来源", a.get("triggerNote", "1分钟K线穿越")),
    ])

    return """
    <tr><td style="padding:9px 0;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background:{bg};border:1px solid {border};border-left:5px solid {color};
                    border-radius:12px;">
        <tr>
          <td colspan="2" style="padding:14px 16px 6px;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
              <td width="40" valign="middle">
                <div style="width:34px;height:34px;line-height:34px;border-radius:9px;
                            background:{color};color:#ffffff;font-size:15px;
                            text-align:center;font-weight:bold;">{arrow}</div>
              </td>
              <td valign="middle" style="padding-left:10px;font-family:{font};">
                <span style="font-size:16px;font-weight:700;color:{color};
                             letter-spacing:.3px;">{name}</span>
                <span style="font-size:12px;font-weight:600;color:{color};
                             background:#ffffff;border:1px solid {border};
                             border-radius:20px;padding:1px 9px;margin-left:6px;">{title}</span>
              </td>
              <td valign="middle" align="right" style="font-family:{font};">
                <div style="font-size:19px;font-weight:800;color:{color};">{price}</div>
                <div style="font-size:11px;color:{gray};">加权现价</div>
              </td>
            </tr></table>
          </td>
        </tr>
        <tr><td colspan="2" style="padding:2px 16px 14px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">{body}</table>
        </td></tr>
      </table>
    </td></tr>
    """.format(bg=bg, border=border, color=color, arrow=arrow, font=FONT,
               name=a.get("name", ""), title=title, price=price,
               gray=C_GRAY, body=body)


def build_alert_html(alerts: list[dict], *, title: str = "加权合约阈值预警",
                     source: str = "天勤加权（仓权）合约",
                     test: bool = False) -> str:
    """把预警列表渲染成一张卡片式 HTML 邮件。"""
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    month_cn = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                "十一", "十二"][now.month - 1]

    cards = "".join(_alert_card(a) for a in alerts)
    n_up = sum(1 for a in alerts if a.get("state") == "up")
    n_down = len(alerts) - n_up

    banner = ""
    if test:
        banner = """
        <tr><td style="padding:0 0 12px;">
          <div style="background:#fff8e1;border:1px dashed #f0b429;border-radius:10px;
                      padding:10px 14px;font-size:13px;color:#8a6d3b;font-family:%s;">
            这是一封<b>测试邮件</b>，用于验证 SMTP 配置是否可用；若下方有内容即为当前真实触发的预警。
          </div>
        </td></tr>
        """ % FONT

    empty = ""
    if not alerts:
        empty = ("""
        <tr><td style="padding:16px;text-align:center;background:#f8fafc;
                       border:1px dashed %s;border-radius:12px;font-size:13px;
                       color:%s;font-family:%s;">
          当前没有触发中的预警品种。
        </td></tr>
        """ % (C_LINE, C_GRAY, FONT))

    return """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;padding:0;background:#eef2f7;">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#eef2f7;padding:24px 12px;">
  <tr><td align="center">
    <table width="660" cellpadding="0" cellspacing="0" border="0"
           style="max-width:660px;width:100%;background:#ffffff;border-radius:18px;
                  overflow:hidden;box-shadow:0 12px 34px rgba(15,23,42,.10);">

      <tr><td style="background:#1f2a37;background-image:linear-gradient(135deg,#243b53 0%,#1f2a37 55%,#111827 100%);
                     padding:26px 28px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
          <td valign="middle" width="54">
            <div style="width:44px;height:44px;line-height:44px;border-radius:12px;
                        background:rgba(255,255,255,.14);color:#ffffff;
                        font-size:22px;text-align:center;">&#128276;</div>
          </td>
          <td valign="middle" style="padding-left:14px;font-family:{font};">
            <div style="font-size:20px;font-weight:700;color:#ffffff;letter-spacing:.5px;">
              {title}
            </div>
            <div style="font-size:13px;color:#9db4cc;margin-top:3px;">
              {source} · 实时跟随行情 · {stamp}
            </div>
          </td>
        </tr></table>
      </td></tr>

      <tr><td style="padding:18px 24px 6px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
          <td width="33%%" align="center" style="padding:6px;">
            <div style="background:#f8fafc;border:1px solid {line};border-radius:12px;
                        padding:12px 6px;font-family:{font};">
              <div style="font-size:22px;font-weight:800;color:{dark};">{total}</div>
              <div style="font-size:12px;color:{gray};margin-top:2px;">触发品种</div>
            </div>
          </td>
          <td width="33%%" align="center" style="padding:6px;">
            <div style="background:#fff5f5;border:1px solid #ffd9d9;border-radius:12px;
                        padding:12px 6px;font-family:{font};">
              <div style="font-size:22px;font-weight:800;color:{up};">{nup}</div>
              <div style="font-size:12px;color:{gray};margin-top:2px;">上穿上阈</div>
            </div>
          </td>
          <td width="33%%" align="center" style="padding:6px;">
            <div style="background:#f2fbf6;border:1px solid #cdeedb;border-radius:12px;
                        padding:12px 6px;font-family:{font};">
              <div style="font-size:22px;font-weight:800;color:{down};">{ndown}</div>
              <div style="font-size:12px;color:{gray};margin-top:2px;">下穿下阈</div>
            </div>
          </td>
        </tr></table>
      </td></tr>

      <tr><td style="padding:6px 24px 4px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          {banner}{cards}{empty}
        </table>
      </td></tr>

      <tr><td style="padding:14px 24px 26px;">
        <div style="border-top:1px solid {line};padding-top:14px;
                    font-size:12px;color:#9aa5b1;line-height:1.9;font-family:{font};">
          本邮件由「加权（仓权）合约监控面板」自动发出，请勿直接回复。<br>
          统计口径：H = 历史最高 · L = 历史最低 · M = 历史主连平均收盘价 ·
          偏离度 = (现价 − M) ÷ (H − M)（高于 M）或 (现价 − M) ÷ (L − M)（低于 M）。<br>
          阈值来源：加权合约所选周期 K 线的动态均线（最后一根用实时价替代）＋ 偏移量，锁定后生效。<br>
          触发判定：1 分钟K线穿越阈值（穿越即报，持续超阈不重复），非交易时段静默。
        </div>
      </td></tr>

    </table>
    <div style="font-size:11px;color:#a0aab5;margin-top:14px;font-family:{font};">
      {y} 年 {m} 月 · 加权（仓权）合约监控面板
    </div>
  </td></tr>
</table>
</body></html>""".format(title=title, font=FONT, source=source, stamp=stamp,
                       total=len(alerts), nup=n_up, ndown=n_down,
                       up=C_UP, down=C_DOWN, dark=C_DARK, gray=C_GRAY,
                       line=C_LINE, banner=banner, cards=cards, empty=empty,
                       y=now.year, m=month_cn)


def build_test_html(cfg: dict) -> str:
    """纯配置测试邮件（不依赖行情）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    to = "、".join(cfg.get("to") or [])
    cc = "、".join(cfg.get("cc") or []) or "无"
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;background:#eef2f7;padding:24px 12px;">
<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="center">
  <table width="560" cellpadding="0" cellspacing="0" border="0"
         style="max-width:560px;width:100%;background:#fff;border-radius:18px;overflow:hidden;
                box-shadow:0 12px 34px rgba(15,23,42,.10);">
    <tr><td style="background:linear-gradient(135deg,#243b53 0%,#1f2a37 60%,#111827 100%);
                   padding:24px 26px;color:#fff;font-family:{font};">
      <div style="font-size:19px;font-weight:700;">SMTP 配置测试成功</div>
      <div style="font-size:13px;color:#9db4cc;margin-top:4px;">加权（仓权）合约监控面板 · {now}</div>
    </td></tr>
    <tr><td style="padding:22px 26px;font-family:{font};font-size:14px;
                   color:#374151;line-height:2;">
      发件账号：<b>{user}</b><br>
      收件人：<b>{to}</b><br>
      抄送：<b>{cc}</b><br>
      服务器：<b>{host}:{port}</b>（{ssl}）<br>
      <div style="margin-top:14px;background:#f2fbf6;border:1px solid #cdeedb;
                  border-left:4px solid #12a150;border-radius:10px;padding:12px 14px;
                  font-size:13px;color:#1b5e20;">
        邮件通道已打通，触发阈值时预警邮件会自动发送到上面的收件人。
      </div>
    </td></tr>
  </table>
</td></tr></table>
</body></html>""".format(now=now, user=cfg.get("user", ""), to=to, cc=cc,
                       host=cfg.get("host", ""), port=cfg.get("port", ""),
                       ssl="SSL" if cfg.get("ssl", True) else "STARTTLS",
                       font=FONT)


def send_mail(cfg: dict, subject: str, html: str) -> dict:
    """发送一封 HTML 邮件。cfg 为 smtp 配置段。返回 {ok, message}。"""
    host = (cfg.get("host") or "").strip()
    port = int(cfg.get("port") or 465)
    user = (cfg.get("user") or "").strip()
    auth = (cfg.get("authcode") or "").strip()
    to_list = [x.strip() for x in (cfg.get("to") or []) if x and x.strip()]
    cc_list = [x.strip() for x in (cfg.get("cc") or []) if x and x.strip()]
    from_name = cfg.get("from_name") or "加权合约监控"

    if not host or not user or not auth:
        return {"ok": False, "message": "SMTP 服务器 / 发件账号 / 授权码不能为空"}
    if not to_list:
        return {"ok": False, "message": "收件人不能为空"}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), user))
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Date"] = formatdate(localtime=True)
    msg.attach(MIMEText(html, "html", "utf-8"))

    rcpt = to_list + cc_list
    try:
        if cfg.get("ssl", True):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE      # 部分网络环境证书链不全
            with smtplib.SMTP_SSL(host, port, timeout=30, context=ctx) as s:
                s.login(user, auth)
                s.sendmail(user, rcpt, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo()
                try:
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                except Exception:                     # noqa: BLE001
                    pass
                s.login(user, auth)
                s.sendmail(user, rcpt, msg.as_string())
        return {"ok": True, "message": "已发送至 " + "、".join(rcpt)}
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "message": "%s: %s" % (type(exc).__name__, exc)}


if __name__ == "__main__":
    demo = [{
        "name": "沪铜", "state": "up", "exch": "上期所", "tq": "KQ.i@SHFE.cu",
        "price": 110230.0, "limit": 109500.0, "diff": 730.0, "pct": 1.24,
        "percent": 92.31, "H": 114700, "L": 21410, "M": 56662.64,
        "volume": 128394, "oi": 421337, "digits": 0,
        "thrLabel": "60分 MA20", "offsetLabel": "上偏移 0.2%",
        "quoteTime": "2026-10-15 21:35:00", "delayLabel": "延时 0.8 秒",
        "at": "2026-10-15 21:35:01", "triggerNote": "1分钟K线由下向上穿越",
    }]
    html = build_alert_html(demo)
    out = "邮件预览.html"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("已写出预览：", out, len(html), "字节")
