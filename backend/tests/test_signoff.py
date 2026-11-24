"""一次性受控签认验收测试。

运行方式（在 backend/ 目录下）：
    python3 -m unittest discover -s tests -v

覆盖场景：
- 业务组把某数据集的代跑签认权限借给值班员（额度 80）；
- 同一委托同时收到两笔签认（70 / 50），恰好一笔入账，另一笔进补办，不能拆段补到 80；
- 已生效凭证可见真正扣掉的运行与作废的授权余额；
- 同执行人但其他数据集的运行（30）不能借用本委托；
- 超额度、未确证、执行人不符、重复签同一运行均被拒；
- 撤销委托后，已签凭证金额与实际代跑人保持原值。
"""
from __future__ import annotations

import os
import tempfile

# 必须在导入 app 之前指向独立测试库，避免污染开发数据。
os.environ["CC10045_DB_DIR"] = tempfile.mkdtemp(prefix="signoff-test-")

import threading
import unittest

from app.database import db
from app.services import service_for
from app.services.query_history import compute_run_fee

GROUP = "电商分析组"
DUTY = "值班员小赵"
DATASET = "订单宽表"
OTHER_DATASET = "用户明细表"
QUOTA = 80.0


def make_run(executor: str, dataset: str, row_count: int,
             status: str = "正常", summary: str = "代跑汇总") -> int:
    """登记一条运行日志（查询历史）。"""
    return db.insert("query_log", {
        "executor": executor,
        "dataset": dataset,
        "query_summary": summary,
        "row_count": row_count,
        "execution_time_ms": 800,
        "status": status,
    })


class SignoffCase(unittest.TestCase):
    def setUp(self) -> None:
        for table in ("signoff_vouchers", "run_delegations", "query_log"):
            db.delete(table, "1=1")
        self.delegation_svc = service_for("run_delegation")
        self.report_svc = service_for("data_report")
        self.history_svc = service_for("query_history")
        # 业务组签发委托：值班员 + 指定数据集 + 额度 80
        entry, missing = self.delegation_svc.create_entry({
            "委托方业务组": GROUP,
            "被委托执行人": DUTY,
            "指定数据集": DATASET,
            "可签额度": str(int(QUOTA)),
        })
        self.assertEqual(missing, [])
        self.delegation_id = int(entry["id"])

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def sign(self, run_id: int, delegation_id: int | None = None):
        return self.report_svc.create_reimbursement_voucher(
            delegation_id or self.delegation_id, run_id)

    def vouchers(self):
        return self.report_svc.list_vouchers(business_group=GROUP)

    # ------------------------------------------------------------------
    # 核心场景：两笔同时提交，恰好一笔入账
    # ------------------------------------------------------------------
    def test_concurrent_sign_exactly_one_wins(self):
        run70 = make_run(DUTY, DATASET, 7000)   # 费用 70
        run50 = make_run(DUTY, DATASET, 5000)   # 费用 50
        self.assertEqual(compute_run_fee({"row_count": 7000}), 70.0)

        barrier = threading.Barrier(2)
        results: dict[int, tuple] = {}

        def submit(run_id: int) -> None:
            barrier.wait()  # 两笔尽量同时压上
            results[run_id] = self.sign(run_id)

        threads = [threading.Thread(target=submit, args=(rid,)) for rid in (run70, run50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        outcomes = {rid: (v is not None) for rid, (v, _msg) in results.items()}
        winners = [rid for rid, ok in outcomes.items() if ok]
        losers = [rid for rid, ok in outcomes.items() if not ok]

        # 恰好一笔入账：可签 70 或可签 50
        self.assertEqual(len(winners), 1, f"应恰好一笔签认成功，实际：{outcomes}")
        self.assertEqual(len(losers), 1)

        # 只入账一条凭证，金额是 70 或 50，绝不是拆段补到 80
        vouchers = self.vouchers()
        self.assertEqual(len(vouchers), 1)
        voucher = vouchers[0]
        self.assertIn(voucher["amount"], (70.0, 50.0))
        self.assertEqual(voucher["run_id"], winners[0])
        self.assertEqual(voucher["business_group"], GROUP)
        self.assertEqual(voucher["executor"], DUTY)
        self.assertEqual(voucher["status"], "已生效")

        # 委托一次性核销，余额作废
        delegation = self.delegation_svc.get_entry(self.delegation_id)
        self.assertEqual(delegation["status"], "已核销")
        self.assertEqual(delegation["used_run_id"], winners[0])
        self.assertEqual(delegation["used_amount"], voucher["amount"])
        self.assertEqual(delegation["voided_balance"], QUOTA - voucher["amount"])

        # 失败的一笔收到明确拒签原因
        loser_msg = results[losers[0]][1]
        self.assertIn("补办", loser_msg)

        # 不能把另一笔拆一段补到 80：委托核销后再签直接失败
        voucher2, msg2 = self.sign(losers[0])
        self.assertIsNone(voucher2)
        self.assertIn("已核销", msg2)
        self.assertEqual(len(self.vouchers()), 1)

        # 未签的那笔进入业务组待补办清单
        reissue_ids = [r["id"] for r in self.report_svc.pending_reissue(GROUP)]
        self.assertIn(losers[0], reissue_ids)
        self.assertNotIn(winners[0], reissue_ids)

    # ------------------------------------------------------------------
    # 凭证详情：真正扣掉的运行 + 授权余额作废
    # ------------------------------------------------------------------
    def test_voucher_detail_shows_deducted_run_and_voided_balance(self):
        run70 = make_run(DUTY, DATASET, 7000, summary="大促订单汇总")
        voucher, msg = self.sign(run70)
        self.assertIsNotNone(voucher, msg)

        detail = self.report_svc.voucher_detail(int(voucher["id"]))
        self.assertEqual(detail["voucher_no"], f"PZ{voucher['id']:06d}")
        self.assertEqual(detail["amount"], 70.0)
        self.assertEqual(detail["quota_snapshot"], QUOTA)
        self.assertEqual(detail["voided_balance"], 10.0)  # 80 - 70 作废
        self.assertEqual(detail["deducted_run"]["id"], run70)
        self.assertEqual(detail["deducted_run"]["query_summary"], "大促订单汇总")
        self.assertEqual(detail["executor"], DUTY)
        self.assertEqual(detail["business_group"], GROUP)

    # ------------------------------------------------------------------
    # 另一数据集的运行不能借用本委托
    # ------------------------------------------------------------------
    def test_other_dataset_run_rejected(self):
        run30 = make_run(DUTY, OTHER_DATASET, 3000)  # 同执行人、另一数据集
        voucher, msg = self.sign(run30)
        self.assertIsNone(voucher)
        self.assertIn("数据集", msg)
        self.assertEqual(len(self.vouchers()), 0)
        # 委托仍然生效，未被占用
        self.assertEqual(self.delegation_svc.get_entry(self.delegation_id)["status"], "生效中")

    # ------------------------------------------------------------------
    # 其余受控校验
    # ------------------------------------------------------------------
    def test_over_quota_run_rejected(self):
        run90 = make_run(DUTY, DATASET, 9000)  # 费用 90 > 额度 80
        voucher, msg = self.sign(run90)
        self.assertIsNone(voucher)
        self.assertIn("超出", msg)

    def test_executor_mismatch_rejected(self):
        run = make_run("值班员小李", DATASET, 5000)
        voucher, msg = self.sign(run)
        self.assertIsNone(voucher)
        self.assertIn("执行人", msg)

    def test_unconfirmed_run_rejected(self):
        run = make_run(DUTY, DATASET, 5000, status="超时终止")
        voucher, msg = self.sign(run)
        self.assertIsNone(voucher)
        self.assertIn("确证", msg)

    def test_same_run_cannot_be_signed_twice(self):
        run = make_run(DUTY, DATASET, 5000)
        voucher, _ = self.sign(run)
        self.assertIsNotNone(voucher)
        # 业务组再开一份委托，也不能重签同一笔运行
        entry2, _ = self.delegation_svc.create_entry({
            "委托方业务组": GROUP, "被委托执行人": DUTY,
            "指定数据集": DATASET, "可签额度": "100",
        })
        voucher2, msg2 = self.sign(run, delegation_id=int(entry2["id"]))
        self.assertIsNone(voucher2)
        self.assertIn("重复签", msg2)

    def test_revoke_keeps_voucher_snapshot(self):
        run70 = make_run(DUTY, DATASET, 7000)
        voucher, _ = self.sign(run70)
        self.assertIsNotNone(voucher)

        # 撤销原委托
        entry, msg = self.delegation_svc.run_action(self.delegation_id, "撤销委托")
        self.assertIsNotNone(entry, msg)
        self.assertEqual(entry["status"], "已撤销")

        # 复查已签凭证：金额与实际代跑人保持原值
        detail = self.report_svc.voucher_detail(int(voucher["id"]))
        self.assertEqual(detail["amount"], 70.0)
        self.assertEqual(detail["executor"], DUTY)
        self.assertEqual(detail["business_group"], GROUP)
        self.assertEqual(detail["run_id"], run70)

        # 已撤销委托不能再签
        run50 = make_run(DUTY, DATASET, 5000)
        voucher2, msg2 = self.sign(run50)
        self.assertIsNone(voucher2)
        self.assertIn("已撤销", msg2)

    def test_delegation_validation(self):
        # 缺字段
        entry, missing = self.delegation_svc.create_entry({"委托方业务组": GROUP})
        self.assertIsNone(entry)
        self.assertIn("被委托执行人", missing)
        # 额度非数字 / 非正数
        entry, missing = self.delegation_svc.create_entry({
            "委托方业务组": GROUP, "被委托执行人": DUTY,
            "指定数据集": DATASET, "可签额度": "abc",
        })
        self.assertIsNone(entry)
        entry, missing = self.delegation_svc.create_entry({
            "委托方业务组": GROUP, "被委托执行人": DUTY,
            "指定数据集": DATASET, "可签额度": "-5",
        })
        self.assertIsNone(entry)

    def test_delegated_usage_marks_pending_reissue(self):
        run70 = make_run(DUTY, DATASET, 7000)
        run50 = make_run(DUTY, DATASET, 5000)
        self.sign(run70)
        usage = {r["id"]: r for r in self.history_svc.delegated_usage(executor=DUTY)}
        self.assertEqual(usage[run70]["signoff_status"], "已签认")
        self.assertEqual(usage[run70]["fee"], 70.0)
        self.assertEqual(usage[run50]["signoff_status"], "待补办")
        self.assertEqual(usage[run50]["fee"], 50.0)


if __name__ == "__main__":
    unittest.main()
