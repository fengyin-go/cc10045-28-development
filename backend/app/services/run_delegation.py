"""代跑委托与一次性受控签认：把业务组发出的委托绑定执行人、指定数据集和可签费用。

业务规则：
- 委托授权（run_delegations）由业务组签发，一次性有效：核销后整份授权作废，
  余额一并作废，不可拆分给多笔运行、不可复用。
- 签认（sign_run）按确证的运行日志计算代跑用量费用，校验委托归属、实际执行人、
  数据集归属与额度后，原子核销委托并生成报销凭证。
- 报销凭证（signoff_vouchers）创建即生效，全字段快照，不提供任何修改入口：
  已签的凭证不能被改签；撤销委托不影响已签凭证的金额与实际代跑人。
- 同一份委托同时被提交也只能用一次：BEGIN IMMEDIATE 串行化 + 状态 CAS +
  唯一索引三层保证，并发下恰好一笔签认成功，另一笔进入补办。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.database import db
from app.services.base import BaseModuleService
from app.services.query_history import CONFIRMED_RUN_STATUSES, compute_run_fee


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class RunDelegationService(BaseModuleService):
    MODULE = "run_delegation"
    ENTITY = "委托授权"
    OPERATION_TERM = "签认"
    LIST_FIELDS = ["委托方业务组", "被委托执行人", "指定数据集", "可签额度"]
    REQUIRED_FIELDS = ["委托方业务组", "被委托执行人", "指定数据集", "可签额度"]
    STATUS_ORDER = ["生效中", "已核销", "已撤销"]
    ACTION_RULES = {"撤销委托": "已撤销"}
    NEGATIVE_ACTIONS = []

    # ------------------------------------------------------------------
    # 委托生命周期
    # ------------------------------------------------------------------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """业务组签发委托：绑定执行人、指定数据集和可签费用额度。"""
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing
        try:
            quota = float(str(values.get("可签额度")).strip())
        except ValueError:
            return None, ["可签额度(须为数字)"]
        if quota <= 0:
            return None, ["可签额度(须大于0)"]

        delegation_id = db.insert("run_delegations", {
            "business_group": str(values["委托方业务组"]).strip(),
            "executor": str(values["被委托执行人"]).strip(),
            "dataset": str(values["指定数据集"]).strip(),
            "quota": quota,
            "status": "生效中",
        })
        db.update("run_delegations",
                  {"delegation_no": f"WT{delegation_id:06d}"}, "id=?", [delegation_id])
        return self.get_entry(delegation_id), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """撤销委托。已签凭证是快照，撤销不影响其金额与实际代跑人。"""
        if action not in self.ACTION_RULES:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if entry["status"] == "已撤销":
            return None, f"{self.ENTITY} {entry_id} 已是已撤销状态，请勿重复撤销"
        db.update("run_delegations",
                  {"status": "已撤销", "revoked_at": _now()}, "id=?", [entry_id])
        return self.get_entry(entry_id), f"{self.ENTITY}已{action}"

    # ------------------------------------------------------------------
    # 一次性受控签认
    # ------------------------------------------------------------------
    def sign_run(self, delegation_id: int, run_id: int) -> tuple[dict[str, Any] | None, str]:
        """用一份委托签认一整笔运行，核销委托并生成报销凭证。

        校验链（任一不满足即拒签，不产生任何写入）：
        委托生效中 → 运行已确证 → 实际执行人匹配 → 数据集匹配 → 费用不超额度
        → 该运行未被签过 → 原子核销（并发下仅一笔成功）。
        """
        try:
            with db.immediate_tx() as conn:
                delegation = conn.execute(
                    "SELECT * FROM run_delegations WHERE id=?", [delegation_id]).fetchone()
                if delegation is None:
                    return None, f"委托授权 {delegation_id} 不存在或已归档"
                if delegation["status"] == "已撤销":
                    return None, f"委托 {delegation['delegation_no']} 已撤销，不能用于签认"
                if delegation["status"] == "已核销":
                    return None, (f"委托 {delegation['delegation_no']} 已核销：每份授权只能签一整笔运行，"
                                  "余额已作废，未签的运行请另行补办")

                run = conn.execute(
                    "SELECT * FROM query_log WHERE id=?", [run_id]).fetchone()
                if run is None:
                    return None, f"运行日志 {run_id} 不存在"
                if run["status"] not in CONFIRMED_RUN_STATUSES:
                    return None, f"运行 {run_id} 未确证（当前状态：{run['status']}），不能签认"
                if run["executor"] != delegation["executor"]:
                    return None, (f"实际执行人「{run['executor']}」与委托绑定执行人"
                                  f"「{delegation['executor']}」不一致，不能借用本委托")
                if run["dataset"] != delegation["dataset"]:
                    return None, (f"运行数据集「{run['dataset']}」不属于本委托指定数据集"
                                  f"「{delegation['dataset']}」，不能借用本委托")

                fee = compute_run_fee(dict(run))
                if fee <= 0:
                    return None, f"运行 {run_id} 无可签用量（返回行数为 0）"
                if fee > delegation["quota"]:
                    return None, (f"运行费用 {fee} 超出委托可签额度 {delegation['quota']}，"
                                  "每份授权只能整笔签认，不能借用本委托")

                duplicate = conn.execute(
                    "SELECT id FROM signoff_vouchers WHERE run_id=?", [run_id]).fetchone()
                if duplicate:
                    return None, f"运行 {run_id} 已被签认，同一笔运行不能重复签"

                now = _now()
                voided = round(float(delegation["quota"]) - fee, 2)
                cursor = conn.execute(
                    """UPDATE run_delegations
                       SET status='已核销', used_run_id=?, used_amount=?,
                           voided_balance=?, consumed_at=?
                       WHERE id=? AND status='生效中'""",
                    [run_id, fee, voided, now, delegation_id])
                if cursor.rowcount != 1:
                    # 并发下另一笔签认已抢先核销：整体回滚，本笔进入补办。
                    raise ConcurrentSignoffError(
                        f"委托 {delegation['delegation_no']} 同时被提交，仅一笔可签，本笔请补办")

                cursor = conn.execute(
                    """INSERT INTO signoff_vouchers
                       (delegation_id, delegation_no, business_group, executor, dataset,
                        run_id, run_summary, run_executed_at, amount, quota_snapshot,
                        voided_balance, status, signed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,'已生效',?)""",
                    [delegation["id"], delegation["delegation_no"], delegation["business_group"],
                     delegation["executor"], delegation["dataset"], run["id"],
                     run["query_summary"], run["executed_at"], fee,
                     delegation["quota"], voided, now])
                voucher_id = cursor.lastrowid or 0
                conn.execute("UPDATE signoff_vouchers SET voucher_no=? WHERE id=?",
                             [f"PZ{voucher_id:06d}", voucher_id])
                voucher = conn.execute(
                    "SELECT * FROM signoff_vouchers WHERE id=?", [voucher_id]).fetchone()
        except ConcurrentSignoffError as exc:
            return None, str(exc)
        except sqlite3.IntegrityError:
            # 唯一索引兜底：同一委托或同一运行被并发插入时拒绝。
            return None, "签认冲突：同一委托或同一运行已被签认，本笔请补办"

        return dict(voucher), (f"签认成功：凭证 {voucher['voucher_no']} 扣运行 {run_id}，"
                               f"金额 {fee}，授权余额 {voided} 已作废")

    # ------------------------------------------------------------------
    # 凭证查询（只读：已签凭证不能被改签，服务层不提供任何修改方法）
    # ------------------------------------------------------------------
    def list_vouchers(
        self, business_group: str | None = None, executor: str | None = None,
    ) -> list[dict[str, Any]]:
        where = ["1=1"]
        params: list[Any] = []
        if business_group:
            where.append("business_group = ?")
            params.append(business_group)
        if executor:
            where.append("executor = ?")
            params.append(executor)
        return db.fetchall(
            f"SELECT * FROM signoff_vouchers WHERE {' AND '.join(where)} ORDER BY id DESC",
            params)

    def get_voucher(self, voucher_id: int) -> dict[str, Any] | None:
        """凭证详情：含真正扣掉的运行与作废的授权余额，归属和执行人均可核对。"""
        voucher = db.fetchone("SELECT * FROM signoff_vouchers WHERE id=?", [voucher_id])
        if voucher is None:
            return None
        voucher["deducted_run"] = db.fetchone(
            "SELECT * FROM query_log WHERE id=?", [voucher["run_id"]])
        return voucher


class ConcurrentSignoffError(Exception):
    """同一委托被并发核销时抛出，触发事务回滚。"""


run_delegation_service = RunDelegationService()
