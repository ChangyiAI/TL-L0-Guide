# -*- coding: utf-8 -*-
"""tl check（接口规格 4.1、4.4、5.5）：进度卡、假设日志、交接卡六栏。编号的核对见 test_01_numbering.py。

通过为 0；有问题为 2。
"""
import shutil
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


class TestCheckAssumptionsRequired(CheckBase):
    """〔裁定 Q18〕假设日志必填：id、step、decision、reason、cost、recorded_by、recorded_at；
    task_item、files、s6_route 选填。cost 的缺省见 open_questions.md 新问题 N1，此处不测缺 cost。"""

    def test_missing_required_field(self):
        """缺任一必填字段（cost 除外）→ 2"""
        for field in ("id", "step", "decision", "reason", "recorded_by", "recorded_at"):
            with self.subTest(field=field):
                item = self.valid_assumption(1)
                del item[field]
                self.write_assumptions("qf-001", [self.valid_assumption(2), item])
                self.assertExit(self.check(), EXIT_FIELD)

    def test_optional_fields_may_be_absent(self):
        """对照组：缺选填字段 task_item、files、s6_route（逐个缺、三个都缺）→ 0"""
        for fields in (("task_item",), ("files",), ("s6_route",), ("task_item", "files", "s6_route")):
            with self.subTest(fields=fields):
                item = self.valid_assumption(1)
                for f in fields:
                    del item[f]
                self.write_assumptions("qf-001", [item])
                self.assertExit(self.check(), EXIT_OK)


class TestCheckProgressFormat(CheckBase):
    """〔裁定 Q23〕进度卡嵌套字段按 4.1 核对：baseline_commit 为 7～40 位小写十六进制；
    current_step 为 S＋一位数字＋连字符＋大写字母；token_holder 须含 role 与 since；task_id 须与目录名一致。"""

    def _check_with(self, change):
        data = self.valid_progress()
        change(data)
        self.write_progress("qf-001", data)
        return self.check()

    def test_baseline_commit(self):
        """baseline_commit：7 位、40 位小写十六进制通过；大写、6 位、41 位、非十六进制、空串 → 2"""
        cases = (("a1b2c3d", EXIT_OK), ("0123456789abcdef0123456789abcdef01234567", EXIT_OK),
                 ("A1B2C3D", EXIT_FIELD), ("a1b2c3", EXIT_FIELD),
                 ("0123456789abcdef0123456789abcdef012345678", EXIT_FIELD),
                 ("g1b2c3d", EXIT_FIELD), ("", EXIT_FIELD))
        for value, expected in cases:
            with self.subTest(baseline_commit=value):
                self.assertExit(self._check_with(lambda d: d.update(baseline_commit=value)), expected)

    def test_current_step(self):
        """current_step：S0-INI、S3-DEV 通过；小写、缺连字符、两位数字、缺字母、下划线 → 2"""
        cases = (("S0-INI", EXIT_OK), ("S3-DEV", EXIT_OK),
                 ("s1-spd", EXIT_FIELD), ("S1-spd", EXIT_FIELD), ("S1SPD", EXIT_FIELD),
                 ("S12-SPD", EXIT_FIELD), ("S1-", EXIT_FIELD), ("S1_SPD", EXIT_FIELD), ("", EXIT_FIELD))
        for value, expected in cases:
            with self.subTest(current_step=value):
                self.assertExit(self._check_with(lambda d: d.update(current_step=value)), expected)

    def test_token_holder_role_and_since(self):
        """token_holder 缺 role 或缺 since → 2"""
        for key in ("role", "since"):
            with self.subTest(missing=key):
                self.assertExit(self._check_with(lambda d: d["token_holder"].pop(key)), EXIT_FIELD)

    def test_task_id_matches_directory(self):
        """task_id 格式合法、但与所在目录名不一致（qf-001 目录里写 qf-002）→ 2"""
        self.assertExit(self._check_with(lambda d: d.update(task_id="qf-002")), EXIT_FIELD)


class TestCheckMissingRecords(CheckBase):
    """〔裁定 Q23〕任务目录下还没有 assumptions.jsonl 或 forms/ 时视为合法。"""

    def _progress_without_form_refs(self):
        # 去掉指向 forms/ 的引用，避免"引用的文件不存在"混入判定
        data = self.valid_progress()
        data["conditions"][0]["readiness"] = "就绪"
        del data["conditions"][0]["exception_ref"]
        del data["steps"][0]["handover"]
        self.write_progress("qf-001", data)

    def test_no_assumptions_file(self):
        """没有 assumptions.jsonl → 0"""
        (self.work_dir("qf-001") / "assumptions.jsonl").unlink()
        self.assertExit(self.check(), EXIT_OK)

    def test_no_forms_dir(self):
        """没有 forms/ 目录 → 0"""
        self._progress_without_form_refs()
        shutil.rmtree(self.work_dir("qf-001") / "forms")
        self.assertExit(self.check(), EXIT_OK)

    def test_neither(self):
        """两者都没有 → 0"""
        self._progress_without_form_refs()
        (self.work_dir("qf-001") / "assumptions.jsonl").unlink()
        shutil.rmtree(self.work_dir("qf-001") / "forms")
        self.assertExit(self.check(), EXIT_OK)


if __name__ == "__main__":
    unittest.main()
