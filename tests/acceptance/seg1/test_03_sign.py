# -*- coding: utf-8 -*-
"""tl sign（接口规格 4.3、5.3）：写入内容、链式校验、六步顺序。

六步：①重算指纹不符→3 ②已签→5 ③退回无理由→1 ④签字根目录不存在→1（子目录自动建）⑤链条坏→5 ⑥追加并回显。
每个拒签用例都核对"不写入"：签字记录前后字节完全相同（或仍不存在）。
"""
import json
import unittest

from seg1_support import (
    ATTACH_REL, EXIT_CORRUPT, EXIT_FINGERPRINT, EXIT_OK, EXIT_USAGE, FIXED_NOW, MAIN_REL,
    REPO_NAME, SIGNATURE_FIELDS, ZERO64, Seg1TestCase, sha256_hex,
)


class SignBase(Seg1TestCase):

    def setUp(self):
        super().setUp()
        self.setup_task()
        self.pid, self.manifest_path, self.m = self.pack_ok()

    def assertRefused(self, res, code, before):
        self.assertExit(res, code)
        self.assertEqual(before, self.sig_bytes(), "拒签时签字记录不得有任何改动")


class TestSignWrites(SignBase):

    def test_confirm_writes_one_line(self):
        """确认：追加一行，七个字段取值正确，第一行 prev_sha256 为 64 个 0"""
        self.sign_ok(self.pid, "确认")
        self.assertTrue(self.sig_file.is_file(), f"签字记录应写在 <TL_SIGN_DIR>/{REPO_NAME}/signatures.jsonl")
        recs = self.sig_records()
        self.assertEqual(1, len(recs))
        r = recs[0]
        for f in SIGNATURE_FIELDS:
            self.assertIn(f, r, f"签字记录缺字段 {f}")
        self.assertEqual(self.pid, r["package_id"])
        self.assertEqual("已确认", r["decision"])
        self.assertEqual(self.m["fingerprint_code"], r["fingerprint_code"])
        self.assertEqual("", r["reason"])
        self.assertEqual(FIXED_NOW, r["signed_at"])
        self.assertEqual("APR:Fred", r["signer"])
        self.assertEqual(ZERO64, r["prev_sha256"])

    def test_reject_with_reason(self):
        """退回并附理由：decision 为"退回"，reason 为所给理由"""
        self.sign_ok(self.pid, "退回", reason="第三节数据字段不全")
        r = self.sig_records()[0]
        self.assertEqual("退回", r["decision"])
        self.assertEqual("第三节数据字段不全", r["reason"])
        self.assertEqual(self.m["fingerprint_code"], r["fingerprint_code"])

    def test_english_decisions(self):
        """决定也接受英文写法 confirm、reject"""
        self.sign_ok(self.pid, "confirm")
        pid2 = self.pack_ok()[0]
        self.sign_ok(pid2, "reject", reason="needs rework")
        recs = self.sig_records()
        self.assertEqual(["已确认", "退回"], [r["decision"] for r in recs])

    def test_chain_prev_sha256(self):
        """链式校验：每行 prev_sha256 为上一行原文（不含行尾换行）的 SHA-256"""
        pids = [self.pid, self.pack_ok()[0], self.pack_ok(doc="g0")[0]]
        self.sign_ok(pids[0], "确认")
        self.sign_ok(pids[1], "退回", reason="重做")
        self.sign_ok(pids[2], "确认")
        lines = self.sig_lines()
        self.assertEqual(3, len(lines))
        recs = [json.loads(x.decode("utf-8")) for x in lines]
        self.assertEqual(pids, [r["package_id"] for r in recs])
        self.assertEqual(ZERO64, recs[0]["prev_sha256"])
        self.assertEqual(sha256_hex(lines[0]), recs[1]["prev_sha256"])
        self.assertEqual(sha256_hex(lines[1]), recs[2]["prev_sha256"])

    def test_old_lines_unchanged(self):
        """只追加、不改旧行：第二次签字后，第一行字节不变"""
        self.sign_ok(self.pid, "确认")
        first = self.sig_bytes()
        self.sign_ok(self.pack_ok()[0], "确认")
        self.assertTrue(self.sig_bytes().startswith(first))

    def test_file_encoding(self):
        """签字记录 UTF-8、无 BOM、换行 LF、每行一个 JSON 对象"""
        self.sign_ok(self.pid, "退回", reason="中文理由")
        data = self.sig_bytes()
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", data)
        for line in self.sig_lines():
            self.assertIsInstance(json.loads(line.decode("utf-8")), dict)

    def test_echo(self):
        """屏幕回显写入的内容（至少含包编号、决定与指纹码；待定 Q17）"""
        res = self.sign(self.pid, "确认", json_out=False)
        self.assertExit(res, EXIT_OK)
        self.assertIn(self.pid, res.stdout, res.describe())
        self.assertIn(self.m["fingerprint_code"], res.stdout, res.describe())
        self.assertIn("已确认", res.stdout, res.describe())

    def test_sign_does_not_modify_repo(self):
        """sign 不改仓库内的清单与受审文件"""
        before = {p: self.path(p).read_bytes() for p in (MAIN_REL, ATTACH_REL)}
        manifest_before = self.manifest_path.read_bytes()
        self.sign_ok(self.pid, "确认")
        self.assertEqual(manifest_before, self.manifest_path.read_bytes())
        for p, data in before.items():
            self.assertEqual(data, self.path(p).read_bytes())


class TestSignStep1Fingerprint(SignBase):
    """第 1 步：重新计算每个文件的指纹和清单指纹，任何一项不符 → 3，不写入。"""

    def test_main_changed(self):
        """正文被改 → 3"""
        self.modify(MAIN_REL)
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_FINGERPRINT, None)

    def test_attachment_changed(self):
        """附件被改 → 3"""
        self.modify(ATTACH_REL)
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_FINGERPRINT, None)

    def test_attachment_deleted(self):
        """附件已不存在 → 3"""
        self.path(ATTACH_REL).unlink()
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_FINGERPRINT, None)

    def test_manifest_edited(self):
        """清单本身被改（改摘要、未改指纹）→ 3"""
        m = json.loads(self.manifest_path.read_bytes().decode("utf-8"))
        m["summary"] = "偷偷改过的摘要"
        self.manifest_path.write_bytes((json.dumps(m, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_FINGERPRINT, None)

    def test_reject_also_checks_fingerprint(self):
        """退回也要先核对指纹：文件被改后签退回 → 3"""
        self.modify(MAIN_REL)
        self.assertRefused(self.sign(self.pid, "退回", reason="理由"), EXIT_FINGERPRINT, None)


class TestSignStep2AlreadySigned(SignBase):
    """第 2 步：同一个包只能签一次 → 5，不写入。"""

    def test_confirm_twice(self):
        """已确认的包再签确认 → 5"""
        self.sign_ok(self.pid, "确认")
        before = self.sig_bytes()
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_CORRUPT, before)

    def test_reject_then_confirm(self):
        """已退回的包再签确认 → 5"""
        self.sign_ok(self.pid, "退回", reason="重做")
        before = self.sig_bytes()
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_CORRUPT, before)

    def test_confirm_then_reject(self):
        """已确认的包再签退回（附理由）→ 5"""
        self.sign_ok(self.pid, "确认")
        before = self.sig_bytes()
        self.assertRefused(self.sign(self.pid, "退回", reason="反悔"), EXIT_CORRUPT, before)


class TestSignStep3RejectReason(SignBase):
    """第 3 步：退回但没给理由 → 1，不写入。"""

    def test_reject_without_reason(self):
        """退回不带 --reason → 1"""
        self.assertRefused(self.sign(self.pid, "退回"), EXIT_USAGE, None)

    def test_reject_english_without_reason(self):
        """reject 不带 --reason → 1"""
        self.assertRefused(self.sign(self.pid, "reject"), EXIT_USAGE, None)

    def test_reject_empty_reason(self):
        """退回带空理由 --reason "" → 1（待定 Q8）"""
        self.assertRefused(self.sign(self.pid, "退回", reason=""), EXIT_USAGE, None)


class TestSignStep4SignDir(SignBase):
    """第 4 步：<仓库名> 子目录不存在则自动创建；签字根目录本身不存在 → 1。"""

    def test_repo_subdir_auto_created(self):
        """签字根目录存在、<仓库名> 子目录不存在：自动创建并写入"""
        self.assertFalse((self.sign_root / REPO_NAME).exists())
        self.sign_ok(self.pid, "确认")
        self.assertTrue(self.sig_file.is_file())

    def test_sign_root_missing(self):
        """签字根目录（TL_SIGN_DIR）不存在 → 1，且不创建任何目录"""
        missing = self.tmp / "no-such-sign-root"
        res = self.sign(self.pid, "确认", sign_dir=missing)
        self.assertExit(res, EXIT_USAGE)
        self.assertFalse(missing.exists(), "签字根目录不存在时不得自行创建")


class TestSignStep5Chain(SignBase):
    """第 5 步：校验现有各行的链条，失败 → 5，不写入。"""

    def _three_signed(self):
        pids = [self.pack_ok()[0], self.pack_ok()[0], self.pack_ok(doc="g0")[0]]
        for p in pids:
            self.sign_ok(p, "确认")
        return pids

    def test_middle_line_tampered(self):
        """签字记录中间一行被改 → 5"""
        self._three_signed()
        self.tamper_sig_line(1)
        before = self.sig_bytes()
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_CORRUPT, before)

    def test_first_line_tampered(self):
        """第一行被改（后面有行）→ 5"""
        self._three_signed()
        self.tamper_sig_line(0)
        before = self.sig_bytes()
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_CORRUPT, before)

    def test_first_line_prev_not_zero(self):
        """第一行 prev_sha256 不是 64 个 0 → 5"""
        self._three_signed()
        # 只剩第一行，并把它的 prev_sha256 改掉
        first = json.loads(self.sig_lines()[0].decode("utf-8"))
        first["prev_sha256"] = "f" * 64
        self.sig_file.write_bytes(json.dumps(first, ensure_ascii=False).encode("utf-8") + b"\n")
        before = self.sig_bytes()
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_CORRUPT, before)

    def test_line_deleted(self):
        """中间一行被删 → 5"""
        self._three_signed()
        lines = self.sig_lines()
        self.sig_file.write_bytes(lines[0] + b"\n" + lines[2] + b"\n")
        before = self.sig_bytes()
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_CORRUPT, before)

    def test_line_not_json(self):
        """有一行不是 JSON（格式错误）→ 5（待定 Q11）"""
        self._three_signed()
        with open(self.sig_file, "ab") as f:
            f.write(b"this is not json\n")
        before = self.sig_bytes()
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_CORRUPT, before)


class TestSignOrder(SignBase):
    """六步的先后顺序：同时触犯两步时，以先核对的一步为准。"""

    def test_step1_before_step2(self):
        """已签 ＋ 文件被改 → 3（第 1 步先于第 2 步）"""
        self.sign_ok(self.pid, "确认")
        self.modify(MAIN_REL)
        before = self.sig_bytes()
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_FINGERPRINT, before)

    def test_step2_before_step3(self):
        """已签 ＋ 退回无理由 → 5（第 2 步先于第 3 步）"""
        self.sign_ok(self.pid, "确认")
        before = self.sig_bytes()
        self.assertRefused(self.sign(self.pid, "退回"), EXIT_CORRUPT, before)

    def test_step1_before_step5(self):
        """链条坏 ＋ 文件被改 → 3（第 1 步先于第 5 步）"""
        p2, p3 = self.pack_ok()[0], self.pack_ok()[0]
        self.sign_ok(p2, "确认")
        self.sign_ok(p3, "确认")
        self.tamper_sig_line(0)
        self.modify(MAIN_REL)
        before = self.sig_bytes()
        self.assertRefused(self.sign(self.pid, "确认"), EXIT_FINGERPRINT, before)

    def test_step3_before_step5(self):
        """链条坏 ＋ 退回无理由 → 1（第 3 步先于第 5 步）"""
        p2, p3 = self.pack_ok()[0], self.pack_ok()[0]
        self.sign_ok(p2, "确认")
        self.sign_ok(p3, "确认")
        self.tamper_sig_line(0)
        before = self.sig_bytes()
        self.assertRefused(self.sign(self.pid, "退回"), EXIT_USAGE, before)

    def test_step1_before_step4(self):
        """签字根目录不存在 ＋ 文件被改 → 3（第 1 步先于第 4 步）"""
        self.modify(MAIN_REL)
        missing = self.tmp / "no-such-sign-root"
        res = self.sign(self.pid, "确认", sign_dir=missing)
        self.assertExit(res, EXIT_FINGERPRINT)
        self.assertFalse(missing.exists())


class TestSignUsage(SignBase):

    def test_invalid_decision_word(self):
        """决定不是 确认／退回／confirm／reject → 1"""
        for word in ("同意", "ok", "yes"):
            with self.subTest(word=word):
                self.assertRefused(self.sign(self.pid, word, reason="理由"), EXIT_USAGE, None)

    def test_missing_decision(self):
        """缺少决定参数 → 1"""
        self.assertRefused(self.run_tl("sign", self.pid, "--json"), EXIT_USAGE, None)


if __name__ == "__main__":
    unittest.main()
