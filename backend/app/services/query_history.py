"""查询历史领域服务：记录和审计所有分析查询的调用链路和性能。

真实数据流：每次分析查询调用 → 记录查询时间/执行人/涉及数据集/查询摘要/返回行数/耗时
→ 写入 query_log 表 → 自动标记慢查询（> 500ms）和超时（> 30s）。

代跑用量：确证运行日志（已正常出结果的运行）是代跑费用唯一可信来源，
费用 = 返回行数 × 行费率，由日志计算得出，请求方不能自报金额。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database import db
from app.services.base import BaseModuleService

# 确证运行：已完整跑完并产出结果的查询；超时终止没有可信产出，不计代跑用量。
CONFIRMED_RUN_STATUSES = ("正常", "慢查询")

# 代跑用量费率：元 / 返回行。
PRICE_PER_ROW = 0.01


def is_confirmed_run(run: dict[str, Any]) -> bool:
    """运行日志是否已确证（已完成且结果可信）。"""
    return run.get("status") in CONFIRMED_RUN_STATUSES


def compute_run_fee(run: dict[str, Any]) -> float:
    """按确证的运行日志计算代跑用量费用：费用 = 返回行数 × 行费率。"""
    return round(int(run.get("row_count") or 0) * PRICE_PER_ROW, 2)


class QueryHistoryService(BaseModuleService):
    MODULE = "query_history"
    ENTITY = "查询记录"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["查询时间", "执行人", "涉及数据集", "查询语句摘要", "返回行数"]
    REQUIRED_FIELDS = ["查询时间", "执行人", "涉及数据集"]
    STATUS_ORDER = ["正常", "慢查询", "超时终止"]
    ACTION_RULES = {"标记慢查询": "慢查询", "重新执行": "正常"}
    NEGATIVE_ACTIONS = []

    def record_query(
        self, dataset: str, query_summary: str, row_count: int,
        execution_time_ms: int, executor: str = "system",
    ) -> dict[str, Any]:
        """记录一条查询日志。"""
        status = "正常"
        if execution_time_ms > 30000:
            status = "超时终止"
        elif execution_time_ms > 500:
            status = "慢查询"

        log_id = db.insert("query_log", {
            "executor": executor,
            "dataset": dataset,
            "query_summary": query_summary,
            "row_count": row_count,
            "execution_time_ms": execution_time_ms,
            "status": status,
        })
        return {"id": log_id, "status": status}

    def performance_summary(self, hours: int = 24) -> dict[str, Any]:
        """获取近期查询性能摘要。"""
        total = db.count("query_log")
        slow = db.count("query_log", "status='慢查询'")
        timeout = db.count("query_log", "status='超时终止'")
        normal = db.count("query_log", "status='正常'")

        # 平均耗时
        avg_row = db.fetchone("SELECT AVG(execution_time_ms) as avg_ms FROM query_log")
        avg_ms = round(float(avg_row["avg_ms"]), 2) if avg_row and avg_row["avg_ms"] else 0

        return {
            "total_queries": total,
            "normal": normal,
            "slow_queries": slow,
            "timeout_queries": timeout,
            "avg_execution_ms": avg_ms,
            "slow_rate": round(slow / max(total, 1), 4),
        }

    def top_slow_queries(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取最慢的查询记录。"""
        return db.fetchall(
            "SELECT * FROM query_log WHERE status IN ('慢查询','超时终止') ORDER BY execution_time_ms DESC LIMIT ?",
            [limit],
        )

    def dataset_usage(self) -> list[dict[str, Any]]:
        """统计各数据集的查询频率。"""
        rows = db.fetchall(
            "SELECT dataset, COUNT(*) as total_queries, AVG(execution_time_ms) as avg_ms "
            "FROM query_log GROUP BY dataset ORDER BY total_queries DESC",
        )
        return rows

    def delegated_usage(
        self, executor: str | None = None, dataset: str | None = None,
    ) -> list[dict[str, Any]]:
        """代跑用量数据：确证运行 + 按日志计算的费用 + 签认状态。

        已签认的运行附上凭证号；未签认的标记为「待补办」，留给业务组另行开委托补签。
        """
        where = [f"q.status IN ({','.join('?' for _ in CONFIRMED_RUN_STATUSES)})"]
        params: list[Any] = list(CONFIRMED_RUN_STATUSES)
        if executor:
            where.append("q.executor = ?")
            params.append(executor)
        if dataset:
            where.append("q.dataset = ?")
            params.append(dataset)
        rows = db.fetchall(
            f"""SELECT q.*, v.id AS voucher_id, v.voucher_no
                FROM query_log q
                LEFT JOIN signoff_vouchers v ON v.run_id = q.id
                WHERE {' AND '.join(where)}
                ORDER BY q.id DESC""",
            params,
        )
        for row in rows:
            row["fee"] = compute_run_fee(row)
            row["signoff_status"] = "已签认" if row["voucher_id"] else "待补办"
        return rows

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_queries"] = db.count("query_log")
        snapshot["slow_count"] = db.count("query_log", "status='慢查询'")
        snapshot["timeout_count"] = db.count("query_log", "status='超时终止'")
        return snapshot