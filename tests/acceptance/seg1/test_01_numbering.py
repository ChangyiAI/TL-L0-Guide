# -*- coding: utf-8 -*-
"""编号规则（接口规格第 3 节）：任务编号、包编号、文书代码、序号递增；check 对编号的核对（5.5）。"""
import json
import unittest

from seg1_support import (
    ATTACH_REL, DOC_CODES, EXIT_FIELD, EXIT_OK, EXIT_USAGE, MAIN_REL, PACKAGE_ID_RE,
    Seg1TestCase, seal_manifest, sha256_hex,
)

VALID_TASK_IDS = ("qf-001", "ab-000", "abcdefgh-999")
INVALID_TASK_IDS = (
    "q-001",          # 字母只有 1 个
    "abcdefghi-001",  # 字母 9 个
    "QF-001",         # 大写
    "qf-01",          # 数字 2 位
    "qf-0001",        # 数字 4 位
    "qf001",          # 缺连字符
    "q1-001",         # 字母段含数字
    "qf_001",         # 下划线
)
INVALID_DOC_CODES = ("g3", "G1", "xx", "gl", "g01", "")


class TestTaskId(Seg1TestCase):

    def _task_with_main(self, task_id):
        self.setup_task(task_id)
        rel = f".tianlong/work/{task_id}/forms/01_需求确认书_v1.md"
        self.write_file(rel, "# 需求确认书\n")
        return rel

    def test_valid_task_ids_accepted(self):
        """合法任务编号（2～8 个小写字母＋连字符＋3 位数字）pack 成功，包编号合格式"""
        for task_id in VALID_TASK_IDS:
            with self.subTest(task_id=task_id):
                rel = self._task_with_main(task_id)
                pid, path, manifest = self.pack_ok(task=task_id, doc="g0", step="S0-INI", main=rel, attach=())
                self.assertEqual(f"{task_id}-g0-01", pid)
                self.assertRegex(pid, PACKAGE_ID_RE)
                self.assertEqual(task_id, manifest["task_id"])

    def test_invalid_task_ids_rejected_by_pack(self):
        """不合格式的任务编号：pack 退出码 1，不写清单"""
        self.setup_task()
        for task_id in INVALID_TASK_IDS:
            with self.subTest(task_id=task_id):
                res = self.pack(task=task_id)
                self.assertExit(res, EXIT_USAGE)
                self.assertEqual([], self.list_package_files())


class TestDocCode(Seg1TestCase):

    def test_all_doc_codes(self):
        """七个文书代码各自生成 <任务编号>-<代码>-01，doc_type 为对应文书名"""
        self.setup_task()
        for code, name in DOC_CODES.items():
            with self.subTest(code=code):
                pid, path, manifest = self.pack_ok(doc=code)
                self.assertEqual(f"qf-001-{code}-01", pid)
                self.assertEqual(name, manifest["doc_type"])
                self.assertEqual(f"{pid}.json", path.name)

    def test_invalid_doc_codes_rejected(self):
        """文书代码不在表内（含大写、空串）：pack 退出码 1，不写清单"""
        self.setup_task()
        for code in INVALID_DOC_CODES:
            with self.subTest(code=code):
                res = self.pack(doc=code)
                self.assertExit(res, EXIT_USAGE)
                self.assertEqual([], self.list_package_files())


class TestSequence(Seg1TestCase):

    def test_sequence_per_task_and_doc(self):
        """序号按"同一任务、同一文书代码"从 01 递增"""
        self.setup_task("qf-001")
        self.setup_task("qf-002")
        other_main = ".tianlong/work/qf-002/forms/02_规格确认书_v1.md"
        self.write_file(other_main, "# 规格确认书 qf-002\n")

        self.assertEqual("qf-001-g1-01", self.pack_ok(doc="g1")[0])
        self.assertEqual("qf-001-g1-02", self.pack_ok(doc="g1")[0])
        self.assertEqual("qf-001-g0-01", self.pack_ok(doc="g0")[0])
        self.assertEqual("qf-001-g1-03", self.pack_ok(doc="g1")[0])
        self.assertEqual("qf-002-g1-01", self.pack_ok(task="qf-002", main=other_main, attach=())[0])
        self.assertEqual("qf-001-g0-02", self.pack_ok(doc="g0")[0])

    def test_sequence_after_reject(self):
        """退回后重新生成的包取新序号（接口规格 4.3）"""
        self.setup_task()
        pid1 = self.pack_ok()[0]
        self.sign_ok(pid1, "退回", reason="第三节字段不全")
        pid2 = self.pack_ok()[0]
        self.assertEqual("qf-001-g1-02", pid2)


class TestCheckNumbering(Seg1TestCase):
    """check 核对编号：任务编号与包编号符合第 3 节格式（接口规格 5.5）。"""

    def _hand_manifest(self, package_id):
        files = [
            {"path": ATTACH_REL, "sha256": sha256_hex(self.path(ATTACH_REL).read_bytes()), "role": "附件"},
            {"path": MAIN_REL, "sha256": sha256_hex(self.path(MAIN_REL).read_bytes()), "role": "正文"},
        ]
        return seal_manifest({
            "package_id": package_id,
            "doc_type": "规格确认书",
            "task_id": "qf-001",
            "step": "S1-SPD",
            "summary": "手工按规格生成的清单",
            "created_at": "2026-10-03T20:58:11+08:00",
            "created_by": "导引技能",
            "files": files,
        })

    def _write_manifest(self, package_id):
        m = self._hand_manifest(package_id)
        self.write_file(f".tianlong/work/qf-001/packages/{package_id}.json",
                        json.dumps(m, ensure_ascii=False, indent=2) + "\n")

    def test_valid_package_id_passes(self):
        """对照组：包编号合格式的清单，check 通过"""
        self.setup_task()
        self._write_manifest("qf-001-g1-01")
        self.assertExit(self.run_tl("check", "--task", "qf-001", "--json"), EXIT_OK)

    def test_invalid_package_id_detected(self):
        """包编号不合格式：check 退出码 2"""
        for bad in ("qf-001-zz-01", "qf-001-g1-1", "qf-001-g1-001", "qf-001-G1-01", "qf-001-g1"):
            with self.subTest(package_id=bad):
                self.setup_task()
                for p in self.list_package_files():
                    p.unlink()
                self._write_manifest(bad)
                self.assertExit(self.run_tl("check", "--task", "qf-001", "--json"), EXIT_FIELD)

    def test_invalid_task_id_in_progress_detected(self):
        """进度卡里的任务编号不合格式：check 退出码 2"""
        for bad in ("QF-001", "q-001", "qf-01", "qf001"):
            with self.subTest(task_id=bad):
                self.setup_task()
                data = self.valid_progress("qf-001")
                data["task_id"] = bad
                self.write_progress("qf-001", data)
                self.assertExit(self.run_tl("check", "--task", "qf-001", "--json"), EXIT_FIELD)


if __name__ == "__main__":
    unittest.main()
