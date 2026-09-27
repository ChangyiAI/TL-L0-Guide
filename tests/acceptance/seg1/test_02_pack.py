# -*- coding: utf-8 -*-
"""tl pack（接口规格 4.2、5.2）：清单位置与字段、文件指纹、清单指纹算法（测试独立重算）、推送行、参数核对。"""
import json
import unittest

from seg1_support import (
    ATTACH_REL, EXIT_FIELD, EXIT_OK, EXIT_USAGE, FIXED_NOW, HEX64_RE, MAIN_REL, MANIFEST_FIELDS,
    Seg1TestCase, sha256_hex, spec_manifest_sha256,
)

SUMMARY = "规格确认书第 1 版，共十五节"


class TestPackManifest(Seg1TestCase):

    def setUp(self):
        super().setUp()
        self.setup_task()

    def test_manifest_location_and_fields(self):
        """清单写在 .tianlong/work/<任务编号>/packages/<包编号>.json，字段齐全、取值对应参数"""
        pid, path, m = self.pack_ok(summary=SUMMARY)
        self.assertEqual("qf-001-g1-01", pid)
        self.assertEqual(self.packages_dir("qf-001") / "qf-001-g1-01.json", path)
        for field in MANIFEST_FIELDS:
            self.assertIn(field, m, f"清单缺字段 {field}")
        self.assertEqual("qf-001-g1-01", m["package_id"])
        self.assertEqual("规格确认书", m["doc_type"])
        self.assertEqual("qf-001", m["task_id"])
        self.assertEqual("S1-SPD", m["step"])
        self.assertEqual(SUMMARY, m["summary"])
        self.assertEqual(FIXED_NOW, m["created_at"], "created_at 应取 TL_NOW（待定 Q4）")
        self.assertIsInstance(m["created_by"], str)
        self.assertTrue(m["created_by"].strip(), "created_by 不应为空（取值见待定 Q5）")

    def test_file_entries(self):
        """files[]：相对仓库根、正斜杠、sha256 为文件内容的 SHA-256、正文恰好一个、按路径字母序（待定 Q7）"""
        _, _, m = self.pack_ok()
        files = m["files"]
        self.assertEqual([ATTACH_REL, MAIN_REL], [f["path"] for f in files])
        roles = {f["path"]: f["role"] for f in files}
        self.assertEqual({MAIN_REL: "正文", ATTACH_REL: "附件"}, roles)
        for f in files:
            with self.subTest(path=f["path"]):
                self.assertNotIn("\\", f["path"])
                self.assertFalse(f["path"].startswith("/"))
                self.assertRegex(f["sha256"], HEX64_RE)
                self.assertEqual(sha256_hex(self.path(f["path"]).read_bytes()), f["sha256"])

    def test_multiple_attachments_sorted(self):
        """多个附件：files[] 按路径字母序排列，正文恰好一个"""
        self.write_file("docs/z-main.md", "main\n")
        self.write_file("b/two.md", "two\n")
        self.write_file("a/one.md", "one\n")
        self.write_file("docs/a-appendix.md", "appendix\n")
        _, _, m = self.pack_ok(main="docs/z-main.md", attach=("b/two.md", "docs/a-appendix.md", "a/one.md"))
        paths = [f["path"] for f in m["files"]]
        self.assertEqual(["a/one.md", "b/two.md", "docs/a-appendix.md", "docs/z-main.md"], paths)
        self.assertEqual(["正文"], [f["role"] for f in m["files"] if f["path"] == "docs/z-main.md"])
        self.assertEqual(1, sum(1 for f in m["files"] if f["role"] == "正文"))
        self.assertEqual(3, sum(1 for f in m["files"] if f["role"] == "附件"))

    def test_main_only(self):
        """只有正文、没有附件也可以打包"""
        _, _, m = self.pack_ok(attach=())
        self.assertEqual([{"path": MAIN_REL, "role": "正文"}],
                         [{"path": f["path"], "role": f["role"]} for f in m["files"]])

    def test_manifest_sha256_recomputed_independently(self):
        """清单指纹：测试按规格 4.2 独立重算（去两字段、键名排序、紧凑、UTF-8、非 ASCII 不转义），结果一致"""
        _, _, m = self.pack_ok(summary=SUMMARY)
        self.assertRegex(m["manifest_sha256"], HEX64_RE, "manifest_sha256 应为 64 位小写十六进制")
        self.assertEqual(spec_manifest_sha256(m), m["manifest_sha256"])
        self.assertEqual(m["manifest_sha256"][:8], m["fingerprint_code"])

    def test_manifest_sha256_not_ascii_escaped(self):
        """反证：若按"非 ASCII 转义"或"带空格"序列化，得到的指纹与清单不同（确认算法选对了变体）"""
        _, _, m = self.pack_ok(summary=SUMMARY)
        body = {k: v for k, v in m.items() if k not in ("manifest_sha256", "fingerprint_code")}
        escaped = sha256_hex(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        spaced = sha256_hex(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        self.assertNotEqual(escaped, m["manifest_sha256"])
        self.assertNotEqual(spaced, m["manifest_sha256"])

    def test_manifest_file_encoding(self):
        """清单文件 UTF-8、无 BOM、换行 LF（接口规格第 2 节）"""
        _, path, _ = self.pack_ok()
        data = path.read_bytes()
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"), "不得带 BOM")
        self.assertNotIn(b"\r", data, "换行应为 LF")
        data.decode("utf-8")

    def test_push_line(self):
        """屏幕输出推送行：<包编号>｜<文书类型>｜指纹码 <8 位>｜<摘要>"""
        before = set(self.list_package_files())
        res = self.pack(summary=SUMMARY, json_out=False)
        self.assertExit(res, EXIT_OK)
        new = sorted(set(self.list_package_files()) - before)
        m = json.loads(new[0].read_bytes().decode("utf-8"))
        line = f"{m['package_id']}｜规格确认书｜指纹码 {m['fingerprint_code']}｜{SUMMARY}"
        self.assertIn(line, res.stdout.splitlines(), res.describe())

    def test_json_output_is_full_manifest(self):
        """--json 时输出完整清单：〔v0.4 裁定 R8 改〕另含 "ok": true，去掉 ok 后与写入的清单文件内容相同"""
        before = set(self.list_package_files())
        res = self.pack()
        self.assertExit(res, EXIT_OK)
        new = sorted(set(self.list_package_files()) - before)
        out = self.parse_json(res)
        self.assertIs(True, out.pop("ok", None), "〔裁定 R8〕--json 成功时应含 \"ok\": true")
        self.assertEqual(json.loads(new[0].read_bytes().decode("utf-8")), out)

    def test_pack_does_not_touch_sign_dir(self):
        """pack 只写仓库内 .tianlong/work/，不碰签字目录"""
        self.pack_ok()
        self.assertEqual([], list(self.sign_root.iterdir()))


class TestPackRejects(Seg1TestCase):
    """参数不合法、文件不存在、在仓库外：退出码 1，且不写清单。"""

    def setUp(self):
        super().setUp()
        self.setup_task()
        (self.tmp / "outside.md").write_text("outside\n", encoding="utf-8")

    def _assert_rejected(self, res):
        self.assertExit(res, EXIT_USAGE)
        self.assertEqual([], self.list_package_files(), "失败时不得写清单")

    def test_missing_main_file(self):
        """正文文件不存在 → 1"""
        self._assert_rejected(self.pack(main="docs/no-such.md", attach=()))

    def test_missing_attachment_file(self):
        """附件文件不存在 → 1"""
        self._assert_rejected(self.pack(attach=(ATTACH_REL, "docs/no-such.md")))

    def test_file_outside_repo_relative(self):
        """文件在仓库外（相对路径 ../）→ 1"""
        self._assert_rejected(self.pack(attach=("../outside.md",)))
        self._assert_rejected(self.pack(main="../outside.md", attach=()))

    def test_file_outside_repo_absolute(self):
        """文件在仓库外（绝对路径）→ 1"""
        self._assert_rejected(self.pack(attach=(str(self.tmp / "outside.md"),)))

    def test_file_in_packages_dir(self):
        """文件指向 packages/ 目录本身 → 1（待定 Q6）"""
        self.write_file(".tianlong/work/qf-001/packages/notes.md", "x\n")
        res = self.pack(attach=(".tianlong/work/qf-001/packages/notes.md",))
        self.assertExit(res, EXIT_USAGE)
        self.assertEqual([self.packages_dir("qf-001") / "notes.md"],
                         sorted(p for p in self.packages_dir("qf-001").iterdir()))

    def test_missing_required_args(self):
        """缺少 --task／--doc／--step／--main／--summary 任一 → 1"""
        full = {"--task": "qf-001", "--doc": "g1", "--step": "S1-SPD", "--main": MAIN_REL, "--summary": "摘要"}
        for missing in full:
            with self.subTest(missing=missing):
                args = ["pack"]
                for k, v in full.items():
                    if k != missing:
                        args += [k, v]
                args.append("--json")
                self._assert_rejected(self.run_tl(*args))


class TestPackPreconditions(Seg1TestCase):
    """〔裁定 Q24〕pack 要求该任务的进度卡已存在、--step 符合 4.1 的步骤代号格式，否则 1，且不写清单。"""

    def setUp(self):
        super().setUp()
        self.setup_task()

    def test_progress_missing(self):
        """任务目录下没有 progress.json → 1"""
        (self.work_dir("qf-001") / "progress.json").unlink()
        res = self.pack()
        self.assertExit(res, EXIT_USAGE)
        self.assertEqual([], self.list_package_files())

    def test_invalid_step_code(self):
        """--step 不是"S＋一位数字＋连字符＋大写字母" → 1"""
        for step in ("s1-spd", "S1-spd", "S1SPD", "S1", "SPD", "S12-SPD", "S1_SPD", "S1-", ""):
            with self.subTest(step=step):
                res = self.pack(step=step)
                self.assertExit(res, EXIT_USAGE)
                self.assertEqual([], self.list_package_files())


class TestPackRuledV3(Seg1TestCase):
    """接口规格 v0.3 第 9 节裁定补的 pack 用例。"""

    def setUp(self):
        super().setUp()
        self.setup_task()

    def test_step_digit_range(self):
        """〔裁定 N3〕--step 的数字只取 0～7：S7-OPS 成功；S8-OPS、S9-OPS → 1，不写清单"""
        self.assertEqual("S7-OPS", self.pack_ok(step="S7-OPS")[2]["step"])
        for p in self.list_package_files():
            p.unlink()
        for step in ("S8-OPS", "S9-OPS"):
            with self.subTest(step=step):
                self.assertExit(self.pack(step=step), EXIT_USAGE)
                self.assertEqual([], self.list_package_files())

    def test_created_by_default_tl(self):
        """〔裁定 N4／Q5〕created_by 缺省为 tl"""
        self.assertEqual("tl", self.pack_ok()[2]["created_by"])

    def test_file_in_other_task_packages_dir(self):
        """〔裁定 N4／Q6〕文件指向别的任务的 packages/ 目录 → 1，不写清单"""
        self.write_file(".tianlong/work/qf-002/packages/notes.md", "x\n")
        res = self.pack(attach=(".tianlong/work/qf-002/packages/notes.md",))
        self.assertExit(res, EXIT_USAGE)
        self.assertEqual([], self.list_package_files())

    def test_invalid_progress_card(self):
        """〔裁定 N5〕进度卡存在但不合法（通道取值错、缺必填字段、不是 JSON）→ 2，不写清单"""
        bad = {}
        d = self.valid_progress()
        d["channel"] = "CH-HOTFIX"
        bad["通道取值错"] = d
        d = self.valid_progress()
        del d["token_holder"]
        bad["缺 token_holder"] = d
        bad["不是 JSON"] = "{not json"
        for name, data in bad.items():
            with self.subTest(case=name):
                self.write_progress("qf-001", data)
                self.assertExit(self.pack(), EXIT_FIELD)
                self.assertEqual([], self.list_package_files())


if __name__ == "__main__":
    unittest.main()
