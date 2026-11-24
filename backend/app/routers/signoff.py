"""一次性受控签认路由：代跑用量查询、签认提交、报销凭证与待补办清单。

委托的登记/撤销走通用资源路由（/api/run_delegation）；凭证只读，不提供
任何修改入口（已签的凭证不能被改签）。本路由必须注册在通用 /api/{module}
路由之前，避免被模块通配符抢占。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import service_for

router = APIRouter(prefix="/api/signoff", tags=["一次性受控签认"])


class SignPayload(BaseModel):
    delegation_id: int
    run_id: int


@router.get("/usage")
def api_delegated_usage(
    executor: str | None = Query(default=None),
    dataset: str | None = Query(default=None),
) -> dict[str, Any]:
    """代跑用量数据：确证运行 + 按运行日志计算的费用 + 签认状态。"""
    service = service_for("query_history")
    return {"ok": True, "items": service.delegated_usage(executor, dataset)}


@router.post("/sign")
def api_sign(payload: SignPayload) -> dict[str, Any]:
    """提交签认：数据报告按代跑用量数据生成报销凭证。

    同一份委托同时被提交也只有一笔成功；失败的一笔进入待补办。
    """
    service = service_for("data_report")
    voucher, message = service.create_reimbursement_voucher(
        payload.delegation_id, payload.run_id)
    return {"ok": voucher is not None, "message": message, "voucher": voucher}


@router.get("/vouchers")
def api_list_vouchers(
    business_group: str | None = Query(default=None),
    executor: str | None = Query(default=None),
) -> dict[str, Any]:
    """报销凭证列表：按委托归属业务组或实际代跑人核对。"""
    service = service_for("data_report")
    return {"ok": True, "items": service.list_vouchers(business_group, executor)}


@router.get("/vouchers/{voucher_id}")
def api_voucher_detail(voucher_id: int) -> dict[str, Any]:
    """凭证详情：本次签认扣的是哪笔运行、签认金额与作废的授权余额。"""
    service = service_for("data_report")
    voucher = service.voucher_detail(voucher_id)
    if voucher is None:
        raise HTTPException(status_code=404, detail=f"报销凭证 {voucher_id} 不存在")
    return voucher


@router.get("/reissue")
def api_pending_reissue(
    business_group: str = Query(...),
) -> dict[str, Any]:
    """业务组待补办清单：委托覆盖范围内确证但未签的运行。"""
    service = service_for("data_report")
    return {"ok": True, "items": service.pending_reissue(business_group)}
