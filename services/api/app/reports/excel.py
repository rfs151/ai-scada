"""运行报表导出 Excel（openpyxl）：概况、负荷曲线（含折线图）、电压合格率、主变、出线。"""

from __future__ import annotations

import io
from datetime import datetime
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

TZ = ZoneInfo("Asia/Shanghai")
HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")
HEAD_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14)
THIN = Side(style="thin", color="B7C3D0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BAD_FONT = Font(color="C0392B", bold=True)
LEVEL_CN = {1: "事故", 2: "异常", 3: "越限", 4: "变位", 5: "告知"}
OPS_CN = {
    "control.execute": "遥控执行",
    "control.adjust": "遥调",
    "tag.add": "挂牌",
    "sim.inject": "场景注入",
}


def _dt(ms: int | None) -> datetime | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, TZ).replace(tzinfo=None)


def _table(ws, row: int, headers: list[str], rows: list[list], formats: dict[int, str] | None = None) -> int:
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row, c, h)
        cell.fill, cell.font, cell.border = HEAD_FILL, HEAD_FONT, BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for r, values in enumerate(rows, row + 1):
        for c, v in enumerate(values, 1):
            cell = ws.cell(r, c, v)
            cell.border = BORDER
            if isinstance(v, datetime):
                cell.number_format = "yyyy-mm-dd hh:mm"
            elif formats and c in formats:
                cell.number_format = formats[c]
    return row + len(rows) + 2


def _widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def to_xlsx(rep: dict, station_name: str, generated_by: str | None = None) -> bytes:
    wb = Workbook()

    # ---------------------------------------------------------------- 概况
    ws = wb.active
    ws.title = "概况"
    ws["A1"] = f"{station_name} 运行报表"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"统计时段：{_dt(rep['start']):%Y-%m-%d %H:%M} ~ {_dt(rep['end']):%Y-%m-%d %H:%M}"
    ws["A3"] = f"生成时间：{datetime.now(TZ):%Y-%m-%d %H:%M:%S}" + (
        f"    制表：{generated_by}" if generated_by else ""
    )
    ld, im = rep["load"], rep["import"]
    row = _table(
        ws, 5, ["负荷特性", "10kV 负荷", "下网功率"],
        [
            ["最大值 (MW)", ld["max"], im["max"]],
            ["最大值时刻", _dt(ld["max_t"]), _dt(im["max_t"])],
            ["最小值 (MW)", ld["min"], im["min"]],
            ["最小值时刻", _dt(ld["min_t"]), _dt(im["min_t"])],
            ["平均值 (MW)", ld["avg"], im["avg"]],
            ["负荷率 (%)", ld["load_factor"], im["load_factor"]],
            ["峰谷差率 (%)", ld["peak_valley"], im["peak_valley"]],
        ],
        {2: "0.00", 3: "0.00"},
    )  # fmt: skip
    row = _table(ws, row, ["告警级别", "条数"], [[LEVEL_CN[k], rep["alarms"].get(k, 0)] for k in range(1, 6)])
    ops = [[OPS_CN[k], v["ok"], v["fail"]] for k, v in rep["operations"].items() if k in OPS_CN]
    row = _table(ws, row, ["操作", "成功", "失败"], ops or [["（无）", 0, 0]])
    ws.cell(row, 1, "事故追忆次数")
    ws.cell(row, 2, rep["pdr"])
    _widths(ws, [16, 20, 20])

    # ---------------------------------------------------------------- 负荷曲线
    ws = wb.create_sheet("负荷曲线")
    rows = [[_dt(c["t"]), c["import"], c["load"], c["pv"]] for c in rep["curve"]]
    _table(
        ws,
        1,
        ["时间", "下网功率 (MW)", "10kV 负荷 (MW)", "光伏出力 (MW)"],
        rows,
        {2: "0.00", 3: "0.00", 4: "0.00"},
    )
    ws.freeze_panes = "A2"
    _widths(ws, [18, 15, 15, 15])
    if rows:
        chart = LineChart()
        chart.title = "负荷曲线"
        chart.y_axis.title = "MW"
        chart.height, chart.width = 9, 22
        chart.add_data(
            Reference(ws, min_col=2, max_col=4, min_row=1, max_row=len(rows) + 1), titles_from_data=True
        )
        chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=len(rows) + 1))
        chart.x_axis.number_format = "hh:mm"
        for s in chart.series:
            s.smooth = False
            s.graphicalProperties.line.width = 15000
        ws.add_chart(chart, "F2")

    # ---------------------------------------------------------------- 电压合格率
    ws = wb.create_sheet("电压合格率")
    vrows = [
        [v["name"], v["lo"], v["hi"], v["rate"], v["minutes"], v["over"], v["under"], v["outage"],
         v["max"], _dt(v["max_t"]), v["min"], _dt(v["min_t"])]
        for v in rep["voltage"]
    ]  # fmt: skip
    _table(
        ws, 1,
        ["测点", "下限 (kV)", "上限 (kV)", "合格率 (%)", "统计分钟", "超上限分钟", "超下限分钟", "停电分钟",
         "最高 (kV)", "最高时刻", "最低 (kV)", "最低时刻"],
        vrows, {4: "0.000", 9: "0.000", 11: "0.000"},
    )  # fmt: skip
    for r, v in enumerate(rep["voltage"], 2):
        if v["rate"] is not None and v["rate"] < 99.0:
            ws.cell(r, 4).font = BAD_FONT
    ws.cell(len(vrows) + 3, 1, "注：以 1 分钟平均电压统计，合格率 = 合格分钟 / (统计时段 − 停电时间)")
    _widths(ws, [24, 10, 10, 11, 10, 11, 11, 10, 10, 18, 10, 18])

    # ---------------------------------------------------------------- 主变
    ws = wb.create_sheet("主变")
    _table(
        ws, 1,
        ["主变", "最大负载率 (%)", "出现时刻", "平均负载率 (%)", "最高油温 (℃)", "出现时刻", "分接开关动作次数",
         "低压侧电量 (kWh)"],
        [[t["name"], t["load_max"], _dt(t["load_max_t"]), t["load_avg"], t["oil_max"], _dt(t["oil_max_t"]),
          t["tap_ops"], t["energy_kwh"]] for t in rep["transformers"]],
        {2: "0.0", 4: "0.0", 5: "0.0", 8: "#,##0"},
    )  # fmt: skip
    _widths(ws, [12, 15, 18, 15, 13, 18, 16, 16])

    # ---------------------------------------------------------------- 出线
    ws = wb.create_sheet("出线")
    _table(
        ws, 1,
        ["出线", "最大电流 (A)", "出现时刻", "电流上限 (A)", "最大有功 (MW)", "平均有功 (MW)", "电量 (kWh)", "事故跳闸次数"],
        [[f["name"], f["i_max"], _dt(f["i_max_t"]), f["i_limit"], f["p_max"], f["p_avg"], f["energy_kwh"], f["trips"]]
         for f in rep["feeders"]],
        {2: "0.0", 5: "0.00", 6: "0.00", 7: "#,##0"},
    )  # fmt: skip
    for r, f in enumerate(rep["feeders"], 2):
        if f["i_max"] is not None and f["i_limit"] and f["i_max"] > f["i_limit"]:
            ws.cell(r, 2).font = BAD_FONT
        if f["trips"]:
            ws.cell(r, 8).font = BAD_FONT
    ws.cell(len(rep["feeders"]) + 3, 1, "注：光伏并网线电量为上网电量；最大电流含故障期间短路电流")
    _widths(ws, [18, 13, 18, 13, 14, 14, 14, 13])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
