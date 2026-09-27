# -*- coding: utf-8 -*-
"""tl verify（接口规格 5.4）：五项核对、退出码、按任务核对。

五项依次：①链条完好（否则 5）②有且只有一条签字（没有 4，多于一条 5）③决定为"已确认"（退回 4）
④签字里的指纹码与清单一致（否则 4）⑤清单文件当前指纹与清单一致（否则 3）。
"""
import json
import unittest

from seg1_support import (
    ATTACH_REL, EXIT_CORRUPT, EXIT_FINGERPRINT, EXIT_OK, EXIT_UNSIGNED, EXIT_USAGE, MAIN_REL,
    Seg1TestCase, seal_manifest, sha256_hex, spec_manifest_sha256,
)


class VerifyBase(Seg1TestCase):

    def setUp(self):
        super().setUp()
        self.setup_task()
        self.pid, self.manifest_path, self.m = self.pack_ok()

    def verify(self, pid=None):
        return self.run_tl("verify", pid or self.pid, "--json")

    def verify_task(self, task="qf-001"):
        return self.run_tl("verify", "--task", task, "--json")


class TestVerifySingle(VerifyBase):

    def test_confirmed_passes(self):
        """已确认、文件未变 → 0"""
        self.sign_ok(self.pid, "确认")
        self.assertExit(self.verify(), EXIT_OK)

    def test_verify_is_read_only(self):
        """verify 只读：签字记录与清单不变"""
        self.sign_ok(self.pid, "确认")
        sig, man = self.sig_bytes(), self.manifest_path.read_bytes()
        self.verify()
        self.assertEqual(sig, self.sig_bytes())
        self.assertEqual(man, self.manifest_path.read_bytes())

    def test_unsigned_no_signature_file(self):
        """未签字（签字记录文件尚不存在）→ 4（待定 Q10）"""
        self.assertExit(self.verify(), EXIT_UNSIGNED)

    def test_unsigned_with_other_signatures(self):
        """未签字（签字记录里只有别的包）→ 4"""
        other = self.pack_ok()[0]
        self.sign_ok(other, "确认")
        self.assertExit(self.verify(), EXIT_UNSIGNED)

    def test_rejected(self):
        """已退回 → 4"""
        self.sign_ok(self.pid, "退回", reason="重做")
        self.assertExit(self.verify(), EXIT_UNSIGNED)


class TestVerifyForgery(VerifyBase):
    """伪造签字判无效（门1 确认单实测③"伪造签字"）。"""

    def test_forged_wrong_fingerprint(self):
        """在别处写一条格式正确、链条正确、指纹码不符的签字 → 4"""
        self.append_sig_record(self.forged_record(self.pid, "deadbeef"))
        self.assertExit(self.verify(), EXIT_UNSIGNED)

    def test_forged_after_other_signatures(self):
        """已有合法签字的记录末尾追加一条指纹不符的伪造签字 → 4"""
        self.sign_ok(self.pack_ok()[0], "确认")
        self.append_sig_record(self.forged_record(self.pid, "0badf00d"))
        self.assertExit(self.verify(), EXIT_UNSIGNED)

    def test_forged_broken_chain(self):
        """伪造一条指纹码正确、但链条不对的签字 → 5"""
        self.sign_ok(self.pack_ok()[0], "确认")
        rec = self.forged_record(self.pid, self.m["fingerprint_code"])
        rec["prev_sha256"] = "0" * 64
        self.append_sig_record(rec, chain_ok=False)
        self.assertExit(self.verify(), EXIT_CORRUPT)

    def test_manifest_rebuilt_after_signing(self):
        """签后改附件并按规格重做清单（文件指纹、清单指纹都自洽）→ 签字里的指纹码对不上 → 4"""
        self.sign_ok(self.pid, "确认")
        self.modify(ATTACH_REL)
        m = json.loads(self.manifest_path.read_bytes().decode("utf-8"))
        for f in m["files"]:
            f["sha256"] = sha256_hex(self.path(f["path"]).read_bytes())
        m = seal_manifest(m)
        self.assertNotEqual(self.m["fingerprint_code"], m["fingerprint_code"])
        self.manifest_path.write_bytes((json.dumps(m, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        self.assertExit(self.verify(), EXIT_UNSIGNED)

    def test_duplicate_signature(self):
        """同一个包出现两条签字（链条正确）→ 5"""
        self.sign_ok(self.pid, "确认")
        self.append_sig_record(self.forged_record(self.pid, self.m["fingerprint_code"]))
        self.assertExit(self.verify(), EXIT_CORRUPT)

    def test_duplicate_confirm_and_reject(self):
        """同一个包先退回、又被追加一条"已确认"（链条正确）→ 5"""
        self.sign_ok(self.pid, "退回", reason="重做")
        self.append_sig_record(self.forged_record(self.pid, self.m["fingerprint_code"]))
        self.assertExit(self.verify(), EXIT_CORRUPT)


class TestVerifyChain(VerifyBase):
    """签字记录中间一行被改：链式校验失败 → 5，与所核对的包是否在那一行无关。"""

    def setUp(self):
        super().setUp()
        self.pids = [self.pid, self.pack_ok()[0], self.pack_ok(doc="g0")[0]]
        for p in self.pids:
            self.sign_ok(p, "确认")

    def test_all_pass_before_tamper(self):
        """对照组：未改动时三个包都通过"""
        for p in self.pids:
            with self.subTest(package_id=p):
                self.assertExit(self.verify(p), EXIT_OK)

    def test_middle_line_tampered(self):
        """中间一行被改 → 核对任何一个包都为 5"""
        self.tamper_sig_line(1)
        for p in self.pids:
            with self.subTest(package_id=p):
                self.assertExit(self.verify(p), EXIT_CORRUPT)

    def test_middle_line_deleted(self):
        """中间一行被删 → 5"""
        lines = self.sig_lines()
        self.sig_file.write_bytes(lines[0] + b"\n" + lines[2] + b"\n")
        self.assertExit(self.verify(self.pids[0]), EXIT_CORRUPT)

    def test_malformed_line(self):
        """有一行不是 JSON → 5（待定 Q11）"""
        with open(self.sig_file, "ab") as f:
            f.write(b"{broken\n")
        self.assertExit(self.verify(self.pids[0]), EXIT_CORRUPT)

    def test_chain_checked_before_everything(self):
        """链条坏 ＋ 被核对的包未签字 → 5（第一项先于第二项）"""
        new = self.pack_ok()[0]
        self.tamper_sig_line(1)
        self.assertExit(self.verify(new), EXIT_CORRUPT)


class TestVerifyAfterSignChange(VerifyBase):
    """签后改件即作废（门1 确认单实测③"签后改件"）：→ 3。"""

    def setUp(self):
        super().setUp()
        self.sign_ok(self.pid, "确认")

    def test_main_changed(self):
        """签后改正文 → 3"""
        self.modify(MAIN_REL)
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_attachment_changed(self):
        """签后改附件 → 3"""
        self.modify(ATTACH_REL)
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_attachment_replaced_same_size(self):
        """签后把附件替换为等长的不同内容 → 3"""
        p = self.path(ATTACH_REL)
        data = bytearray(p.read_bytes())
        data[0] = ord("X") if data[0] != ord("X") else ord("Y")
        p.write_bytes(bytes(data))
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_attachment_deleted(self):
        """签后删附件 → 3"""
        self.path(ATTACH_REL).unlink()
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_restored_passes_again(self):
        """文件改回原样 → 重新通过（核对只看当前内容与清单是否一致）"""
        p = self.path(MAIN_REL)
        original = p.read_bytes()
        self.modify(MAIN_REL)
        self.assertExit(self.verify(), EXIT_FINGERPRINT)
        p.write_bytes(original)
        self.assertExit(self.verify(), EXIT_OK)


class TestVerifyOrder(VerifyBase):
    """五项核对的先后：前一项不通过即以前一项的退出码为准。"""

    def test_rejected_and_changed(self):
        """已退回 ＋ 文件被改 → 4（第三项先于第五项）"""
        self.sign_ok(self.pid, "退回", reason="重做")
        self.modify(MAIN_REL)
        self.assertExit(self.verify(), EXIT_UNSIGNED)

    def test_unsigned_and_changed(self):
        """未签 ＋ 文件被改 → 4（第二项先于第五项）"""
        self.modify(MAIN_REL)
        self.assertExit(self.verify(), EXIT_UNSIGNED)

    def test_forged_and_changed(self):
        """伪造指纹码 ＋ 文件被改 → 4（第四项先于第五项）"""
        self.append_sig_record(self.forged_record(self.pid, "deadbeef"))
        self.modify(MAIN_REL)
        self.assertExit(self.verify(), EXIT_UNSIGNED)

    def test_duplicate_and_changed(self):
        """重复签字 ＋ 文件被改 → 5（第二项先于第五项）"""
        self.sign_ok(self.pid, "确认")
        self.append_sig_record(self.forged_record(self.pid, self.m["fingerprint_code"]))
        self.modify(MAIN_REL)
        self.assertExit(self.verify(), EXIT_CORRUPT)


class TestVerifyTask(VerifyBase):
    """verify --task：任一个包不通过即取最严重的退出码（裁定 Q9：严重程度 5 ＞ 3 ＞ 4）。"""

    def setUp(self):
        super().setUp()
        self.write_file("docs/g0-main.md", "需求确认书正文\n")
        self.pid2 = self.pack_ok(doc="g0", step="S0-INI", main="docs/g0-main.md", attach=())[0]

    def test_all_confirmed(self):
        """全部已确认、文件未变 → 0"""
        self.sign_ok(self.pid, "确认")
        self.sign_ok(self.pid2, "确认")
        self.assertExit(self.verify_task(), EXIT_OK)

    def test_one_unsigned(self):
        """一个已确认、一个未签 → 4"""
        self.sign_ok(self.pid, "确认")
        self.assertExit(self.verify_task(), EXIT_UNSIGNED)

    def test_one_rejected(self):
        """一个已确认、一个退回 → 4"""
        self.sign_ok(self.pid, "确认")
        self.sign_ok(self.pid2, "退回", reason="重做")
        self.assertExit(self.verify_task(), EXIT_UNSIGNED)

    def test_one_changed(self):
        """都已确认、其中一个签后改件 → 3"""
        self.sign_ok(self.pid, "确认")
        self.sign_ok(self.pid2, "确认")
        self.modify("docs/g0-main.md")
        self.assertExit(self.verify_task(), EXIT_FINGERPRINT)

    def test_chain_broken(self):
        """链条坏 → 5"""
        self.sign_ok(self.pid, "确认")
        self.sign_ok(self.pid2, "确认")
        self.tamper_sig_line(0)
        self.assertExit(self.verify_task(), EXIT_CORRUPT)

    def test_mixed_changed_and_unsigned(self):
        """〔裁定 Q9〕一个签后改件（3）、一个未签（4）→ 3（严重程度 5 ＞ 3 ＞ 4）"""
        self.sign_ok(self.pid, "确认")
        self.modify(MAIN_REL)
        self.assertExit(self.verify_task(), EXIT_FINGERPRINT)

    def test_other_task_not_counted(self):
        """别的任务的包不影响本任务的核对结果"""
        self.setup_task("qf-002")
        self.write_file("docs/qf-002.md", "x\n")
        self.pack_ok(task="qf-002", main="docs/qf-002.md", attach=())  # 未签
        self.sign_ok(self.pid, "确认")
        self.sign_ok(self.pid2, "确认")
        self.assertExit(self.verify_task("qf-001"), EXIT_OK)
        self.assertExit(self.verify_task("qf-002"), EXIT_UNSIGNED)


class TestVerifyManifestRecompute(VerifyBase):
    """〔裁定 Q20〕verify 重新计算清单指纹，与清单里的 manifest_sha256 不一致 → 3。
    核对顺序：链条（5）→ 重算清单指纹（3）→ 签字条数（4／5）→ 决定 → 指纹码 → 文件。"""

    def edit_manifest(self, change):
        """改清单内容，但不动 manifest_sha256、fingerprint_code 两个字段，也不动受审文件。"""
        m = json.loads(self.manifest_path.read_bytes().decode("utf-8"))
        change(m)
        self.manifest_path.write_bytes((json.dumps(m, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    def test_summary_edited_after_sign(self):
        """签后只改清单的 summary → 3"""
        self.sign_ok(self.pid, "确认")
        self.edit_manifest(lambda m: m.update(summary="签后偷偷改的摘要"))
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_step_edited_after_sign(self):
        """签后只改清单的 step → 3"""
        self.sign_ok(self.pid, "确认")
        self.edit_manifest(lambda m: m.update(step="S4-REL"))
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_roles_swapped_after_sign(self):
        """签后对调正文与附件的角色 → 3"""
        self.sign_ok(self.pid, "确认")

        def swap(m):
            for f in m["files"]:
                f["role"] = "附件" if f["role"] == "正文" else "正文"
        self.edit_manifest(swap)
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_file_entry_removed_after_sign(self):
        """签后从清单里删掉附件这一项（剩下的文件都未改）→ 3"""
        self.sign_ok(self.pid, "确认")
        self.edit_manifest(lambda m: m.update(files=[f for f in m["files"] if f["role"] == "正文"]))
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_unsigned_and_manifest_edited(self):
        """未签 ＋ 清单被改 → 3（重算清单指纹先于签字条数）"""
        self.edit_manifest(lambda m: m.update(summary="改过"))
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_rejected_and_manifest_edited(self):
        """已退回 ＋ 清单被改 → 3（重算清单指纹先于决定核对）"""
        self.sign_ok(self.pid, "退回", reason="重做")
        self.edit_manifest(lambda m: m.update(summary="改过"))
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_duplicate_and_manifest_edited(self):
        """重复签字 ＋ 清单被改 → 3（重算清单指纹先于签字条数）"""
        self.sign_ok(self.pid, "确认")
        self.append_sig_record(self.forged_record(self.pid, self.m["fingerprint_code"]))
        self.edit_manifest(lambda m: m.update(summary="改过"))
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_chain_broken_and_manifest_edited(self):
        """链条坏 ＋ 清单被改 → 5（链条先于重算清单指纹）"""
        self.sign_ok(self.pid, "确认")
        self.sign_ok(self.pack_ok()[0], "确认")
        self.tamper_sig_line(0)
        self.edit_manifest(lambda m: m.update(summary="改过"))
        self.assertExit(self.verify(), EXIT_CORRUPT)

    def test_task_with_edited_manifest(self):
        """按任务核对：另一个包已确认且未变，本包签后清单被改 → 3"""
        other = self.pack_ok(doc="g0", step="S0-INI")[0]
        self.sign_ok(other, "确认")
        self.sign_ok(self.pid, "确认")
        self.edit_manifest(lambda m: m.update(summary="改过"))
        self.assertExit(self.verify_task(), EXIT_FINGERPRINT)


class TestVerifyBadPackageId(VerifyBase):
    """〔裁定 Q21〕verify 的包编号格式不合法或包不存在 → 1。"""

    def test_package_not_exist(self):
        """包编号格式合法、但包不存在 → 1"""
        self.sign_ok(self.pid, "确认")
        for pid in ("qf-001-g1-99", "qf-002-g1-01"):
            with self.subTest(package_id=pid):
                self.assertExit(self.verify(pid), EXIT_USAGE)

    def test_package_id_invalid_format(self):
        """包编号格式不合法 → 1"""
        for pid in ("QF-001-g1-01", "qf-001-zz-01", "qf-001-g1-1", "bogus"):
            with self.subTest(package_id=pid):
                self.assertExit(self.verify(pid), EXIT_USAGE)


class TestVerifyRuledV3(VerifyBase):
    """接口规格 v0.3 第 9 节裁定补的 verify 用例。"""

    def _rewrite_manifest(self, change, reseal_sha=True):
        m = json.loads(self.manifest_path.read_bytes().decode("utf-8"))
        change(m)
        if reseal_sha:
            m["manifest_sha256"] = spec_manifest_sha256(m)
        self.manifest_path.write_bytes((json.dumps(m, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return m

    def test_stale_fingerprint_code_after_sign(self):
        """〔裁定 N2〕签后删掉附件项，写回新的 manifest_sha256、fingerprint_code 仍是旧值 → 3"""
        self.sign_ok(self.pid, "确认")
        m = self._rewrite_manifest(lambda m: m.update(files=[f for f in m["files"] if f["role"] == "正文"]))
        self.assertEqual(self.m["fingerprint_code"], m["fingerprint_code"])
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_fingerprint_code_altered_only(self):
        """〔裁定 N2〕签后只改 fingerprint_code → 3（清单自洽核对先于签字指纹码核对，所以不是 4）"""
        self.sign_ok(self.pid, "确认")
        self._rewrite_manifest(lambda m: m.update(fingerprint_code="deadbeef"), reseal_sha=False)
        self.assertExit(self.verify(), EXIT_FINGERPRINT)

    def test_bad_package_before_chain(self):
        """〔裁定 N7〕签字记录链条已坏时，核对不存在或格式不合法的包编号 → 1（先于一切核对）"""
        self.sign_ok(self.pid, "确认")
        self.sign_ok(self.pack_ok()[0], "确认")
        self.tamper_sig_line(0)
        self.assertExit(self.verify(self.pid), EXIT_CORRUPT)  # 对照：存在的包 → 5
        for pid in ("qf-001-g1-99", "QF-001-g1-01", "bogus"):
            with self.subTest(package_id=pid):
                self.assertExit(self.verify(pid), EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
