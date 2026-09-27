# -*- coding: utf-8 -*-
"""tl status（接口规格 5.1）：退出码 0；进度卡不合法时为 2。签字状态：未签／已确认／退回／签后文件已变。"""
import unittest

from seg1_support import ATTACH_REL, EXIT_FIELD, EXIT_OK, MAIN_REL, Seg1TestCase


class StatusBase(Seg1TestCase):

    def setUp(self):
        super().setUp()
        self.setup_task()

    def lines_with(self, text, needle):
        return [line for line in text.splitlines() if needle in line]


class TestStatusExitCode(StatusBase):

    def test_valid(self):
        """进度卡合法：不带 --task、带 --task 都为 0"""
        self.assertExit(self.run_tl("status", "--json"), EXIT_OK)
        self.assertExit(self.run_tl("status", "--task", "qf-001", "--json"), EXIT_OK)
        self.assertExit(self.run_tl("status"), EXIT_OK)
        self.assertExit(self.run_tl("status", "--task", "qf-001"), EXIT_OK)

    def test_invalid_progress_with_task(self):
        """进度卡不合法（缺字段、通道取值错、不是 JSON）：status --task → 2"""
        bad_cases = {}
        d = self.valid_progress()
        del d["token_holder"]
        bad_cases["缺 token_holder"] = d
        d = self.valid_progress()
        d["channel"] = "CH-HOTFIX"
        bad_cases["通道取值错"] = d
        bad_cases["不是 JSON"] = "{not json"
        for name, data in bad_cases.items():
            with self.subTest(case=name):
                self.write_progress("qf-001", data)
                self.assertExit(self.run_tl("status", "--task", "qf-001", "--json"), EXIT_FIELD)
                self.assertExit(self.run_tl("status", "--task", "qf-001"), EXIT_FIELD)

    def test_invalid_progress_listing(self):
        """列全部任务时，其中一个任务的进度卡不合法 → 2（待定 Q15）"""
        self.setup_task("qf-002")
        d = self.valid_progress("qf-002")
        d["channel"] = "CH-HOTFIX"
        self.write_progress("qf-002", d)
        self.assertExit(self.run_tl("status", "--json"), EXIT_FIELD)

    def test_status_is_read_only(self):
        """status 只读"""
        pid = self.pack_ok()[0]
        self.sign_ok(pid, "确认")
        work = self.work_dir("qf-001")
        before = {p: p.read_bytes() for p in work.rglob("*") if p.is_file()}
        sig = self.sig_bytes()
        self.run_tl("status", "--task", "qf-001")
        self.assertEqual(before, {p: p.read_bytes() for p in work.rglob("*") if p.is_file()})
        self.assertEqual(sig, self.sig_bytes())


class TestStatusContent(StatusBase):
    """带 --task 的中文摘要：每个待签包的包编号、指纹码、签字状态，以及包内文件清单。
    判定方式（待定 Q14）：签字状态与包编号出现在同一行。"""

    def setUp(self):
        super().setUp()
        self.pid, _, self.m = self.pack_ok()

    def status_text(self):
        res = self.run_tl("status", "--task", "qf-001")
        self.assertExit(res, EXIT_OK)
        return res.stdout

    def assertStateOnPackageLine(self, state):
        text = self.status_text()
        lines = self.lines_with(text, self.pid)
        self.assertTrue(lines, f"输出中找不到包编号 {self.pid}\n{text}")
        self.assertTrue(any(state in line for line in lines),
                        f"包编号所在行应显示签字状态“{state}”\n{text}")
        return text

    def test_package_info_listed(self):
        """列出包编号、文书类型、指纹码，以及包内文件"""
        text = self.status_text()
        self.assertIn(self.pid, text)
        self.assertIn("规格确认书", text)
        self.assertIn(self.m["fingerprint_code"], text)
        self.assertIn(MAIN_REL, text)
        self.assertIn(ATTACH_REL, text)

    def test_state_unsigned(self):
        """未签字 → “未签”"""
        self.assertStateOnPackageLine("未签")

    def test_state_confirmed(self):
        """已确认 → “已确认”"""
        self.sign_ok(self.pid, "确认")
        self.assertStateOnPackageLine("已确认")

    def test_state_rejected(self):
        """退回 → “退回”"""
        self.sign_ok(self.pid, "退回", reason="重做")
        self.assertStateOnPackageLine("退回")

    def test_state_changed_after_sign(self):
        """已确认后改附件 → “签后文件已变”"""
        self.sign_ok(self.pid, "确认")
        self.modify(ATTACH_REL)
        self.assertStateOnPackageLine("签后文件已变")


if __name__ == "__main__":
    unittest.main()
