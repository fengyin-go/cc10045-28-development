"""数据报告领域服务：管理周期性分析报告的模板配置和自动生成。

真实数据流：定义报告模板（包含指标集/周期/输出格式）→ pipeline.generate_report 读取最新指标值
→ 组装结构化报告内容 → 写入 report_outputs → 快照管理。

报销凭证：数据报告使用查询历史算出的代跑用量数据，对业务组签发的委托做一次性
受控签认，生成报销凭证；凭证写明本次扣的是哪笔运行，未签的运行进入待补办清单。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db
from app.pipeline import generate_report
from app.services.base import BaseModuleService
from app.services.query_history import CONFIRMED_RUN_STATUSES, compute_run_fee
from app.services.run_delegation import run_delegation_service


class DataReportService(BaseModuleService):
    MODULE = "data_report"
    ENTITY = "报告生成器"
    OPERATION_TERM = "生成"
    LIST_FIELDS = ["报告名称", "包含指标集", "报告周期", "输出格式", "自动发送"]
    REQUIRED_FIELDS = ["报告名称", "包含指标集", "报告周期"]
    STATUS_ORDER = ["草稿", "已发布", "已停用"]
    ACTION_RULES = {"发布报告": "已发布", "停用报告": "已停用"}
    NEGATIVE_ACTIONS = []

    def generate(self, report_id: int) -> dict[str, Any]:
        """生成一次数据报告。"""
        report = db.fetchone("SELECT * FROM reports WHERE id=?", [report_id])
        if report is None:
            return {"ok": False, "message": f"报告 {report_id} 不存在"}

        result = generate_report(dict(report))
        return {"ok": True, "report_name": result["report_name"], "summary": result["summary"]}

    def recent_outputs(self, report_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """获取报告的历史生成结果。"""
        rows = db.fetchall(
            "SELECT * FROM report_outputs WHERE report_id=? ORDER BY generated_at DESC LIMIT ?",
            [report_id, limit],
        )
        for r in rows:
            try:
                r["output_content"] = json.loads(r["output_content"]) if isinstance(
                    r["output_content"], str) else r["output_content"]
            except json.JSONDecodeError:
                pass
        return rows

    def compare_outputs(self, output_id_a: int, output_id_b: int) -> dict[str, Any]:
        """对比两份报告输出的差异。"""
        a = db.fetchone("SELECT * FROM report_outputs WHERE id=?", [output_id_a])
        b = db.fetchone("SELECT * FROM report_outputs WHERE id=?", [output_id_b])
        if not a or not b:
            return {"ok": False, "message": "报告输出不存在"}

        try:
            content_a = json.loads(a["output_content"]) if isinstance(a["output_content"], str) else a["output_content"]
            content_b = json.loads(b["output_content"]) if isinstance(b["output_content"], str) else b["output_content"]
        except json.JSONDecodeError:
            return {"ok": False, "message": "内容解析失败"}

        diffs = []
        sections_a = content_a.get("sections", [])
        sections_b = content_b.get("sections", [])

        for sa, sb in zip(sections_a, sections_b):
            val_a = sa.get("current_value", 0)
            val_b = sb.get("current_value", 0)
            if val_a and val_b and val_a != 0:
                pct = round((val_a - val_b) / abs(val_a) * 100, 2)
                if abs(pct) > 5:
                    diffs.append({
                        "metric": sa.get("metric_name"),
                        "value_a": val_a,
                        "value_b": val_b,
                        "diff_pct": pct,
                    })

        return {
            "ok": True,
            "output_a_time": a["generated_at"],
            "output_b_time": b["generated_at"],
            "significant_diffs": len(diffs),
            "diffs": diffs,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_reports"] = db.count("reports")
        snapshot["total_outputs"] = db.count("report_outputs")
        return snapshot

    # ------------------------------------------------------------------
    # 代跑费用报销凭证（一次性受控签认）
    # ------------------------------------------------------------------
    def create_reimbursement_voucher(
        self, delegation_id: int, run_id: int,
    ) -> tuple[dict[str, Any] | None, str]:
        """用代跑用量数据对指定运行生成报销凭证。

        签认核心由 run_delegation 服务执行：每份委托只能签一整笔运行，
        核销后余额作废；同一份委托同时被提交也只能成功一次。
        """
        return run_delegation_service.sign_run(delegation_id, run_id)

    def list_vouchers(
        self, business_group: str | None = None, executor: str | None = None,
    ) -> list[dict[str, Any]]:
        """报销凭证列表，可按委托归属业务组或实际代跑人核对。"""
        return run_delegation_service.list_vouchers(business_group, executor)

    def voucher_detail(self, voucher_id: int) -> dict[str, Any] | None:
        """凭证详情：本次签认扣的是哪笔运行、金额与作废的授权余额。"""
        return run_delegation_service.get_voucher(voucher_id)

    def pending_reissue(self, business_group: str) -> list[dict[str, Any]]:
        """业务组待补办清单：其委托覆盖的执行人×数据集下，确证但未签的运行。"""
        pairs = db.fetchall(
            "SELECT DISTINCT executor, dataset FROM run_delegations WHERE business_group=?",
            [business_group])
        placeholders = ",".join("?" for _ in CONFIRMED_RUN_STATUSES)
        items: list[dict[str, Any]] = []
        seen: set[int] = set()
        for pair in pairs:
            rows = db.fetchall(
                f"""SELECT q.* FROM query_log q
                    WHERE q.executor=? AND q.dataset=? AND q.status IN ({placeholders})
                      AND NOT EXISTS (SELECT 1 FROM signoff_vouchers v WHERE v.run_id=q.id)
                    ORDER BY q.id DESC""",
                [pair["executor"], pair["dataset"], *CONFIRMED_RUN_STATUSES])
            for row in rows:
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                row["fee"] = compute_run_fee(row)
                row["signoff_status"] = "待补办"
                items.append(row)
        return items