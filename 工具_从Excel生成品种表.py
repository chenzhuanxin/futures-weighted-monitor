# -*- coding: utf-8 -*-
"""从 Excel 汇总表生成 品种表.py"""
import openpyxl, io, os

XLSX = r"C:/Users/gongq/Downloads/futures-daily-k-data/期货日K数据/汇总_全部品种.xlsx"

# Excel 品种代码 -> (中文显示名, 交易所key, 天勤加权合约)
MAP = {
    "A":   ("豆一",      "DCE",   "KQ.i@DCE.a"),
    "AD":  ("铸造铝",    "SHFE",  "KQ.i@SHFE.ad"),
    "AG":  ("沪银",      "SHFE",  "KQ.i@SHFE.ag"),
    "AL":  ("沪铝",      "SHFE",  "KQ.i@SHFE.al"),
    "AO":  ("氧化铝",    "SHFE",  "KQ.i@SHFE.ao"),
    "AP":  ("苹果",      "CZCE",  "KQ.i@CZCE.AP"),
    "AU":  ("沪金",      "SHFE",  "KQ.i@SHFE.au"),
    "B":   ("豆二",      "DCE",   "KQ.i@DCE.b"),
    "BB":  ("胶合板",    "DCE",   "KQ.i@DCE.bb"),
    "BC":  ("国际铜",    "INE",   "KQ.i@INE.bc"),
    "BR":  ("丁二烯胶",  "SHFE",  "KQ.i@SHFE.br"),
    "BU":  ("沥青",      "SHFE",  "KQ.i@SHFE.bu"),
    "C":   ("玉米",      "DCE",   "KQ.i@DCE.c"),
    "CF":  ("棉花",      "CZCE",  "KQ.i@CZCE.CF"),
    "CJ":  ("红枣",      "CZCE",  "KQ.i@CZCE.CJ"),
    "CS":  ("淀粉",      "DCE",   "KQ.i@DCE.cs"),
    "CU":  ("沪铜",      "SHFE",  "KQ.i@SHFE.cu"),
    "CY":  ("棉纱",      "CZCE",  "KQ.i@CZCE.CY"),
    "EB":  ("苯乙烯",    "DCE",   "KQ.i@DCE.eb"),
    "EC":  ("欧线集运",  "INE",   "KQ.i@INE.ec"),
    "EG":  ("乙二醇",    "DCE",   "KQ.i@DCE.eg"),
    "ER":  ("早稻(旧)",  "CZCE",  None),
    "FB":  ("纤维板",    "DCE",   "KQ.i@DCE.fb"),
    "FG":  ("玻璃",      "CZCE",  "KQ.i@CZCE.FG"),
    "FU":  ("燃油",      "SHFE",  "KQ.i@SHFE.fu"),
    "HC":  ("热卷",      "SHFE",  "KQ.i@SHFE.hc"),
    "I":   ("铁矿石",    "DCE",   "KQ.i@DCE.i"),
    "IC":  ("中证500股指", "CFFEX", "KQ.i@CFFEX.IC"),
    "IF":  ("沪深",      "CFFEX", "KQ.i@CFFEX.IF"),
    "IH":  ("上证",      "CFFEX", "KQ.i@CFFEX.IH"),
    "IM":  ("中证1000股指", "CFFEX", "KQ.i@CFFEX.IM"),
    "J":   ("焦炭",      "DCE",   "KQ.i@DCE.j"),
    "JD":  ("鸡蛋",      "DCE",   "KQ.i@DCE.jd"),
    "JM":  ("焦煤",      "DCE",   "KQ.i@DCE.jm"),
    "JR":  ("粳稻",      "CZCE",  "KQ.i@CZCE.JR"),
    "L":   ("塑料",      "DCE",   "KQ.i@DCE.l"),
    "LC":  ("碳酸锂",    "GFEX",  "KQ.i@GFEX.lc"),
    "LG":  ("原木",      "DCE",   "KQ.i@DCE.lg"),
    "LH":  ("生猪",      "DCE",   "KQ.i@DCE.lh"),
    "LR":  ("晚籼稻",    "CZCE",  None),
    "LU":  ("低硫燃油",  "INE",   "KQ.i@INE.lu"),
    "M":   ("豆粕",      "DCE",   "KQ.i@DCE.m"),
    "MA":  ("甲醇",      "CZCE",  "KQ.i@CZCE.MA"),
    "ME":  ("甲醇(旧)",  "CZCE",  None),
    "NI":  ("沪镍",      "SHFE",  "KQ.i@SHFE.ni"),
    "NR":  ("20号胶",    "INE",   "KQ.i@INE.nr"),
    "OI":  ("菜油",      "CZCE",  "KQ.i@CZCE.OI"),
    "P":   ("棕榈油",    "DCE",   "KQ.i@DCE.p"),
    "PB":  ("沪铅",      "SHFE",  "KQ.i@SHFE.pb"),
    "PF":  ("短纤",      "CZCE",  "KQ.i@CZCE.PF"),
    "PG":  ("LPG",       "DCE",   "KQ.i@DCE.pg"),
    "PK":  ("花生",      "CZCE",  "KQ.i@CZCE.PK"),
    "PL":  ("丙烯",      "CZCE",  "KQ.i@CZCE.PL"),
    "PM":  ("普麦",      "CZCE",  "KQ.i@CZCE.PM"),
    "PP":  ("聚丙烯",    "DCE",   "KQ.i@DCE.pp"),
    "PR":  ("瓶片",      "CZCE",  "KQ.i@CZCE.PR"),
    "PS":  ("多晶硅",    "GFEX",  "KQ.i@GFEX.ps"),
    "PX":  ("对二甲苯",  "CZCE",  "KQ.i@CZCE.PX"),
    "RB":  ("螺纹钢",    "SHFE",  "KQ.i@SHFE.rb"),
    "RI":  ("早籼稻",    "CZCE",  "KQ.i@CZCE.RI"),
    "RM":  ("菜粕",      "CZCE",  "KQ.i@CZCE.RM"),
    "RO":  ("菜籽油(旧)", "CZCE", None),
    "RR":  ("粳米",      "DCE",   "KQ.i@DCE.rr"),
    "RS":  ("菜籽",      "CZCE",  "KQ.i@CZCE.RS"),
    "RU":  ("橡胶",      "SHFE",  "KQ.i@SHFE.ru"),
    "SA":  ("纯碱",      "CZCE",  "KQ.i@CZCE.SA"),
    "SC":  ("原油",      "INE",   "KQ.i@INE.sc"),
    "SF":  ("硅铁",      "CZCE",  "KQ.i@CZCE.SF"),
    "SH":  ("烧碱",      "CZCE",  "KQ.i@CZCE.SH"),
    "SI":  ("工业硅",    "GFEX",  "KQ.i@GFEX.si"),
    "SM":  ("锰硅",      "CZCE",  "KQ.i@CZCE.SM"),
    "SN":  ("沪锡",      "SHFE",  "KQ.i@SHFE.sn"),
    "SP":  ("纸浆",      "SHFE",  "KQ.i@SHFE.sp"),
    "SR":  ("白糖",      "CZCE",  "KQ.i@CZCE.SR"),
    "SS":  ("不锈钢",    "SHFE",  "KQ.i@SHFE.ss"),
    "T":   ("十债",      "CFFEX", "KQ.i@CFFEX.T"),
    "TA":  ("PTA",       "CZCE",  "KQ.i@CZCE.TA"),
    "TC":  ("动力煤(旧)", "CZCE", None),
    "TF":  ("五债",      "CFFEX", "KQ.i@CFFEX.TF"),
    "TL":  ("三十债",    "CFFEX", "KQ.i@CFFEX.TL"),
    "TS":  ("二债",      "CFFEX", "KQ.i@CFFEX.TS"),
    "UR":  ("尿素",      "CZCE",  "KQ.i@CZCE.UR"),
    "V":   ("PVC",       "DCE",   "KQ.i@DCE.v"),
    "WH":  ("强麦",      "CZCE",  "KQ.i@CZCE.WH"),
    "WR":  ("线材",      "SHFE",  "KQ.i@SHFE.wr"),
    "WS":  ("强麦(旧)",  "CZCE",  None),
    "WT":  ("普麦(旧)",  "CZCE",  None),
    "Y":   ("豆油",      "DCE",   "KQ.i@DCE.y"),
    "ZC":  ("动力煤",    "CZCE",  "KQ.i@CZCE.ZC"),
    "ZN":  ("沪锌",      "SHFE",  "KQ.i@SHFE.zn"),
}

# 交易所中文名
EXCH_CN = {"SHFE": "上期所", "INE": "上期能源", "DCE": "大商所",
           "CZCE": "郑商所", "GFEX": "广期所", "CFFEX": "中金所"}

wb = openpyxl.load_workbook(XLSX, data_only=True)
ws = wb["汇总"]
rows = list(ws.iter_rows(values_only=True))[1:]

lines = []
miss = []
for r in rows:
    code = str(r[0]).strip()
    if code not in MAP:
        miss.append(code)
        continue
    cname, exch, tq = MAP[code]
    H = r[6]; L = r[9]; M = r[12]
    ipo = str(r[3])
    days = r[4]
    # 原始日K导出被截断的品种（交易日过少）→ 基准不可靠，改由服务端按
    # 加权合约全历史日K自动计算
    auto = bool(days and days < 250)
    lines.append((code, cname, exch, tq, H, L, M, ipo, days, auto))

# 补充：原参照网页有、但本次日K汇总未覆盖的 3 个品种（基准同样自动计算）
EXTRA = [
    ("PT", "铂",     "GFEX", "KQ.i@GFEX.pt"),
    ("PD", "钯",     "GFEX", "KQ.i@GFEX.pd"),
    ("OP", "胶版纸", "SHFE", "KQ.i@SHFE.op"),
]
for (code, cname, exch, tq) in EXTRA:
    lines.append((code, cname, exch, tq, 0, 0, 0, "", 0, True))
wb.close()

auto_list = [x[0] for x in lines if x[9]]
print("覆盖品种：", len(lines), " 未映射：", miss, " 自动基准：", auto_list)

out = io.StringIO()
out.write("# -*- coding: utf-8 -*-\n")
out.write('"""\n')
out.write("品种表 · 加权（仓权）合约版\n")
out.write("=" * 60 + "\n\n")
out.write("数据源：天勤 TqSdk 官方「指数合约」KQ.i@交易所.品种\n")
out.write("        （官方按**持仓量加权**计算，即「加权（仓权）合约」）\n\n")
out.write("历史基准 H / L / M 来自本地日K数据汇总：\n")
out.write("    汇总_全部品种.xlsx  （期货日K数据，2026-10 导出）\n")
out.write("    H = 历史最高价   L = 历史最低价   M = 历史主连平均收盘价\n\n")
out.write("百分比口径（与参照网页完全一致，未做任何改动）：\n")
out.write("    现价 ≥ M 时： pct = (现价 − M) ÷ (H − M) × 100\n")
out.write("    现价 <  M 时： pct = (现价 − M) ÷ (L − M) × 100\n\n")
out.write("字段：code 品种代码 / name 中文名 / exch 交易所 / tq 加权合约 /\n")
out.write("      H 历史最高 / L 历史最低 / M 历史均价 / ipo 上市日 / days 交易日数 /\n")
out.write("      auto=True 表示 Excel 基准不可靠（原始导出被截断），\n")
out.write("           改由服务端按加权合约**全历史日K**自动计算 H/L/M\n")
out.write('"""\n\n')
out.write("# (code, name, exch, tq加权合约, H, L, M, 上市日, 交易日数, 基准自动)\n")
out.write("_ROWS = [\n")
for (code, cname, exch, tq, H, L, M, ipo, days, auto) in lines:
    tqs = ('"%s"' % tq) if tq else "None"
    out.write('    (%-5s %-10s %-9s %-22s %-11s %-11s %-13s %-11s %-6s %s),\n'
              % ('"%s",' % code, '"%s",' % cname, '"%s",' % EXCH_CN[exch],
                 '%s,' % tqs, '%s,' % repr(H), '%s,' % repr(L), '%s,' % repr(M),
                 '"%s",' % ipo, '%s,' % days, auto))
out.write("]\n\n")
out.write('''VARIETIES = [
    {"code": c, "name": n, "exch": e, "tq": t, "H": H, "L": L, "M": M,
     "ipo": p, "days": d, "auto": bool(a)}
    for (c, n, e, t, H, L, M, p, d, a) in _ROWS
]

CODES = [v["code"] for v in VARIETIES]
BY_CODE = {v["code"]: v for v in VARIETIES}
NAME = {v["code"]: v["name"] for v in VARIETIES}
EXCH = {v["code"]: v["exch"] for v in VARIETIES}
TQ = {v["code"]: v["tq"] for v in VARIETIES if v["tq"]}
BASE = {v["code"]: {"H": v["H"], "L": v["L"], "M": v["M"]} for v in VARIETIES}

# 可正常取行情的品种（有加权合约映射）
LIVE_CODES = [v["code"] for v in VARIETIES if v["tq"]]
# 需要按加权合约全历史自动计算基准的品种
AUTO_CODES = [v["code"] for v in VARIETIES if v["tq"] and v["auto"]]
# 无加权合约（已退市的老品种）
DEAD_CODES = [v["code"] for v in VARIETIES if not v["tq"]]


def codes_live() -> list:
    return list(LIVE_CODES)


if __name__ == "__main__":
    print("品种总数 %d   可交易 %d   已退市 %d   自动基准 %d"
          % (len(VARIETIES), len(LIVE_CODES), len(DEAD_CODES), len(AUTO_CODES)))
    print("已退市（无加权合约）：",
          "、".join(BY_CODE[c]["name"] for c in DEAD_CODES))
    print("自动计算基准：",
          "、".join(BY_CODE[c]["name"] for c in AUTO_CODES))
''')


dst = r"C:/Users/gongq/WorkBuddy/2026-10-15-15-16-19/加权合约监控面板/品种表.py"
os.makedirs(os.path.dirname(dst), exist_ok=True)
with open(dst, "w", encoding="utf-8") as fh:
    fh.write(out.getvalue())
print("已写出：", dst)
