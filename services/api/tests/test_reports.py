import io

from openpyxl import load_workbook

from app.reports.excel import to_xlsx
from app.reports.stats import bucket_seconds, extreme, load_stats, voltage_stat

M = 60_000


def test_voltage_qualification_excludes_outage():
    # 10 分钟：6 合格、2 超上限、1 超下限、1 停电
    avgs = [10.5] * 6 + [10.8, 10.9] + [9.9] + [0.0]
    rows = [(i * M, v, v - 0.01 if v else 0.0, v + 0.01 if v else 0.0) for i, v in enumerate(avgs)]
    s = voltage_stat("BUS1.UAB", "10kV I 段", rows, lo=10.0, hi=10.7, dead_below=1.0)
    assert (s.minutes, s.over, s.under, s.outage) == (9, 2, 1, 1)
    assert s.rate == round(100 * 6 / 9, 3)
    assert s.max == 10.91 and s.max_t == 7 * M
    assert s.min == 9.89  # 停电分钟不计入最低电压


def test_load_stats_and_extreme():
    st = load_stats([(0, 10.0), (M, 20.0), (2 * M, 15.0), (3 * M, None)])
    assert st["max"] == 20.0 and st["max_t"] == M and st["min"] == 10.0
    assert st["load_factor"] == 75.0 and st["peak_valley"] == 50.0
    assert load_stats([])["max"] is None
    assert extreme([(0, 1.0, 0.5, 1.5), (M, 2.0, 1.8, 3.2)]) == {"max": 3.2, "max_t": M, "avg": 1.5}


def test_bucket_seconds():
    assert bucket_seconds(24 * 60) == 60
    assert bucket_seconds(86_400) == 300  # 288 点 → 5 分钟
    assert bucket_seconds(7 * 86_400) == 2100  # 35 分钟
    assert bucket_seconds(7 * 86_400 + 1) == 2160  # 不整除时向上取整到分钟


def test_excel_export():
    t0 = 1_793_000_000_000
    rep = {
        "start": t0, "end": t0 + 24 * M, "bucket_s": 60,
        "load": {"max": 20.0, "max_t": t0, "min": 10.0, "min_t": t0 + M, "avg": 15.0, "load_factor": 75.0,
                 "peak_valley": 50.0},
        "import": {"max": 18.0, "max_t": t0, "min": 8.0, "min_t": t0 + M, "avg": 13.0, "load_factor": 72.2,
                   "peak_valley": 55.6},
        "curve": [{"t": t0 + i * M, "import": 13.0 + i, "load": 15.0 + i, "pv": 2.0} for i in range(24)],
        "voltage": [{"point": "BUS1.UAB", "name": "10kV I 段母线线电压Uab", "lo": 10.0, "hi": 10.7, "rate": 98.5,
                     "minutes": 24, "over": 0, "under": 0, "outage": 0, "max": 10.6, "max_t": t0, "min": 10.3,
                     "min_t": t0}],
        "transformers": [{"bay": "T1", "name": "1号主变", "load_max": 40.0, "load_max_t": t0, "load_avg": 30.0,
                          "oil_max": 50.0, "oil_max_t": t0, "tap_ops": 2, "energy_kwh": 12345.0}],
        "feeders": [{"bay": "F02", "name": "10kV 锦苑线", "kind": "feeder", "i_max": 3800.0, "i_max_t": t0,
                     "i_limit": 416, "p_max": 5.0, "p_avg": 3.0, "energy_kwh": 4000.0, "trips": 2}],
        "alarms": {1: 5, 3: 10}, "operations": {"control.execute": {"ok": 3, "fail": 1}}, "pdr": 1,
    }  # fmt: skip
    wb = load_workbook(io.BytesIO(to_xlsx(rep, "110kV 示范变电站", "张工")))
    assert wb.sheetnames == ["概况", "负荷曲线", "电压合格率", "主变", "出线"]
    assert wb["概况"]["A1"].value == "110kV 示范变电站 运行报表"
    assert wb["负荷曲线"].max_row == 25 and len(wb["负荷曲线"]._charts) == 1
    assert wb["电压合格率"]["D2"].value == 98.5 and wb["电压合格率"]["D2"].font.bold  # 不足 99% 标红
    assert wb["出线"]["H2"].value == 2


def test_auto_reset_schedule():
    from app.autoreset import decide

    # 未到时间
    assert decide(100, 3600, None, 3600, 600) == (False, 3600)
    # 到时间、无人操作 → 复位，下次 1 小时后
    assert decide(3600, 3600, None, 3600, 600) == (True, 7200)
    # 到时间但 5 分钟前有人操作 → 推迟到空闲 10 分钟
    assert decide(3600, 3600, 3300, 3600, 600) == (False, 3900)
    assert decide(3900, 3900, 3300, 3600, 600) == (True, 7500)


def test_locf_fills_unchanged_minutes():
    from app.reports.stats import locf

    # 第 0、3 分钟有变化记录，其余分钟值未变（历史库无记录）
    rows = [(0, 10.0, 9.0, 11.0, 10.5), (3 * M, 20.0, 19.0, 21.0, 20.0)]
    out = locf(rows, 0, 5 * M, init=None)
    assert [r[0] for r in out] == [0, M, 2 * M, 3 * M, 4 * M]
    assert out[1] == (M, 10.5, 10.5, 10.5) and out[4] == (4 * M, 20.0, 20.0, 20.0)
    # 区间开头无记录：取区间前最后一个值
    assert locf([], 30_000, 2 * M + 30_000, init=7.0) == [
        (0, 7.0, 7.0, 7.0),
        (M, 7.0, 7.0, 7.0),
        (2 * M, 7.0, 7.0, 7.0),
    ]
