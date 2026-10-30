"""由厂站模型构建 pandapower 电网并提取量测。

建模要点：
    - 每个连接节点 → 一条母线；断路器/刀闸 → 母线-母线开关（分闸即改变拓扑，孤岛部分潮流为 NaN → 记 0）
    - 接地刀不参与潮流，只有位置状态（五防逻辑使用）
    - 主变有载调压：pandapower tap_pos 为相对中性点的分接位置（正值 = 高压侧匝数增加 → 低压侧电压降低）；
      对外"档位"按国内习惯 1~17 档、9 档为额定，档位数越大低压侧电压越高：档位 = 9 - tap_pos
"""

import logging
import math
import warnings
from dataclasses import dataclass

import pandapower as pp

from scadalib.model import Station

log = logging.getLogger(__name__)
warnings.filterwarnings("ignore", module="pandapower")

TAP_NEUTRAL_POSITION = 9


def tap_to_position(tap_pos: int) -> int:
    return TAP_NEUTRAL_POSITION - tap_pos


@dataclass(slots=True)
class Indices:
    bus: dict[str, int]
    switch: dict[str, int]
    line: dict[str, int]
    trafo: dict[str, int]
    load: dict[str, int]  # 出线间隔 id → load
    sgen: dict[str, int]  # 光伏间隔 id → sgen
    shunt: dict[str, int]  # 电容器间隔 id → shunt


def build_net(st: Station) -> tuple[pp.pandapowerNet, Indices]:
    net = pp.create_empty_network(name=st.name, f_hz=50.0)
    vl_kv = {k: v["vn_kv"] for k, v in st.voltage_levels.items()}
    idx = Indices({}, {}, {}, {}, {}, {}, {})

    for node, vl in st.node_vl().items():
        idx.bus[node] = pp.create_bus(net, vn_kv=vl_kv[vl], name=node)

    pp.create_ext_grid(
        net,
        idx.bus[st.grid["node"]],
        vm_pu=st.grid["vm_pu"],
        s_sc_max_mva=st.grid["s_sc_max_mva"],
        name="上级电源",
    )

    for e in st.equipment:
        match e.type:
            case "line":
                p = st.std_types["line"][e.attrs["std"]]
                idx.line[e.id] = pp.create_line_from_parameters(
                    net, idx.bus[e.attrs["from"]], idx.bus[e.attrs["to"]], e.attrs["length_km"],
                    p["r_ohm_per_km"], p["x_ohm_per_km"], p["c_nf_per_km"], p["max_i_ka"], name=e.id,
                )  # fmt: skip
            case "transformer":
                p = dict(st.std_types["transformer"][e.attrs["std"]])
                idx.trafo[e.id] = pp.create_transformer_from_parameters(
                    net, idx.bus[e.attrs["hv"]], idx.bus[e.attrs["lv"]], tap_pos=e.attrs.get("tap", 0),
                    tap_changer_type="Ratio", name=e.id, **p,
                )  # fmt: skip
            case "breaker" | "disconnector":
                a, b = e.nodes
                idx.switch[e.id] = pp.create_switch(
                    net, idx.bus[a], idx.bus[b], et="b", closed=e.normally_closed, name=e.id,
                    type="CB" if e.type == "breaker" else "DS",
                )  # fmt: skip

    for bay in st.bays:
        match bay.kind:
            case "feeder":
                line = next(e for e in bay.equipment if e.type == "line")
                idx.load[bay.id] = pp.create_load(net, idx.bus[line.attrs["to"]], p_mw=0.0, q_mvar=0.0,
                                                  name=bay.id)  # fmt: skip
            case "pv":
                line = next(e for e in bay.equipment if e.type == "line")
                idx.sgen[bay.id] = pp.create_sgen(net, idx.bus[line.attrs["to"]], p_mw=0.0, q_mvar=0.0,
                                                  name=bay.id, type="PV")  # fmt: skip
            case "capacitor":
                cb = next(e for e in bay.equipment if e.type == "breaker")
                # q_mvar 为负表示发出无功（容性）
                idx.shunt[bay.id] = pp.create_shunt(net, idx.bus[cb.nodes[1]], q_mvar=-bay.attrs["q_mvar"],
                                                    name=bay.id)  # fmt: skip
    return net, idx


def run_pf(net: pp.pandapowerNet) -> bool:
    try:
        pp.runpp(net, algorithm="nr", init="results", numba=False, max_iteration=20)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("power flow failed: %s", e)
        try:
            pp.runpp(net, algorithm="nr", init="flat", numba=False)
            return True
        except Exception:  # noqa: BLE001
            return False


def nz(x: float) -> float:
    """孤岛/停电设备的潮流结果为 NaN，按 0 处理。"""
    return 0.0 if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def cos_phi(p: float, q: float) -> float:
    s = math.hypot(p, q)
    return abs(p) / s if s > 1e-6 else 1.0
