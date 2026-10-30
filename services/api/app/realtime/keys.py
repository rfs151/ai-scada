"""Redis 键名，与 services/fep/fep/main.py 保持一致。"""

RT_VALUES = "rt:val"  # Hash: 测点 → {"v","q","t"}
RT_CHANGES = "rt:chg"  # Stream: d=[[点号, 值, 品质, 时标ms], ...]
YX_EVENTS = "ev:yx"  # Stream: e={...}
FRAMES = "fep:frames"  # Stream: dir/hex/text/t/ch
CMD_REQ = "cmd:req"  # Stream: r={...}
CHANNEL_PREFIX = "ch:"  # Hash: 通道状态
EV_BUS = "ev:bus"  # Stream: m={"type": alarm|alarm_ack|tags|control|pdr, ...}  业务事件总线（api → 各客户端）
CTL_RECENT_PREFIX = "ctl:recent:"  # String(TTL): 设备最近一次遥控，用于区分遥控变位与事故/就地变位


def cmd_result(req_id: str) -> str:
    return f"cmd:res:{req_id}"
