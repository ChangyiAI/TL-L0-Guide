# -*- coding: utf-8 -*-
"""〔v0.5〕复审与 Windows 冒烟后回归用例：接口规格 v0.5 第 11 节（第 11.12 节 9 组逐项）。

第 11 节与前文（含第 10 节）不一致之处以第 11 节为准。每个用例的说明里用〔裁定 11.n〕标出依据。
凡规格没有写死、需要测试员取舍的地方，见 open_questions.md 第六节，注释里以"第六节 Wn"标出。
需要特权才能构造的情形（符号链接、文件读不了），构造不出来时跳过并写明原因。
"""
import json
import os
import re
import time
import unittest

from seg1_support import (
    EXIT_CORRUPT, EXIT_FIELD, EXIT_FINGERPRINT, EXIT_OK, EXIT_USAGE, FIXED_NOW, MAIN_REL, REPO_NAME,
    Seg1TestCase, seal_manifest, sha256_hex,
)

BOM = b"\xef\xbb\xbf"
LOCK_NAME = "signatures.jsonl.lock"
ISO_TZ_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


class V5Base(Seg1TestCase):
    """本文件的公共部件。"""

    def setUp(self):
        super().setUp()
        self.setup_task()

    def verify(self, pid):
        return self.run_tl("verify", pid, "--json")

    def verify_task(self, task="qf-001"):
        return self.run_tl("verify", "--task", task, "--json")

    def status_task(self, task="qf-001"):
        return self.run_tl("status", "--task", task)

    def assertLineHas(self, text, package_id, state):
        lines = [line for line in text.splitlines() if package_id in line]
        self.assertTrue(lines, f"输出中找不到包编号 {package_id}\n{text}")
        self.assertTrue(any(state in line for line in lines), f"包编号 {package_id} 所在行应显示“{state}”\n{text}")

    def hand_manifest(self, package_id, files, task_id="qf-001"):
        """按规格 4.2 手写清单并封好指纹；files 为 (路径, 角色, sha256) 三元组，按路径排序。"""
        return seal_manifest({
            "package_id": package_id, "doc_type": "规格确认书", "task_id": task_id, "step": "S1-SPD",
            "summary": "手写清单", "created_at": FIXED_NOW, "created_by": "导引技能",
            "files": sorted(({"path": p, "sha256": s, "role": r} for p, r, s in files), key=lambda f: f["path"]),
        })

    def write_manifest(self, m, task_dir="qf-001"):
        return self.write_file(f".tianlong/work/{task_dir}/packages/{m['package_id']}.json",
                               json.dumps(m, ensure_ascii=False, indent=2) + "\n")

    def file_sha(self, rel):
        return sha256_hex(self.path(rel).read_bytes())


# ====================================================================== 11.1 签字文件位置

class TestV5SignFileLocation(V5Base):
    """〔裁定 11.1〕签字文件所在目录（签字根目录\\仓库名）解析后的真实位置不得位于仓库根之内，否则判 1。"""

    def setUp(self):
        super().setUp()
        self.pid = self.pack_ok()[0]

    def _repo_snapshot(self):
        return sorted(str(p.relative_to(self.repo)) for p in self.repo.rglob("*") if ".git" not in p.parts)

    def test_sign_dir_is_parent_of_repo(self):
        """TL_SIGN_DIR 设为仓库的上一级目录（签字文件会落到 <仓库>/signatures.jsonl）：sign 判 1，verify 判 1，
        仓库内不出现签字文件或锁文件"""
        before = self._repo_snapshot()
        env = {"TL_SIGN_DIR": str(self.tmp)}
        self.assertExit(self.run_tl_env("sign", self.pid, "确认", "--json", env=env), EXIT_USAGE)
        self.assertExit(self.run_tl_env("verify", self.pid, "--json", env=env), EXIT_USAGE)
        self.assertFalse((self.repo / "signatures.jsonl").exists(), "仓库内不得出现签字文件")
        self.assertFalse((self.repo / LOCK_NAME).exists(), "仓库内不得出现锁文件")
        self.assertEqual(before, self._repo_snapshot(), "仓库内不得多出任何文件")

    def test_repo_subdir_links_into_repo(self):
        """签字根目录本身在仓库外，但其下的 <仓库名> 是指向仓库内的链接：sign 判 1、verify 判 1，链接目标里不写入
        （没有建链接的特权时跳过）"""
        target = self.repo / ".sig-in-repo"
        target.mkdir()
        self.symlink_or_skip(target, self.sign_root / REPO_NAME, target_is_directory=True)
        self.assertExit(self.sign(self.pid, "确认"), EXIT_USAGE)
        self.assertExit(self.verify(self.pid), EXIT_USAGE)
        self.assertEqual([], list(target.iterdir()), "不得经链接把签字写进仓库")


# ====================================================================== 11.2 按任务核对的汇总

class TestV5VerifyTaskAggregate(V5Base):
    """〔裁定 11.2〕"已签包的清单缺失"（3）并入逐包结果；全部核对完再按 5 ＞ 3 ＞ 2 ＞ 4 取最重，不得提前返回。"""

    def _two_signed(self):
        p1 = self.pack_ok()[0]
        p2 = self.pack_ok(doc="g0", step="S0-INI")[0]
        m1 = json.loads((self.packages_dir("qf-001") / f"{p1}.json").read_bytes().decode("utf-8"))
        m2 = json.loads((self.packages_dir("qf-001") / f"{p2}.json").read_bytes().decode("utf-8"))
        self.sign_ok(p1, "确认")
        self.sign_ok(p2, "确认")
        return {p1: m1, p2: m2}

    def test_duplicate_and_missing_manifest(self):
        """某包重复签字（5）＋ 另一已签包清单缺失（3）→ 5；两种先后排列都试；对照：只有清单缺失 → 3"""
        for dup_first in (True, False):
            with self.subTest(重复签字的是=("先打包的包" if dup_first else "后打包的包")):
                self.clear_task_and_signatures()
                ms = self._two_signed()
                (dup, dm), (gone, _) = list(ms.items()) if dup_first else list(ms.items())[::-1]
                self.assertExit(self.verify_task(), EXIT_OK, "对照：两包都已确认时应通过")
                (self.packages_dir("qf-001") / f"{gone}.json").unlink()
                self.assertExit(self.verify_task(), EXIT_FINGERPRINT, "对照：只有清单缺失时应为 3")
                self.append_sig_record(self.forged_record_v4(dup, dm["fingerprint_code"], dm["manifest_sha256"]))
                self.assertExit(self.verify_task(), EXIT_CORRUPT)

    def clear_task_and_signatures(self):
        for p in self.list_package_files():
            p.unlink()
        if self.sig_file.exists():
            self.sig_file.unlink()


# ====================================================================== 11.3 packages 路径判断

class TestV5PackagesPath(V5Base):
    """〔裁定 11.3〕"不得指向任何任务的 packages/"按解析后的真实路径判断；Windows 下不区分大小写，
    并按系统规则规范化（含末尾的点和空格）。"""

    PID = "qf-001-g1-01"
    NOTES = ".tianlong/work/qf-001/packages/notes.md"

    def setUp(self):
        super().setUp()
        self.write_file(self.NOTES, "packages 下的文件\n")

    def _assert_all_two(self, path_in_manifest):
        m = self.hand_manifest(self.PID, [(MAIN_REL, "正文", self.file_sha(MAIN_REL)),
                                          (path_in_manifest, "附件", self.file_sha(self.NOTES))])
        self.write_manifest(m)
        res = self.sign(self.PID, "确认")
        self.assertExit(res, EXIT_FIELD, "sign 应判 2")
        self.assertIsNone(self.sig_bytes(), "不得写入签字")
        self.assertExit(self.verify(self.PID), EXIT_FIELD, "verify 应判 2")
        self.assertExit(self.status_task(), EXIT_FIELD, "status --task 应判 2")

    def test_control_hand_manifest(self):
        """对照组：同样手写、但附件是仓库里的普通文件，sign 0（证明手写清单本身合格）"""
        self.write_file("docs/plain.md", "普通文件\n")
        m = self.hand_manifest(self.PID, [(MAIN_REL, "正文", self.file_sha(MAIN_REL)),
                                          ("docs/plain.md", "附件", self.file_sha("docs/plain.md"))])
        self.write_manifest(m)
        self.assertExit(self.sign(self.PID, "确认"), EXIT_OK)

    def test_case_and_trailing_dot_on_windows(self):
        """Windows：路径写成 Packages/…（大小写不同）或 packages./…、"packages /…"（末尾带点、空格）时，
        sign、verify、status 判 2；pack --attach 判 1（非 Windows 跳过，第六节 W1）"""
        if os.name != "nt":
            self.skipTest("规格 11.3 只要求 Windows 下不区分大小写、按系统规则规范化；"
                          "本机不是 Windows，Packages/ 与 packages/ 是两个不同目录（见 open_questions 第六节 W1）")
        for variant in (".tianlong/work/qf-001/Packages/notes.md", ".tianlong/work/qf-001/PACKAGES/notes.md",
                        ".tianlong/work/qf-001/packages./notes.md", ".tianlong/work/qf-001/packages /notes.md"):
            with self.subTest(path=variant):
                for p in self.list_package_files():
                    p.unlink()
                self._assert_all_two(variant)
                for p in self.list_package_files():
                    p.unlink()
                self.assertExit(self.pack(attach=(variant,)), EXIT_USAGE, "pack 应判 1（接口规格 Q6）")
                self.assertEqual([], self.list_package_files())

    def test_link_into_packages(self):
        """经仓库内的链接指向 packages/：目录链接 docs/pk → packages/，文件链接 docs/notes-link.md → packages/notes.md；
        sign、verify、status 判 2（没有建链接的特权时跳过）"""
        pk = self.packages_dir("qf-001")
        self.symlink_or_skip(pk, self.path("docs/pk"), target_is_directory=True)
        self.symlink_or_skip(pk / "notes.md", self.path("docs/notes-link.md"))
        for variant in ("docs/pk/notes.md", "docs/notes-link.md"):
            with self.subTest(path=variant):
                for p in self.list_package_files():
                    p.unlink()
                self._assert_all_two(variant)


# ====================================================================== 11.4 签字记录逐字段校验

class TestV5RecordFields(V5Base):
    """〔裁定 11.4〕读取签字记录时逐字段校验类型和取值，任一项不合格判 5；5.4 旧的"指纹码与清单一致（否则 4）"作废。
    每个用例都把记录整份重写、链条自洽，只坏一个字段。"""

    def setUp(self):
        super().setUp()
        self.pid = self.pack_ok()[0]
        self.other = self.pack_ok(doc="g0", step="S0-INI")[0]
        self.sign_ok(self.pid, "确认")
        self.good = self.sig_records()[0]

    def _bad(self, change):
        rec = dict(self.good)
        change(rec)
        return rec

    def cases(self):
        return {
            "package_id 为数字": self._bad(lambda r: r.update(package_id=12345)),
            "package_id 不合格式": self._bad(lambda r: r.update(package_id="qf-001-g1-1")),
            "decision 为其他值": self._bad(lambda r: r.update(decision="同意")),
            "signer 不是 APR:Fred": self._bad(lambda r: r.update(signer="APR:Other")),
            "signed_at 不带时区": self._bad(lambda r: r.update(signed_at="2026-10-03T21:14:05")),
            "signed_at 不是时间": self._bad(lambda r: r.update(signed_at="昨天晚上")),
            "fingerprint_code 与完整指纹前 8 位不一致": self._bad(lambda r: r.update(fingerprint_code="deadbeef")),
            "manifest_sha256 不是小写十六进制": self._bad(lambda r: r.update(manifest_sha256=r["manifest_sha256"].upper())),
            "manifest_sha256 位数不对": self._bad(lambda r: r.update(manifest_sha256=r["manifest_sha256"][:63])),
            "退回理由为空": self._bad(lambda r: r.update(decision="退回", reason="")),
            "退回理由只有空白": self._bad(lambda r: r.update(decision="退回", reason="  　")),
            "reason 不是字符串": self._bad(lambda r: r.update(reason=None)),
        }

    def test_control_rewrite_unchanged(self):
        """对照组：原样重写（链条自洽、字段都合格）→ verify 0（证明重写方法本身不会弄坏记录）"""
        self.rewrite_sig_records([self.good])
        self.assertExit(self.verify(self.pid), EXIT_OK)

    def test_bad_field_is_corrupt(self):
        """package_id 为数字或不合格式、decision 其他值、signer 不对、signed_at 不合法、指纹码与完整指纹前 8 位不一致、
        完整指纹格式不对、退回理由为空或只有空白、reason 不是字符串：verify 判 5；sign 别的包也判 5 且不写入"""
        for name, rec in self.cases().items():
            with self.subTest(case=name):
                self.rewrite_sig_records([rec])
                self.assertExit(self.verify(self.pid), EXIT_CORRUPT, "verify 应判 5")
                before = self.sig_bytes()
                self.assertExit(self.sign(self.other, "确认"), EXIT_CORRUPT, "sign 应判 5")
                self.assertEqual(before, self.sig_bytes(), "sign 不得写入")


# ====================================================================== 11.5 status 的行为

class TestV5Status(V5Base):
    """〔裁定 11.5〕status 只返回 0、1、2，不返回 5：签字记录损坏时照常列出，在相关包上标"签字记录损坏"；
    不带 --task 时每个任务一行（任务编号、当前步骤、通道、令牌持有者、待签包数）；返回 2 时标准错误逐条列出包编号。"""

    def test_corrupt_field_status_zero(self):
        """包的签字记录有字段不合格（decision 为"同意"）：status --task 返回 0，该包所在行显示“签字记录损坏”；
        不带 --task、带 --json 也都返回 0"""
        pid = self.pack_ok()[0]
        self.sign_ok(pid, "确认")
        rec = self.sig_records()[0]
        rec["decision"] = "同意"
        self.rewrite_sig_records([rec])
        res = self.status_task()
        self.assertExit(res, EXIT_OK)
        self.assertLineHas(res.stdout, pid, "签字记录损坏")
        self.assertExit(self.run_tl("status"), EXIT_OK)
        self.assertExit(self.run_tl("status", "--task", "qf-001", "--json"), EXIT_OK)

    def test_chain_broken_status_zero(self):
        """签字记录链条断开：status --task 返回 0，输出中出现“签字记录损坏”（第六节 W2：链条断开时哪些包算"相关"未写死）"""
        p1, p2 = self.pack_ok()[0], self.pack_ok()[0]
        self.sign_ok(p1, "确认")
        self.sign_ok(p2, "确认")
        self.tamper_sig_line(0)
        res = self.status_task()
        self.assertExit(res, EXIT_OK)
        self.assertIn("签字记录损坏", res.stdout, res.describe())
        self.assertExit(self.run_tl("status"), EXIT_OK)

    def test_listing_one_line_per_task(self):
        """不带 --task：每个任务恰好一行，含任务编号、当前步骤、通道、令牌持有者与待签包数
        （两个任务的包都未签，待签包数分别为 3、4；第六节 W3）"""
        self.setup_task("qf-002")
        d = self.valid_progress("qf-002")
        d.update(channel="CH-FIX", current_step="S2-DSN")
        d["token_holder"]["role"] = "REV"
        self.write_progress("qf-002", d)
        self.write_file("docs/qf-002.md", "x\n")
        for _ in range(3):
            self.pack_ok()
        for _ in range(4):
            self.pack_ok(task="qf-002", main="docs/qf-002.md", attach=())
        res = self.run_tl("status")
        self.assertExit(res, EXIT_OK)
        for task, step, channel, role, count in (("qf-001", "S1-SPD", "CH-FEAT", "ARC", 3),
                                                 ("qf-002", "S2-DSN", "CH-FIX", "REV", 4)):
            with self.subTest(task=task):
                lines = [line for line in res.stdout.splitlines() if task in line]
                self.assertEqual(1, len(lines), f"任务 {task} 应恰好一行\n{res.stdout}")
                line = lines[0]
                for value in (step, channel, role):
                    self.assertIn(value, line, res.describe())
                self.assertRegex(line, rf"(?<![0-9]){count}(?![0-9])", f"应含待签包数 {count}{res.describe()}")

    def test_exit_2_lists_packages_on_stderr(self):
        """两个清单结构不合格（files 为空）：status --task 返回 2，标准错误逐条列出这两个包编号"""
        self.pack_ok()
        bad = ("qf-001-fx-01", "qf-001-mn-01")
        for pid in bad:
            self.write_manifest(self.hand_manifest(pid, []))
        res = self.status_task()
        self.assertExit(res, EXIT_FIELD)
        for pid in bad:
            with self.subTest(package_id=pid):
                self.assertIn(pid, res.stderr, res.describe())


# ====================================================================== 11.6 编码

class TestV5Encoding(V5Base):
    """〔裁定 11.6〕假设日志、交接卡不是合法 UTF-8 时判 2；assumptions.jsonl 开头带 BOM 时判 2，提示同 10.8（含“BOM”）。"""

    def _gbk(self, text):
        data = text.encode("gbk")
        with self.assertRaises(UnicodeDecodeError, msg="测试夹具本身应当不是合法 UTF-8"):
            data.decode("utf-8")
        return data

    def test_assumptions_not_utf8(self):
        """假设日志不是合法 UTF-8：①只在自由文本 decision 里夹一个非法字节（其余字段都合格，只有编码错）；
        ②整行按 GBK 编码保存。check 都判 2"""
        line = json.dumps(self.valid_assumption(1, decision="日志文件名统一用小写MARK"), ensure_ascii=False) + "\n"
        only_bad_byte = line.encode("utf-8").replace(b"MARK", b"\xff")
        for name, data in (("只有一个非法字节", only_bad_byte), ("GBK 编码", self._gbk(line))):
            with self.subTest(case=name):
                self.write_file(".tianlong/work/qf-001/assumptions.jsonl", data)
                self.assertExit(self.run_tl("check", "--task", "qf-001", "--json"), EXIT_FIELD)

    def test_handover_not_utf8(self):
        """交接卡不是合法 UTF-8：①六栏都填齐、只在"未决事项"一栏夹一个非法字节；②整张按 GBK 编码保存。check 都判 2"""
        text = self.handover_text()
        self.assertIn("| 无 |", text, "测试夹具：未决事项一栏应为“无”")
        only_bad_byte = text.encode("utf-8").replace("| 无 |".encode("utf-8"), "| 无".encode("utf-8") + b"\xff |", 1)
        for name, data in (("只有一个非法字节", only_bad_byte), ("GBK 编码", self._gbk(text))):
            with self.subTest(case=name):
                self.write_file(".tianlong/work/qf-001/forms/07_交接卡_s1.md", data)
                self.assertExit(self.run_tl("check", "--task", "qf-001", "--json"), EXIT_FIELD)

    def test_assumptions_bom(self):
        """assumptions.jsonl 开头带 BOM（内容本身合格）：check 判 2，标准错误含“BOM”"""
        p = self.work_dir("qf-001") / "assumptions.jsonl"
        self.assertExit(self.run_tl("check", "--task", "qf-001"), EXIT_OK, "对照：不带 BOM 时应通过")
        p.write_bytes(BOM + p.read_bytes())
        res = self.run_tl("check", "--task", "qf-001")
        self.assertExit(res, EXIT_FIELD)
        self.assertIn("BOM", res.stderr, res.describe())


# ====================================================================== 11.7 签字锁的提示

class TestV5LockMessage(V5Base):
    """〔裁定 11.7〕等锁超时的提示须写明锁文件的完整路径，并说明确认没有别的签字进程后可以删除该锁文件再重试。"""

    def test_lock_timeout_message(self):
        """锁文件一直存在：sign 约 10 秒后判 1，标准错误含锁文件完整路径与“删除”二字；不写入、不删锁"""
        pid = self.pack_ok()[0]
        lock = self.sign_root / REPO_NAME / LOCK_NAME
        lock.parent.mkdir(parents=True)
        lock.write_bytes(b"")
        t0 = time.monotonic()
        res = self.sign(pid, "确认")
        elapsed = time.monotonic() - t0
        self.assertExit(res, EXIT_USAGE)
        self.assertGreaterEqual(elapsed, 9.0, f"应等待约 10 秒再放弃，实际 {elapsed:.1f} 秒")
        self.assertTrue(str(lock) in res.stderr or lock.as_posix() in res.stderr,
                        f"标准错误应含锁文件完整路径 {lock}{res.describe()}")
        self.assertIn("删除", res.stderr, res.describe())
        self.assertIsNone(self.sig_bytes())
        self.assertTrue(lock.exists(), "不得删除别的进程的锁文件")


# ====================================================================== 11.8 签字文件读不了

class TestV5SignFileBusy(V5Base):
    """〔裁定 11.8〕读取签字文件遇到权限类错误（例如被其他程序独占打开）判 1，提示"签字文件被占用"。"""

    def test_sign_file_unreadable(self):
        """签字文件读不了：verify、sign 都判 1，标准错误含“签字文件被占用”，sign 不写入
        （Windows 上以字节锁模拟独占打开；其他系统去掉读权限；以 root 运行等无法构造时跳过）"""
        p0 = self.pack_ok()[0]
        p1 = self.pack_ok(doc="g0", step="S0-INI")[0]
        self.sign_ok(p0, "确认")
        before = self.sig_bytes()
        restore = self.make_unreadable_or_skip(self.sig_file)
        results = {"verify": self.verify(p0), "sign": self.sign(p1, "确认")}
        restore()
        for name, res in results.items():
            with self.subTest(command=name):
                self.assertExit(res, EXIT_USAGE)
                self.assertIn("签字文件被占用", res.stderr, res.describe())
        self.assertEqual(before, self.sig_bytes(), "sign 不得写入")


# ====================================================================== 11.9 TL_NOW 的写法

class TestV5TlNow(V5Base):
    """〔裁定 11.9〕TL_NOW 接受 Z 结尾与 +hh:mm／-hh:mm 结尾，结果一致；空串按未设置处理（追认 V5）。"""

    def _pack_sign_verify(self, now, doc):
        env = {"TL_NOW": now}
        res = self.run_tl_env("pack", "--task", "qf-001", "--doc", doc, "--step", "S1-SPD", "--main", MAIN_REL,
                              "--summary", f"TL_NOW={now}", "--json", env=env)
        self.assertExit(res, EXIT_OK, f"TL_NOW={now} 时 pack 应成功")
        pid = f"qf-001-{doc}-01"
        m = json.loads((self.packages_dir("qf-001") / f"{pid}.json").read_bytes().decode("utf-8"))
        self.assertExit(self.run_tl_env("sign", pid, "确认", "--json", env=env), EXIT_OK, f"TL_NOW={now} 时 sign 应成功")
        self.assertExit(self.run_tl_env("verify", pid, "--json", env=env), EXIT_OK)
        rec = [r for r in self.sig_records() if r["package_id"] == pid][0]
        return m["created_at"], rec["signed_at"]

    def test_z_same_as_plus_zero(self):
        """TL_NOW 为 …Z、…+00:00、…-05:00 时 pack、sign、verify 都成功，created_at、signed_at 原样写入（接口规格 Q4）；
        日期不合法的 …Z 仍判 1"""
        for now, doc in (("2026-10-03T13:14:05Z", "g1"), ("2026-10-03T13:14:05+00:00", "g0"),
                         ("2026-10-03T08:14:05-05:00", "pa")):
            with self.subTest(TL_NOW=now):
                self.assertEqual((now, now), self._pack_sign_verify(now, doc))
        res = self.run_tl_env("pack", "--task", "qf-001", "--doc", "mn", "--step", "S1-SPD", "--main", MAIN_REL,
                              "--summary", "x", "--json", env={"TL_NOW": "2026-13-03T13:14:05Z"})
        self.assertExit(res, EXIT_USAGE, "月份为 13 的 …Z 应判 1")

    def test_empty_is_unset(self):
        """TL_NOW 为空串：按未设置处理，pack 成功，created_at 为带时区的 ISO 8601 时间（追认 V5）"""
        res = self.run_tl_env("pack", "--task", "qf-001", "--doc", "g1", "--step", "S1-SPD", "--main", MAIN_REL,
                              "--summary", "x", "--json", env={"TL_NOW": ""})
        self.assertExit(res, EXIT_OK)
        m = json.loads((self.packages_dir("qf-001") / "qf-001-g1-01.json").read_bytes().decode("utf-8"))
        self.assertRegex(m["created_at"], ISO_TZ_RE)


if __name__ == "__main__":
    unittest.main()
