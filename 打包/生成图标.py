# -*- coding: utf-8 -*-
r"""
生成应用图标：打包/图标.ico（多尺寸）+ 打包/图标_预览.png

设计说明
--------
主题：加权（仓权）合约监控面板 —— 期货行情监控。

  底：深色圆角方砖（上浅下深的蓝黑渐变），带细描边与顶部高光；
      深色底在浅色任务栏、深色任务栏上都能立住。
  图：五根 K 线柱，逐级抬升，最后一根大阳拔起 —— 表达「行情走势」；
      红涨绿跌，沿用中国期货习惯（与看板配色一致）。
  线：一条金色上升趋势线自左下贯穿至右上，压在 K 线之下 —— 表达
      「监控 / 趋势跟踪」，也是画面里的高光焦点。

所有坐标都按画布尺寸 S 做比例换算，且小尺寸（<=32px）自动切换到
精简版（去掉网格与高光、K 线减到 3 根并加粗），保证 16px 下依然清晰
可辨，而不是把 512px 硬缩成一团糊。

只用 Pillow，不联网。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

HERE = Path(__file__).resolve().parent

# ---- 配色（与看板保持一致：涨红 / 跌绿） ---------------------------------- #
BG_TOP = (34, 49, 84)        # 渐变上：#223154
BG_BOT = (12, 19, 34)        # 渐变下：#0c1322
UP_COL = (240, 78, 74)       # 涨 · 红 #f04e4a
DOWN_COL = (30, 168, 96)     # 跌 · 绿 #1ea860
GOLD = (255, 201, 77)        # 趋势线 · 金 #ffc94d
EDGE = (122, 152, 214)       # 描边 · 淡蓝

# 完整版 K 线：cx, open_y, close_y, high_y, low_y （均为画布比例，y 向下）
# 收盘价在上（close_y < open_y）= 上涨 = 红
BARS_FULL = [
    (0.232, 0.652, 0.712, 0.606, 0.752),
    (0.364, 0.712, 0.644, 0.602, 0.736),
    (0.496, 0.644, 0.578, 0.538, 0.668),
    (0.628, 0.578, 0.610, 0.548, 0.642),
    (0.760, 0.610, 0.436, 0.396, 0.628),
]
# 精简版（小图标用）：3 根更粗的柱，保留「抬升 + 大阳」的轮廓
BARS_SIMPLE = [
    (0.290, 0.660, 0.706, 0.606, 0.756),
    (0.500, 0.636, 0.512, 0.462, 0.664),
    (0.710, 0.544, 0.330, 0.262, 0.578),
]


def _grad(size: int):
    """竖向渐变底。"""
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        c = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3))
        d.line([(0, y), (size, y)], fill=c)
    return img


def _round_mask(size: int, radius: float) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def draw(size: int) -> Image.Image:
    """画一枚图标，返回 RGBA 图。"""
    S = float(size)
    simple = size <= 40
    bars = BARS_SIMPLE if simple else BARS_FULL
    bw = (0.145 if simple else 0.092) * S          # 柱体宽
    radius = 0.235 * S                              # 圆角

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # 1) 渐变底
    bg = _grad(size).convert("RGBA")
    canvas.alpha_composite(bg)

    d = ImageDraw.Draw(canvas)

    # 2) 网格（仅大尺寸）：三条横线，极淡
    if not simple:
        grid = (255, 255, 255, 18)
        for gy in (0.300, 0.470, 0.640):
            d.line([(0.135 * S, gy * S), (0.870 * S, gy * S)],
                   fill=grid, width=max(1, int(0.006 * S)))

    # 3) 趋势线（压在 K 线之下，金色，圆头端点）
    tw = max(1, int(round(0.022 * S)))
    d.line([(0.150 * S, 0.752 * S), (0.862 * S, 0.360 * S)],
           fill=GOLD + (255,), width=tw, joint="curve")
    if not simple:
        rr = 0.030 * S
        for px, py in ((0.150, 0.752), (0.862, 0.360)):
            d.ellipse([px * S - rr, py * S - rr, px * S + rr, py * S + rr],
                      fill=(20, 28, 48, 255), outline=GOLD + (255,),
                      width=max(1, int(round(0.014 * S))))
            # 端点空心圈：底色 + 金边，避免与柱体糊在一起

    # 4) K 线：先影线后实体（实体盖住影线中段，边界干净）
    for cx, oy, cy, hy, ly in bars:
        up = cy < oy                                # 收盘在上 = 涨
        col = UP_COL if up else DOWN_COL
        # 影线
        lw = max(1, int(0.020 * S))
        d.line([(cx * S, hy * S), (cx * S, ly * S)],
               fill=col + (255,), width=lw)
        # 实体
        top = min(oy, cy) * S
        bot = max(oy, cy) * S
        if bot - top < max(1.0, 0.016 * S):          # 保证最细柱也看得见
            bot = top + max(1.0, 0.016 * S)
        d.rectangle([cx * S - bw / 2, top, cx * S + bw / 2, bot],
                    fill=col + (255,))

    # 5) 圆角裁切
    mask = _round_mask(size, radius)
    a = ImageChops.multiply(canvas.getchannel("A"), mask)
    canvas.putalpha(a)

    # 6) 描边（内缩，保证四角完整）
    d = ImageDraw.Draw(canvas)
    ins = max(1, int(0.012 * S))
    d.rounded_rectangle(
        [ins, ins, size - 1 - ins, size - 1 - ins],
        radius=max(1, int(radius - ins)),
        outline=EDGE + (95,), width=max(1, int(0.016 * S)))

    # 7) 顶部高光：整体轻微提亮上沿，不画明显光条
    if not simple:
        hl = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(hl).rounded_rectangle(
            [ins, ins, size - 1 - ins, size - 1 - ins],
            radius=max(1, int(radius - ins)),
            fill=(0, 0, 0, 0), outline=(255, 255, 255, 26),
            width=max(1, int(0.030 * S)))
        canvas.alpha_composite(hl)

    return canvas


def build_ico(items: list, path: Path) -> None:
    """自己组装 ICO 容器，让每个尺寸都是「原生绘制」而非缩放。

    PIL 的 ``im.save(..., sizes=[...])`` 是把一张大图resize 到各尺寸，
    16px 会糊成一团；这里逐尺寸调用 ``draw()``，小图标按精简版重画，
    边缘才立得住。条目用 PNG 压缩（Vista 及以上均支持）。
    """
    import io
    import struct

    blobs, entries = [], []
    offset = 6 + 16 * len(items)
    for size, im in items:
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        blobs.append(data)
        entries.append((size, len(data), offset))
        offset += len(data)

    with open(path, "wb") as fh:
        fh.write(struct.pack("<HHH", 0, 1, len(items)))
        for size, nbytes, off in entries:
            b = 0 if size >= 256 else size      # 256 在字节位上记 0
            fh.write(struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, nbytes, off))
        for data in blobs:
            fh.write(data)


def main() -> int:
    # ICO 内嵌尺寸：覆盖 Windows 资源管理器 / 任务栏 / 快捷方式的取图标范围
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    ico = HERE / "图标.ico"
    build_ico([(s, draw(s)) for s in sizes], ico)

    prev = HERE / "图标_预览.png"
    draw(512).save(prev, format="PNG")

    print("图标已生成：")
    for p in (ico, prev):
        print("   %s  (%.1f KB)" % (p, p.stat().st_size / 1024.0))
    print("内嵌尺寸：%s" % ", ".join(str(s) for s in sizes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
