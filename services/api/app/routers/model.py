from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api/model", tags=["model"])


@router.get("/station", summary="厂站模型：电压等级、间隔、设备")
async def station(request: Request) -> dict:
    st = request.app.state.station
    return {
        "id": st.id,
        "name": st.name,
        "ca": st.ca,
        "voltage_levels": st.voltage_levels,
        # 拓扑着色所需：电源点（上级电网等值节点）与各连接节点的电压等级
        "sources": [st.grid["node"]],
        "nodes": st.node_vl(),
        "bays": [
            {
                "id": b.id,
                "name": b.name,
                "kind": b.kind,
                "vl": b.vl,
                "equipment": [
                    {
                        "id": e.id,
                        "type": e.type,
                        "name": e.name,
                        "vl": e.vl,
                        "nodes": e.nodes,
                        **({"normal": "closed" if e.normally_closed else "open"} if e.is_switch else {}),
                    }
                    for e in b.equipment
                ],  # fmt: skip
            }
            for b in st.bays
        ],
    }


@router.get("/points", summary="点表")
async def points(
    request: Request,
    cat: str | None = Query(None, description="YC/YX/YK/YT/DD"),
    bay: str | None = None,
) -> list[dict]:
    st = request.app.state.station
    return [p.to_dict() for p in st.points if (cat is None or p.cat == cat) and (bay is None or p.bay == bay)]
