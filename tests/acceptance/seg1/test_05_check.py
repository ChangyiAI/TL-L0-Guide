# -*- coding: utf-8 -*-
"""tl check（接口规格 4.1、4.4、5.5）：进度卡、假设日志、交接卡六栏。编号的核对见 test_01_numbering.py。

通过为 0；有问题为 2。
"""
import unittest

from seg1_support import (
    EXIT_FIELD, EXIT_OK, HANDOVER_COLUMNS, LOW_REASONS, PROGRESS_REQUIRED, Seg1TestCase,
)


class CheckBase(Seg1TestCase):

    def setUp(self):
        super().setUp()
        self.setup_task()

    def check(self, task="qf-001"):
        return self.run_tl("check", "--task", task, "--json")


class TestCheckBaseline(CheckBase):

    def test_valid_task_passes(self):
        """对照组：合法的进度卡、假设日志、交接卡 → 0"""
        self.assertExit(self.check(), EXIT_OK)

    def test_check_is_read_only(self):
        """check 只读：不改任务目录下的任何文件，不碰签字目录"""
        work = self.work_dir("qf-001")
        before = {p: p.read_bytes() for p in work.rglob("*") if p.is_file()}
        self.check()
        after = {p: p.read_bytes() for p in work.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual([], list(self.sign_root.iterdir()))


class TestCheckProgress(CheckBase):
    """进度卡：字段齐全、取值合法（接口规格 4.1）。"""

    def test_missing_required_field(self):
        """缺任一必填字段 → 2"""
        for field in PROGRESS_REQUIRED:
            with self.subTest(field=field):
                data = self.valid_progress()
                del data[field]
                self.write_progress("qf-001", data)
                self.assertExit(self.check(), EXIT_FIELD)

    def test_not_json(self):
        """进度卡不是合法 JSON → 2"""
        self.write_progress("qf-001", '{"task_id": "qf-001", ')
        self.assertExit(self.check(), EXIT_FIELD)

    def test_channel_values(self):
        """channel：CH-FEAT／CH-FIX／CH-MINOR 通过，其他 → 2"""
        for value, expected in (("CH-FEAT", EXIT_OK), ("CH-FIX", EXIT_OK), ("CH-MINOR", EXIT_OK),
                                ("CH-HOTFIX", EXIT_FIELD), ("ch-feat", EXIT_FIELD), ("", EXIT_FIELD)):
            with self.subTest(channel=value):
                data = self.valid_progress()
                data["channel"] = value
                self.write_progress("qf-001", data)
                self.assertExit(self.check(), expected)

    def test_readiness_values(self):
        """conditions[].readiness：就绪／适用未就绪 通过，其他 → 2"""
        for value, expected in (("就绪", EXIT_OK), ("适用未就绪", EXIT_OK),
                                ("未就绪", EXIT_FIELD), ("ready", EXIT_FIELD)):
            with self.subTest(readiness=value):
                data = self.valid_progress()
                data["conditions"][0]["readiness"] = value
                self.write_progress("qf-001", data)
                self.assertExit(self.check(), expected)

    def test_not_ready_requires_exception_ref(self):
        """readiness 为"适用未就绪"时缺 exception_ref → 2"""
        data = self.valid_progress()
        del data["conditions"][0]["exception_ref"]
        self.write_progress("qf-001", data)
        self.assertExit(self.check(), EXIT_FIELD)

    def test_not_ready_empty_exception_ref(self):
        """readiness 为"适用未就绪"时 exception_ref 为空串 → 2（待定 Q12）"""
        data = self.valid_progress()
        data["conditions"][0]["exception_ref"] = ""
        self.write_progress("qf-001", data)
        self.assertExit(self.check(), EXIT_FIELD)

    def test_ready_without_exception_ref(self):
        """readiness 为"就绪"时不带 exception_ref → 0"""
        data = self.valid_progress()
        data["conditions"][0]["readiness"] = "就绪"
        del data["conditions"][0]["exception_ref"]
        self.write_progress("qf-001", data)
        self.assertExit(self.check(), EXIT_OK)

    def test_step_result_values(self):
        """steps[].result：成功／失败／结果不明 通过，其他 → 2"""
        for value, expected in (("成功", EXIT_OK), ("失败", EXIT_OK), ("结果不明", EXIT_OK),
                                ("部分成功", EXIT_FIELD), ("success", EXIT_FIELD)):
            with self.subTest(result=value):
                data = self.valid_progress()
                data["steps"][0]["result"] = value
                self.write_progress("qf-001", data)
                self.assertExit(self.check(), expected)


class TestCheckAssumptions(CheckBase):
    """假设日志：每行格式合法；cost 取 低／中／高，缺省为中；定"低"须附四个固定理由之一（接口规格 4.4）。"""

    def test_cost_values(self):
        """cost：中、高通过；不在三值之内 → 2"""
        for value, expected in (("中", EXIT_OK), ("高", EXIT_OK), ("极低", EXIT_FIELD), ("low", EXIT_FIELD)):
            with self.subTest(cost=value):
                self.write_assumptions("qf-001", [self.valid_assumption(1, cost=value)])
                self.assertExit(self.check(), expected)

    def test_cost_default(self):
        """不写 cost（缺省为中）→ 0"""
        item = self.valid_assumption(1)
        del item["cost"]
        self.write_assumptions("qf-001", [item])
        self.assertExit(self.check(), EXIT_OK)

    def test_low_with_fixed_reason(self):
        """定"低"并附四个固定理由之一 → 0"""
        for reason in LOW_REASONS:
            with self.subTest(low_reason=reason):
                self.write_assumptions("qf-001", [self.valid_assumption(1, cost="低", low_reason=reason)])
                self.assertExit(self.check(), EXIT_OK)

    def test_low_without_reason(self):
        """定"低"但没有 low_reason → 2"""
        self.write_assumptions("qf-001", [self.valid_assumption(1, cost="低")])
        self.assertExit(self.check(), EXIT_FIELD)

    def test_low_with_empty_reason(self):
        """定"低"但 low_reason 为空串 → 2"""
        self.write_assumptions("qf-001", [self.valid_assumption(1, cost="低", low_reason="")])
        self.assertExit(self.check(), EXIT_FIELD)

    def test_low_with_other_reason(self):
        """定"低"但理由不是四个固定值之一 → 2"""
        for reason in ("影响很小", "纯命名、纯格式", "不涉接口、数据与权限", "接口"):
            with self.subTest(low_reason=reason):
                self.write_assumptions("qf-001", [self.valid_assumption(1, cost="低", low_reason=reason)])
                self.assertExit(self.check(), EXIT_FIELD)

    def test_bad_line_among_good(self):
        """多行中只有第 2 行不合法 → 2"""
        self.write_assumptions("qf-001", [
            self.valid_assumption(1),
            self.valid_assumption(2, cost="低"),
            self.valid_assumption(3, cost="高"),
        ])
        self.assertExit(self.check(), EXIT_FIELD)

    def test_line_not_json(self):
        """有一行不是 JSON → 2"""
        self.write_assumptions("qf-001", [self.valid_assumption(1), "not json at all"])
        self.assertExit(self.check(), EXIT_FIELD)


class TestCheckHandover(CheckBase):
    """交接卡：forms/ 下所有 07_交接卡*.md 的六个栏目都不为空（D-206④，接口规格 5.5）。"""

    def test_each_column_blank(self):
        """六个栏目中任一为空 → 2"""
        for col in HANDOVER_COLUMNS:
            with self.subTest(column=col):
                self.write_handover(blank=(col,))
                self.assertExit(self.check(), EXIT_FIELD)

    def test_column_whitespace_only(self):
        """栏目只填空白（含全角空格）→ 2"""
        self.write_handover(whitespace=("未决事项",))
        self.assertExit(self.check(), EXIT_FIELD)

    def test_column_row_missing(self):
        """栏目整行被删 → 2（待定 Q13）"""
        self.write_handover(drop=("令牌移交",))
        self.assertExit(self.check(), EXIT_FIELD)

    def test_all_cards_checked(self):
        """forms/ 下有多张交接卡，只要其中一张有空栏 → 2"""
        self.write_handover(name="07_交接卡_s1.md", blank=("下一步",))
        self.assertExit(self.check(), EXIT_FIELD)

    def test_card_named_without_suffix(self):
        """文件名恰为 07_交接卡.md 的交接卡也要核对"""
        self.write_handover(name="07_交接卡.md", blank=("产出",))
        self.assertExit(self.check(), EXIT_FIELD)

    def test_multiple_valid_cards(self):
        """多张交接卡都填齐 → 0"""
        self.write_handover(name="07_交接卡_s1.md")
        self.write_handover(name="07_交接卡.md")
        self.assertExit(self.check(), EXIT_OK)

    def test_other_forms_ignored(self):
        """非交接卡的文书（如空白的过程审批单）不按交接卡六栏核对 → 0"""
        self.write_file(".tianlong/work/qf-001/forms/06_过程审批单_004.md",
                        "# 过程审批单\n\n| 项 | 填写 |\n| :-- | :-- |\n| 涉及文件 | |\n")
        self.assertExit(self.check(), EXIT_OK)


if __name__ == "__main__":
    unittest.main()
