# -*- coding: utf-8 -*-
"""〔v0.4〕审查后回归用例：接口规格 v0.4 第 10 节裁定 R1～R8（第 10.9 节逐项）。

第 10 节与前文不一致之处以第 10 节为准。每个用例的说明里用〔裁定 Rn〕标出依据。
凡规格没有写死、需要测试员取舍的地方，见 open_questions.md 第五节，注释里以"第五节 Vn"标出。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import unittest

from seg1_support import (
    ATTACH_REL, EXIT_CORRUPT, EXIT_FIELD, EXIT_FINGERPRINT, EXIT_OK, EXIT_UNSIGNED, EXIT_USAGE,
    FIXED_NOW, HEX64_RE, MAIN_REL, REPO_NAME, TL_PATH, ZERO64, Seg1TestCase, seal_manifest, sha256_hex,
    spec_manifest_sha256,
)

BOM = b"\xef\xbb\xbf"
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LOCK_NAME = "signatures.jsonl.lock"


class RegBase(Seg1TestCase):
    """回归用例的公共部件（只在本文件内使用）。"""

    def setUp(self):
        super().setUp()
        self.setup_task()

    # ------------------------------------------------ 手写清单

    def entry(self, path, role, sha=None):
        """清单文件项；sha 缺省时按仓库内的文件原始字节计算（不存在则填 64 个 0）。
        注意：只对"词法上正常"的路径去读文件，带 \\\\、盘符等的路径一律由调用方给出 sha，测试本身不去访问它们。"""
        if sha is None:
            p = self.repo / path
            sha = sha256_hex(p.read_bytes()) if p.is_file() else ZERO64
        return {"path": path, "sha256": sha, "role": role}

    def hand_manifest(self, package_id, files, task_id="qf-001", **overrides):
        """按规格 4.2 手写清单并封好指纹（manifest_sha256 与 fingerprint_code 自洽）；files 按路径排序。"""
        m = {
            "package_id": package_id,
            "doc_type": "规格确认书",
            "task_id": task_id,
            "step": "S1-SPD",
            "summary": "手写清单",
            "created_at": FIXED_NOW,
            "created_by": "导引技能",
            "files": sorted(files, key=lambda f: f["path"]),
        }
        m.update(overrides)
        return seal_manifest(m)

    def good_files(self):
        return [self.entry(MAIN_REL, "正文"), self.entry(ATTACH_REL, "附件")]

    def write_manifest(self, m, task_dir="qf-001", filename=None, bom=False):
        name = filename or m["package_id"]
        data = (json.dumps(m, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        return self.write_file(f".tianlong/work/{task_dir}/packages/{name}.json", (BOM if bom else b"") + data)

    def clear_packages(self):
        for p in self.list_package_files():
            p.unlink()

    # ------------------------------------------------ 运行与判定

    def verify(self, pid):
        return self.run_tl("verify", pid, "--json")

    def verify_task(self, task="qf-001"):
        return self.run_tl("verify", "--task", task, "--json")

    def status_task(self, task="qf-001"):
        return self.run_tl("status", "--task", task)

    def assertLineHas(self, text, package_id, state):
        lines = [line for line in text.splitlines() if package_id in line]
        self.assertTrue(lines, f"输出中找不到包编号 {package_id}\n{text}")
        self.assertTrue(any(state in line for line in lines),
                        f"包编号 {package_id} 所在行应显示“{state}”\n{text}")

    def assertChinese(self, res, where="stderr"):
        text = getattr(res, where)
        self.assertTrue(text.strip(), f"〔裁定 R8〕非 0 退出时{where}不应为空{res.describe()}")
        self.assertRegex(text, CJK_RE, f"〔裁定 R8〕{where}应含中文原因{res.describe()}")

    def rewrite_sig(self, records):
        """用给定记录重写整份签字记录，并按规格重新串好链条（模拟"整份重写、链条自洽"的篡改）。"""
        out, prev = [], ZERO64
        for rec in records:
            r = dict(rec)
            r["prev_sha256"] = prev
            line = json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            out.append(line)
            prev = sha256_hex(line)
        self.sig_file.parent.mkdir(parents=True, exist_ok=True)
        self.sig_file.write_bytes(b"".join(line + b"\n" for line in out))

    def edit_manifest_file(self, path, change, reseal=False):
        m = json.loads(path.read_bytes().decode("utf-8"))
        change(m)
        if reseal:
            m = seal_manifest(m)
        path.write_bytes((json.dumps(m, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return m


# ====================================================================== R1 仓库根

class TestR1RepoRoot(RegBase):
    """〔裁定 R1〕仓库根：从当前目录逐级向上，第一个含 .git（目录或文件）的目录；不调用任何外部程序（含 git）；
    pack 与 sign 要求当前目录位于仓库根之内。"""

    def setUp(self):
        Seg1TestCase.setUp(self)  # 不在默认仓库里建任务，各用例自己建

    def _plain_project(self, name, git_kind):
        """建一个"不是真 git 仓库"的项目目录：只有空的 .git 目录，或一个 .git 文件。真 git 在这里会报"不是仓库"。"""
        root = self.tmp / name
        root.mkdir()
        if git_kind == "dir":
            (root / ".git").mkdir()
        else:
            (root / ".git").write_text("gitdir: ../no-such-gitdir\n", encoding="utf-8")
        return root

    def _full_cycle(self, root, cwd=None, main=MAIN_REL):
        """在 root 里建任务，打包、签字、核对、查看，全部应成功；返回包编号。"""
        self.repo = root
        self.setup_task()
        cwd = cwd or root
        res = self.run_tl("pack", "--task", "qf-001", "--doc", "g1", "--step", "S1-SPD", "--main", main,
                          "--summary", "仓库根用例", "--json", cwd=cwd)
        self.assertExit(res, EXIT_OK, "pack 应成功")
        files = sorted(p.name for p in (root / ".tianlong/work/qf-001/packages").iterdir())
        self.assertEqual(["qf-001-g1-01.json"], files)
        pid = "qf-001-g1-01"
        self.assertExit(self.run_tl("sign", pid, "确认", "--json", cwd=cwd), EXIT_OK, "sign 应成功")
        sig = self.sign_root / root.name / "signatures.jsonl"
        self.assertTrue(sig.is_file(), f"签字应写入 <签字根目录>/{root.name}/signatures.jsonl")
        self.assertExit(self.run_tl("verify", pid, "--json", cwd=cwd), EXIT_OK)
        self.assertExit(self.run_tl("status", "--task", "qf-001", "--json", cwd=cwd), EXIT_OK)
        self.assertExit(self.run_tl("check", "--task", "qf-001", "--json", cwd=cwd), EXIT_OK)
        return pid

    def test_git_dir_or_file_marks_root(self):
        """只有空的 .git 目录、或只有一个 .git 文件的目录，也是仓库根：五个子命令都正常（说明不依赖 git 程序）"""
        for kind in ("dir", "file"):
            with self.subTest(git=kind):
                root = self._plain_project(f"plain-{kind}", kind)
                self._full_cycle(root)

    def test_run_from_subdirectory(self):
        """在仓库根下的子目录里运行：仍以仓库根为准（清单写在仓库根下、签字写在 <仓库名> 下）"""
        sub = self.repo / "docs" / "deep"
        sub.mkdir(parents=True)
        self._full_cycle(self.repo, cwd=sub, main=str(self.repo / MAIN_REL))
        self.assertTrue(self.sig_file.is_file())
        self.assertFalse((sub / ".tianlong").exists(), "不得把子目录当成仓库根")

    def test_nearest_git_wins(self):
        """仓库里嵌套了一个含 .git 的子目录：从它里面运行，以最近的一层为仓库根"""
        inner = self.repo / "vendor" / "inner"
        inner.mkdir(parents=True)
        (inner / ".git").mkdir()
        deep = inner / "a" / "b"
        deep.mkdir(parents=True)
        self._full_cycle(inner, cwd=deep, main=str(inner / MAIN_REL))
        self.assertFalse((self.tmp / REPO_NAME / ".tianlong").exists(), "外层仓库不得被写入")
        self.assertFalse((self.sign_root / REPO_NAME).exists(), "签字应记在内层仓库名下")

    def test_fake_git_not_executed(self):
        """当前目录和 PATH 里都放一个名为 git 的程序：tl 照常工作，且从不执行它"""
        marker = self.tmp / "git-was-executed"
        fake_bin = self.tmp / "fake-bin"
        fake_bin.mkdir()
        self.setup_task()
        for d in (fake_bin, self.repo):
            sh = d / "git"
            sh.write_text(f"#!/bin/sh\necho run > '{marker}'\necho /nonexistent\nexit 0\n", encoding="utf-8")
            sh.chmod(0o755)
            for ext in (".bat", ".cmd"):
                (d / f"git{ext}").write_text(f"@echo run> \"{marker}\"\r\n@echo C:\\nonexistent\r\n",
                                             encoding="utf-8")
        # PATH 只剩假 git 所在目录与当前目录：调用任何 git 都会落到假程序上（或者找不到 git）
        env = {"PATH": os.pathsep.join([str(fake_bin), "."])}
        res = self.run_tl_env("pack", "--task", "qf-001", "--doc", "g1", "--step", "S1-SPD", "--main", MAIN_REL,
                              "--summary", "假 git", "--json", env=env)
        self.assertExit(res, EXIT_OK)
        for args in (("sign", "qf-001-g1-01", "确认", "--json"), ("verify", "qf-001-g1-01", "--json"),
                     ("verify", "--task", "qf-001", "--json"), ("status", "--json"),
                     ("check", "--task", "qf-001", "--json")):
            with self.subTest(args=args):
                self.assertExit(self.run_tl_env(*args, env=env), EXIT_OK)
        self.assertFalse(marker.exists(), "tl 不得执行任何名为 git 的程序（接口规格 10.1）")
        self.assertTrue(self.sig_file.is_file())

    def test_git_env_vars_ignored(self):
        """设置 GIT_DIR、GIT_WORK_TREE 指向另一个仓库：不影响仓库根（清单、签字都落在当前仓库名下）"""
        self.setup_task()
        other = self.tmp / "other-proj"
        other.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(other), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        env = {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)}
        res = self.run_tl_env("pack", "--task", "qf-001", "--doc", "g1", "--step", "S1-SPD", "--main", MAIN_REL,
                              "--summary", "环境变量", "--json", env=env)
        self.assertExit(res, EXIT_OK)
        self.assertTrue((self.packages_dir("qf-001") / "qf-001-g1-01.json").is_file())
        self.assertExit(self.run_tl_env("sign", "qf-001-g1-01", "确认", "--json", env=env), EXIT_OK)
        self.assertTrue(self.sig_file.is_file())
        self.assertExit(self.run_tl_env("verify", "qf-001-g1-01", "--json", env=env), EXIT_OK)
        self.assertFalse((other / ".tianlong").exists(), "不得写入 GIT_WORK_TREE 指向的仓库")
        self.assertFalse((self.sign_root / "other-proj").exists(), "签字不得记到别的仓库名下")

    def test_cwd_outside_repo(self):
        """当前目录在仓库根之外（即使 GIT_DIR、GIT_WORK_TREE 指向仓库）：pack、sign 判 1，不写清单、不写签字"""
        outside = self.tmp / "outside-dir"
        outside.mkdir()
        for d in (outside, *outside.parents):
            if (d / ".git").exists():
                self.skipTest(f"本机临时目录的上层 {d} 含 .git，无法构造“仓库之外”（见 open_questions 第五节 V9）")
        self.setup_task()
        pid = self.pack_ok()[0]
        before = set(self.list_package_files())
        env = {"GIT_DIR": str(self.repo / ".git"), "GIT_WORK_TREE": str(self.repo)}
        res = self.run_tl_env("pack", "--task", "qf-001", "--doc", "g1", "--step", "S1-SPD",
                              "--main", str(self.repo / MAIN_REL), "--summary", "仓库外", "--json",
                              env=env, cwd=outside)
        self.assertExit(res, EXIT_USAGE)
        self.assertEqual(before, set(self.list_package_files()))
        res = self.run_tl_env("sign", pid, "确认", "--json", env=env, cwd=outside)
        self.assertExit(res, EXIT_USAGE)
        self.assertIsNone(self.sig_bytes())
        self.assertEqual([], list(outside.iterdir()), "不得在当前目录写出任何文件")


# ====================================================================== R2 清单结构校验

class TestR2ManifestStructure(RegBase):
    """〔裁定 R2〕sign、verify、status 读取受审文件之前先做清单结构校验，任一项不合格判 2。
    每个手写清单都封好了指纹、文件指纹也都对得上：若不做结构校验，sign 会写入签字（或判 3），因此能区分。"""

    PID = "qf-001-g1-01"

    def setUp(self):
        super().setUp()
        self.outside = self.tmp / "outside.md"
        self.outside.write_text("仓库外的文件\n", encoding="utf-8")
        self.write_file(".tianlong/work/qf-001/packages/notes.md", "本任务 packages 下的文件\n")
        self.write_file(".tianlong/work/qf-002/packages/notes.md", "别的任务 packages 下的文件\n")

    def _abs_posix_no_drive(self):
        p = self.outside.resolve()
        s = p.as_posix()
        return s[len(p.drive):] if p.drive else s

    def _abs_with_drive(self):
        p = self.outside.resolve()
        return p.as_posix() if p.drive else "C:/tl-test/outside.md"

    def bad_cases(self):
        """名称 → 清单（文件名一律为 qf-001-g1-01.json，放在 qf-001 下）。"""
        good = self.good_files
        out_sha = sha256_hex(self.outside.read_bytes())
        cases = {
            "编号：内容与文件名不一致": self.hand_manifest("qf-001-g1-02", good()),
            "编号：内容为别的文书代码": self.hand_manifest("qf-001-g0-01", good()),
            "任务：task_id 与目录不一致": self.hand_manifest(self.PID, good(), task_id="qf-002"),
            "files 为空": self.hand_manifest(self.PID, []),
            "没有正文": self.hand_manifest(self.PID, [self.entry(MAIN_REL, "附件"), self.entry(ATTACH_REL, "附件")]),
            "正文不止一个": self.hand_manifest(self.PID, [self.entry(MAIN_REL, "正文"), self.entry(ATTACH_REL, "正文")]),
            "role 取值不合法": self.hand_manifest(self.PID, [self.entry(MAIN_REL, "正文"), self.entry(ATTACH_REL, "封面")]),
            "路径越出仓库（../）": self.hand_manifest(self.PID, good() + [self.entry("../outside.md", "附件", out_sha)]),
            "路径含 ..（解析后仍在仓库内）": self.hand_manifest(
                self.PID, [self.entry(MAIN_REL, "正文"), self.entry("docs/../" + ATTACH_REL, "附件",
                                                                   sha256_hex(self.path(ATTACH_REL).read_bytes()))]),
            "以 \\\\ 开头（网络共享）": self.hand_manifest(
                self.PID, good() + [self.entry("\\\\tl-test.invalid\\share\\x.md", "附件", ZERO64)]),
            "以 \\ 开头": self.hand_manifest(self.PID, good() + [self.entry("\\tl-test\\x.md", "附件", ZERO64)]),
            "以 / 开头（绝对路径）": self.hand_manifest(
                self.PID, good() + [self.entry(self._abs_posix_no_drive(), "附件", out_sha)]),
            "带盘符（绝对）": self.hand_manifest(
                self.PID, good() + [self.entry(self._abs_with_drive(), "附件", out_sha)]),
            "带盘符（相对）": self.hand_manifest(self.PID, good() + [self.entry("c:x.md", "附件", ZERO64)]),
            "用反斜杠分隔": self.hand_manifest(
                self.PID, [self.entry(MAIN_REL, "正文"),
                           self.entry(ATTACH_REL.replace("/", "\\"), "附件",
                                      sha256_hex(self.path(ATTACH_REL).read_bytes()))]),
            "指向本任务 packages/": self.hand_manifest(
                self.PID, good() + [self.entry(".tianlong/work/qf-001/packages/notes.md", "附件")]),
            "指向别的任务 packages/": self.hand_manifest(
                self.PID, good() + [self.entry(".tianlong/work/qf-002/packages/notes.md", "附件")]),
        }
        return cases

    def _assert_all_three(self, m, check_line=True):
        self.clear_packages()
        self.write_manifest(m, filename=self.PID)
        res = self.sign(self.PID, "确认")
        self.assertExit(res, EXIT_FIELD, "sign 应判 2")
        self.assertIsNone(self.sig_bytes(), "结构不合格时不得写入签字")
        self.assertExit(self.verify(self.PID), EXIT_FIELD, "verify 应判 2")
        res = self.status_task()
        self.assertExit(res, EXIT_FIELD, "status --task 应判 2")
        if check_line:
            self.assertLineHas(res.stdout, self.PID, "清单不合格")
        else:
            self.assertIn("清单不合格", res.stdout, res.describe())

    def test_control_valid_hand_manifest(self):
        """对照组：结构合格的手写清单，sign 0、verify 0、status 0（证明手写清单本身按规格封好）"""
        self.write_manifest(self.hand_manifest(self.PID, self.good_files()))
        self.assertExit(self.sign(self.PID, "确认"), EXIT_OK)
        self.assertExit(self.verify(self.PID), EXIT_OK)
        self.assertExit(self.status_task(), EXIT_OK)

    def test_bad_manifests(self):
        """编号三处不一致、任务不一致、files 为空、正文数目不对、role 不合法、越出仓库、含 ..、以 \\\\ 或 \\ 或 / 开头、
        带盘符、反斜杠、指向任何任务的 packages/：sign、verify、status 都判 2，sign 不写入，status 显示“清单不合格”"""
        for name, m in self.bad_cases().items():
            with self.subTest(case=name):
                self._assert_all_three(m, check_line=(m["package_id"] == self.PID))

    def test_link_to_outside(self):
        """文件项是仓库内的一个链接、指向仓库外的文件 → 2"""
        link = self.path("docs/outside-link.md")
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(str(self.outside), str(link))
        except (OSError, NotImplementedError) as e:
            self.skipTest(f"本机无法建立符号链接：{e}")
        m = self.hand_manifest(self.PID, self.good_files() +
                               [self.entry("docs/outside-link.md", "附件", sha256_hex(self.outside.read_bytes()))])
        self._assert_all_three(m)

    def test_package_task_part_differs_from_dir(self):
        """qf-001 的 packages/ 下出现 qf-002-g1-01.json（内容自洽）：verify --task qf-001、status --task qf-001 → 2"""
        m = self.hand_manifest("qf-002-g1-01", self.good_files(), task_id="qf-002")
        self.write_manifest(m)
        self.assertExit(self.verify_task("qf-001"), EXIT_FIELD)
        self.assertExit(self.status_task("qf-001"), EXIT_FIELD)

    def test_package_in_other_task_dir(self):
        """qf-001 的包（内容自洽）放在 qf-002 的 packages/ 下：verify --task qf-002、status --task qf-002 → 2"""
        self.setup_task("qf-002")
        self.write_manifest(self.hand_manifest(self.PID, self.good_files()), task_dir="qf-002")
        self.assertExit(self.verify_task("qf-002"), EXIT_FIELD)
        self.assertExit(self.status_task("qf-002"), EXIT_FIELD)


class TestR2Severity(RegBase):
    """〔裁定 R2〕按任务核对的严重程度：5 ＞ 3 ＞ 2 ＞ 4。"""

    def setUp(self):
        super().setUp()
        self.p_unsigned = self.pack_ok()[0]                                    # 未签 → 4
        self.write_manifest(self.hand_manifest("qf-001-fx-01", []))            # 结构不合格 → 2

    def test_2_over_4(self):
        """一个结构不合格（2）、一个未签（4）→ 2"""
        self.assertExit(self.verify_task(), EXIT_FIELD)

    def test_3_over_2(self):
        """再加一个签后改件（3）→ 3"""
        self.write_file("docs/g0.md", "需求确认书\n")
        p = self.pack_ok(doc="g0", step="S0-INI", main="docs/g0.md", attach=())[0]
        self.sign_ok(p, "确认")
        self.modify("docs/g0.md")
        self.assertExit(self.verify_task(), EXIT_FINGERPRINT)

    def test_5_over_all(self):
        """再让签字记录链条损坏 → 5"""
        self.write_file("docs/g0.md", "需求确认书\n")
        p = self.pack_ok(doc="g0", step="S0-INI", main="docs/g0.md", attach=())[0]
        self.sign_ok(p, "确认")
        self.write_file("docs/pa.md", "过程审批单\n")
        self.sign_ok(self.pack_ok(doc="pa", main="docs/pa.md", attach=())[0], "确认")
        self.modify("docs/g0.md")
        self.tamper_sig_line(0)
        self.assertExit(self.verify_task(), EXIT_CORRUPT)


# ====================================================================== R3 完整清单指纹

class TestR3FullDigest(RegBase):
    """〔裁定 R3〕签字记录存完整清单指纹 manifest_sha256；verify 比对完整指纹，不一致判 3；
    fingerprint_code 只供人看；某行缺 manifest_sha256 判 5。"""

    def setUp(self):
        super().setUp()
        self.pid, self.manifest_path, self.m = self.pack_ok()

    def _same_prefix_other_digest(self):
        full = self.m["manifest_sha256"]
        fake = full[:8] + "".join("e" if c == "f" else "f" for c in full[8:])
        self.assertEqual(full[:8], fake[:8])
        self.assertNotEqual(full, fake)
        return fake

    def test_record_has_manifest_sha256(self):
        """sign 写入的记录含 manifest_sha256，等于清单的完整指纹（64 位小写十六进制）"""
        self.sign_ok(self.pid, "确认")
        rec = self.sig_records()[0]
        self.assertIn("manifest_sha256", rec)
        self.assertRegex(rec["manifest_sha256"], HEX64_RE)
        self.assertEqual(self.m["manifest_sha256"], rec["manifest_sha256"])
        self.assertEqual(spec_manifest_sha256(self.m), rec["manifest_sha256"])

    def test_forged_same_prefix(self):
        """追加一条链条正确、前 8 位相同但完整指纹不同的伪造签字 → 3"""
        fake = self._same_prefix_other_digest()
        self.append_sig_record(self.forged_record_v4(self.pid, self.m["fingerprint_code"], fake))
        self.assertExit(self.verify(self.pid), EXIT_FINGERPRINT)

    def test_rewritten_chain_same_prefix(self):
        """把合法签字的完整指纹改成前 8 位相同的另一个值、并重写整条链（链条自洽）→ 3"""
        self.sign_ok(self.pid, "确认")
        rec = self.sig_records()[0]
        rec["manifest_sha256"] = self._same_prefix_other_digest()
        self.rewrite_sig([rec])
        self.assertExit(self.verify(self.pid), EXIT_FINGERPRINT)

    def test_missing_field_is_corrupt(self):
        """签字记录（链条自洽）缺 manifest_sha256 → verify 判 5；sign 别的包也判 5，不写入"""
        self.sign_ok(self.pid, "确认")
        rec = self.sig_records()[0]
        self.assertIn("manifest_sha256", rec, "〔裁定 R3〕签字记录应含 manifest_sha256")
        del rec["manifest_sha256"]
        self.rewrite_sig([rec])
        self.assertExit(self.verify(self.pid), EXIT_CORRUPT)
        other = self.pack_ok()[0]
        before = self.sig_bytes()
        self.assertExit(self.sign(other, "确认"), EXIT_CORRUPT)
        self.assertEqual(before, self.sig_bytes())

    def test_missing_field_on_other_line(self):
        """缺字段的是别的包那一行：核对本包（记录完好）也判 5"""
        other = self.pack_ok()[0]
        self.sign_ok(self.pid, "确认")
        self.sign_ok(other, "确认")
        recs = self.sig_records()
        self.assertIn("manifest_sha256", recs[1], "〔裁定 R3〕签字记录应含 manifest_sha256")
        del recs[1]["manifest_sha256"]
        self.rewrite_sig(recs)
        self.assertExit(self.verify(self.pid), EXIT_CORRUPT)

    def test_duplicate_full_format(self):
        """同一个包两条格式完整的签字（链条正确）→ 5（补回既有重复签字用例在新格式下的覆盖）"""
        self.sign_ok(self.pid, "确认")
        self.append_sig_record(self.forged_record_v4(self.pid, self.m["fingerprint_code"], self.m["manifest_sha256"]))
        self.assertExit(self.verify(self.pid), EXIT_CORRUPT)
        self.assertExit(self.verify_task(), EXIT_CORRUPT)


# ====================================================================== R4 测试开关

class TestR4TestSwitches(RegBase):
    """〔裁定 R4〕TL_SIGN_DIR 空串按未设置处理；设置时须为绝对路径、且不在仓库根之内，否则判 1。
    TL_NOW 须为带时区的 ISO 8601 时间，否则判 1。"""

    def setUp(self):
        super().setUp()
        self.pid, _, self.m = self.pack_ok()

    def test_sign_dir_empty_is_unset(self):
        """TL_SIGN_DIR 为空串：不得当成当前目录。仓库里预先放一份"当前目录下"的伪造签字，verify 不得据此通过
        （按未设置处理时读默认签字目录，那里没有本测试仓库的签字；只做只读核对，不碰真实签字目录的写入）"""
        fake = self.repo / REPO_NAME / "signatures.jsonl"
        fake.parent.mkdir(parents=True)
        rec = self.forged_record_v4(self.pid, self.m["fingerprint_code"], self.m["manifest_sha256"])
        rec["prev_sha256"] = ZERO64
        fake.write_bytes(json.dumps(rec, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        before = fake.read_bytes()
        res = self.run_tl_env("verify", self.pid, "--json", env={"TL_SIGN_DIR": ""})
        # 第五节 V4：默认目录 C:\TianlongSign 在非 Windows 系统上不是绝对路径，实现可能判 1；Windows 上应为 4
        self.assertIn(res.code, (EXIT_USAGE, EXIT_UNSIGNED), res.describe())
        self.assertEqual(before, fake.read_bytes())

    def test_sign_dir_relative(self):
        """TL_SIGN_DIR 为相对路径（仓库内的、以及指回合法签字根目录的 ../）：sign、verify 判 1，不写入"""
        (self.repo / "sig-rel").mkdir()
        for value in ("sig-rel", os.path.join("..", "sign-root")):
            with self.subTest(TL_SIGN_DIR=value):
                env = {"TL_SIGN_DIR": value}
                self.assertExit(self.run_tl_env("sign", self.pid, "确认", "--json", env=env), EXIT_USAGE)
                self.assertExit(self.run_tl_env("verify", self.pid, "--json", env=env), EXIT_USAGE)
                self.assertEqual([], list((self.repo / "sig-rel").iterdir()))
                self.assertIsNone(self.sig_bytes())

    def test_sign_dir_inside_repo(self):
        """TL_SIGN_DIR 是绝对路径、但落在仓库根之内（仓库下的目录、仓库根本身）：sign、verify 判 1，不写入"""
        inside = self.repo / ".sig"
        inside.mkdir()
        for value in (str(inside), str(self.repo)):
            with self.subTest(TL_SIGN_DIR=value):
                env = {"TL_SIGN_DIR": value}
                self.assertExit(self.run_tl_env("sign", self.pid, "确认", "--json", env=env), EXIT_USAGE)
                self.assertExit(self.run_tl_env("verify", self.pid, "--json", env=env), EXIT_USAGE)
                self.assertEqual([], list(inside.iterdir()))
                self.assertFalse((self.repo / REPO_NAME).exists())

    def test_tl_now_invalid(self):
        """TL_NOW 不是带时区的 ISO 8601 时间：pack、sign 判 1，不写清单、不写签字"""
        for value in ("not-a-time", "2026-10-03T21:14:05", "2026-13-03T21:14:05+08:00",
                      "2026/10/03T21:14:05+08:00", "21:14:05+08:00"):
            with self.subTest(TL_NOW=value):
                env = {"TL_NOW": value}
                before = set(self.list_package_files())
                res = self.run_tl_env("pack", "--task", "qf-001", "--doc", "g1", "--step", "S1-SPD",
                                      "--main", MAIN_REL, "--summary", "x", "--json", env=env)
                self.assertExit(res, EXIT_USAGE)
                self.assertEqual(before, set(self.list_package_files()))
                self.assertExit(self.run_tl_env("sign", self.pid, "确认", "--json", env=env), EXIT_USAGE)
                self.assertIsNone(self.sig_bytes())

    def test_tl_now_valid_other_offset(self):
        """对照组：TL_NOW 为 2026-10-03T13:14:05+00:00 时 pack 成功，created_at 原样写入"""
        value = "2026-10-03T13:14:05+00:00"
        res = self.run_tl_env("pack", "--task", "qf-001", "--doc", "g0", "--step", "S0-INI",
                              "--main", MAIN_REL, "--summary", "x", "--json", env={"TL_NOW": value})
        self.assertExit(res, EXIT_OK)
        m = json.loads((self.packages_dir("qf-001") / "qf-001-g0-01.json").read_bytes().decode("utf-8"))
        self.assertEqual(value, m["created_at"])


# ====================================================================== R5 按任务核对

class TestR5VerifyTask(RegBase):
    """〔裁定 R5〕按任务核对：任务不合法或不存在判 1；没有任何待签包判 4；有签字而清单已不存在判 3。"""

    def test_task_without_packages(self):
        """任务下没有待签包（没有 packages/ 目录，或目录为空）→ 4"""
        self.assertExit(self.verify_task(), EXIT_UNSIGNED)
        self.packages_dir("qf-001").mkdir(parents=True)
        self.assertExit(self.verify_task(), EXIT_UNSIGNED)

    def test_signed_manifest_deleted(self):
        """两个包都已确认，删掉其中一个的清单 → 3"""
        p1 = self.pack_ok()[0]
        p2 = self.pack_ok(doc="g0", step="S0-INI")[0]
        self.sign_ok(p1, "确认")
        self.sign_ok(p2, "确认")
        self.assertExit(self.verify_task(), EXIT_OK)
        (self.packages_dir("qf-001") / f"{p1}.json").unlink()
        self.assertExit(self.verify_task(), EXIT_FINGERPRINT)

    def test_only_signed_manifest_deleted(self):
        """唯一的包已确认、清单被删（packages/ 已空）→ 3（有签字而清单不存在，3 重于"没有包"的 4）"""
        p1 = self.pack_ok()[0]
        self.sign_ok(p1, "确认")
        (self.packages_dir("qf-001") / f"{p1}.json").unlink()
        self.assertExit(self.verify_task(), EXIT_FINGERPRINT)

    def test_other_task_signature_not_counted(self):
        """别的任务有签字而清单已删：不影响本任务（本任务全部通过 → 0）；那个任务 → 3"""
        self.setup_task("qf-002")
        self.write_file("docs/qf-002.md", "x\n")
        p_other = self.pack_ok(task="qf-002", main="docs/qf-002.md", attach=())[0]
        self.sign_ok(p_other, "确认")
        p1 = self.pack_ok()[0]
        self.sign_ok(p1, "确认")
        (self.packages_dir("qf-002") / f"{p_other}.json").unlink()
        self.assertExit(self.verify_task("qf-001"), EXIT_OK)
        self.assertExit(self.verify_task("qf-002"), EXIT_FINGERPRINT)

    def test_task_invalid_or_missing(self):
        """任务编号不合法、或任务目录不存在 → 1"""
        for task in ("QF-001", "qf-01", "../qf-001", "qf-001/..", str(self.tmp), "qf-999"):
            with self.subTest(task=task):
                self.assertExit(self.verify_task(task), EXIT_USAGE)


# ====================================================================== R6 steps[].step

class TestR6StepsStep(RegBase):
    """〔裁定 R6〕进度卡 steps[].step 与 current_step 用同一格式（S＋0～7＋连字符＋大写字母）。"""

    def _progress_with_step(self, value):
        d = self.valid_progress()
        d["steps"][0]["step"] = value
        self.write_progress("qf-001", d)

    def test_check_steps_step_format(self):
        """check：S0-INI、S7-OPS 通过；小写、缺连字符、数字 8、两位数字、字母小写、空串 → 2"""
        for value, expected in (("S0-INI", EXIT_OK), ("S7-OPS", EXIT_OK), ("s0-ini", EXIT_FIELD),
                                ("S0INI", EXIT_FIELD), ("S8-INI", EXIT_FIELD), ("S10-INI", EXIT_FIELD),
                                ("S0-ini", EXIT_FIELD), ("", EXIT_FIELD)):
            with self.subTest(step=value):
                self._progress_with_step(value)
                self.assertExit(self.run_tl("check", "--task", "qf-001", "--json"), expected)

    def test_pack_and_status_reject_bad_steps_step(self):
        """steps[].step 不合法（S8-INI）的进度卡：pack 判 2 且不写清单，status --task 判 2"""
        self._progress_with_step("S8-INI")
        self.assertExit(self.pack(), EXIT_FIELD)
        self.assertEqual([], self.list_package_files())
        self.assertExit(self.run_tl("status", "--task", "qf-001", "--json"), EXIT_FIELD)


# ====================================================================== R7 status

class TestR7Status(RegBase):
    """〔裁定 R7〕status 的新状态（需重新打包／签后文件已变／清单不合格）；非法任务判 1；--task 显示进度卡各栏。"""

    def setUp(self):
        super().setUp()
        self.pid, self.manifest_path, self.m = self.pack_ok()

    def _status_ok(self):
        res = self.status_task()
        self.assertExit(res, EXIT_OK)
        return res.stdout

    def test_unsigned_file_changed(self):
        """从没签过、受审文件已变 → “需重新打包”"""
        self.modify(MAIN_REL)
        self.assertLineHas(self._status_ok(), self.pid, "需重新打包")

    def test_unsigned_manifest_changed(self):
        """从没签过、清单已变 → “需重新打包”"""
        self.edit_manifest_file(self.manifest_path, lambda m: m.update(summary="改过的摘要"))
        self.assertLineHas(self._status_ok(), self.pid, "需重新打包")

    def test_signed_manifest_changed(self):
        """签过之后清单已变 → “签后文件已变”"""
        self.sign_ok(self.pid, "确认")
        self.edit_manifest_file(self.manifest_path, lambda m: m.update(summary="改过的摘要"))
        self.assertLineHas(self._status_ok(), self.pid, "签后文件已变")

    def test_signed_manifest_rebuilt(self):
        """签过之后改附件、并按规格重做清单（清单自洽）→ “签后文件已变”"""
        self.sign_ok(self.pid, "确认")
        self.modify(ATTACH_REL)

        def refresh(m):
            for f in m["files"]:
                f["sha256"] = sha256_hex(self.path(f["path"]).read_bytes())
        self.edit_manifest_file(self.manifest_path, refresh, reseal=True)
        self.assertLineHas(self._status_ok(), self.pid, "签后文件已变")

    def test_invalid_or_missing_task(self):
        """status --task 的任务编号不合法或任务不存在 → 1（加不加 --json 都一样）"""
        for task in ("QF-001", "../qf-001", str(self.tmp), "qf-999"):
            with self.subTest(task=task):
                self.assertExit(self.run_tl("status", "--task", task), EXIT_USAGE)
                self.assertExit(self.run_tl("status", "--task", task, "--json"), EXIT_USAGE)

    def test_all_progress_fields_shown(self):
        """status --task 显示进度卡各栏：任务编号、通道、基线提交、当前步骤、条件步骤及就绪状态、令牌持有者、各步结果"""
        d = self.valid_progress()
        d["channel"] = "CH-MINOR"
        d["baseline_commit"] = "0f1e2d3c4b"
        d["current_step"] = "S2-DSN"
        d["conditions"].append({"code": "COND-API", "applicable": True, "readiness": "就绪"})
        d["token_holder"]["role"] = "REV"
        d["steps"].append({"step": "S1-SPD", "result": "结果不明"})
        self.write_progress("qf-001", d)
        text = self._status_ok()
        for value in ("qf-001", "CH-MINOR", "0f1e2d3c4b", "S2-DSN", "COND-UI", "适用未就绪", "COND-API", "就绪",
                      "REV", "S0-INI", "成功", "S1-SPD", "结果不明"):
            with self.subTest(value=value):
                self.assertIn(value, text)


# ====================================================================== R8 其他修正

class TestR8ErrorOutput(RegBase):
    """〔裁定 R8〕非 0 退出时标准错误至少一行中文原因；--json 时标准输出为一份 JSON：
    成功含 "ok": true；失败含 "ok": false、"code"、"errors"（中文原因列表）。"""

    def setUp(self):
        super().setUp()
        self.pid = self.pack_ok()[0]

    def _cases(self):
        return (
            (("pack", "--task", "qf-001", "--doc", "zz", "--step", "S1-SPD", "--main", MAIN_REL, "--summary", "x"),
             EXIT_USAGE),
            (("sign", "qf-001-g1-99", "确认"), EXIT_USAGE),
            (("sign", self.pid, "退回"), EXIT_USAGE),
            (("verify", self.pid), EXIT_UNSIGNED),
            (("verify", "qf-001-g1-99"), EXIT_USAGE),
            (("verify", "--task", "qf-999"), EXIT_USAGE),
            (("status", "--task", "qf-999"), EXIT_USAGE),
            (("check", "--task", "qf-999"), EXIT_USAGE),
        )

    def _assert_failure(self, args, expected):
        res = self.run_tl(*args)
        self.assertExit(res, expected)
        self.assertChinese(res)
        res = self.run_tl(*args, "--json")
        self.assertExit(res, expected)
        self.assertChinese(res)
        out = self.parse_json(res)
        self.assertIs(False, out.get("ok"), res.describe())
        self.assertEqual(expected, out.get("code"), res.describe())
        errors = out.get("errors")
        self.assertIsInstance(errors, list, res.describe())
        self.assertTrue(errors, res.describe())
        self.assertTrue(all(isinstance(e, str) for e in errors), res.describe())
        self.assertRegex("".join(errors), CJK_RE, res.describe())

    def test_failures_report_reason(self):
        """各子命令的非 0 退出（1、4）：标准错误有中文原因；--json 输出 ok=false、code、errors"""
        for args, expected in self._cases():
            with self.subTest(args=args):
                self._assert_failure(args, expected)

    def test_failures_2_3_5_report_reason(self):
        """退出码 2（check）、3（sign 遇改件）、5（重复签字）同样给出原因"""
        self.write_assumptions("qf-001", [self.valid_assumption(1, cost="极低")])
        self._assert_failure(("check", "--task", "qf-001"), EXIT_FIELD)
        other = self.pack_ok(doc="g0", step="S0-INI")[0]
        self.sign_ok(other, "确认")
        self._assert_failure(("sign", other, "确认"), EXIT_CORRUPT)
        self.modify(MAIN_REL)
        self._assert_failure(("sign", self.pid, "确认"), EXIT_FINGERPRINT)

    def test_argument_error_with_json(self):
        """参数缺失（sign 缺决定）且带 --json：退出码 1，标准输出仍是 ok=false 的 JSON"""
        res = self.run_tl("sign", self.pid, "--json")
        self.assertExit(res, EXIT_USAGE)
        self.assertChinese(res)
        out = self.parse_json(res)
        self.assertIs(False, out.get("ok"))
        self.assertEqual(EXIT_USAGE, out.get("code"))

    def test_success_has_ok_true(self):
        """成功时 --json 输出含 "ok": true（status、status --task、check、pack、sign、verify、verify --task）"""
        res = self.pack(doc="g0", step="S0-INI")
        self.assertExit(res, EXIT_OK)
        self.assertIs(True, self.parse_json(res).get("ok"))
        res = self.sign(self.pid, "确认")
        self.assertExit(res, EXIT_OK)
        self.assertIs(True, self.parse_json(res).get("ok"))
        self.sign_ok("qf-001-g0-01", "确认")
        for args in (("status", "--json"), ("status", "--task", "qf-001", "--json"),
                     ("check", "--task", "qf-001", "--json"), ("verify", self.pid, "--json"),
                     ("verify", "--task", "qf-001", "--json")):
            with self.subTest(args=args):
                res = self.run_tl(*args)
                self.assertExit(res, EXIT_OK)
                self.assertIs(True, self.parse_json(res).get("ok"), res.describe())


class TestR8PackNoOverwrite(RegBase):
    """〔裁定 R8〕pack 以独占创建方式写清单；编号已被占用时取下一个序号。
    编号冲突只能靠并发制造：同时启动 24 个 pack，做 5 轮（5 个文书代码）。正确的实现每轮都必然通过；
    会覆盖的实现不能保证每次暴露：参考实现故意改错后实测 10 次抓住 10 次（见交接卡；第五节 V7）。"""

    N = 24

    def _launch_packs(self, doc):
        env = {k: v for k, v in os.environ.items() if not k.startswith("TL_")}
        env.update({"TL_SIGN_DIR": str(self.sign_root), "TL_NOW": FIXED_NOW,
                    "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        procs = []
        for i in range(self.N):
            args = ["pack", "--task", "qf-001", "--doc", doc, "--step", "S1-SPD", "--main", MAIN_REL,
                    "--summary", f"并发摘要-{doc}-{i}", "--json"]
            procs.append(subprocess.Popen([sys.executable, str(TL_PATH), *args], cwd=str(self.repo), env=env,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        results = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=120)
            results.append((proc.returncode, stdout.decode("utf-8", errors="replace"),
                            stderr.decode("utf-8", errors="replace")))
        return results

    def test_concurrent_packs_get_distinct_numbers(self):
        """24 个 pack 同时运行（5 轮）：全部成功、包编号各不相同、清单文件一个不少，每个清单内容与它自己的推送一致"""
        for doc in ("as", "mn", "pa", "fx", "g2"):
            with self.subTest(doc=doc):
                ids = []
                for i, (code, stdout, stderr) in enumerate(self._launch_packs(doc)):
                    self.assertEqual(EXIT_OK, code, f"第 {i} 个 pack 失败：{stderr}")
                    out = json.loads(stdout)
                    pid = out["package_id"]
                    ids.append(pid)
                    m = json.loads((self.packages_dir("qf-001") / f"{pid}.json").read_bytes().decode("utf-8"))
                    self.assertEqual(f"并发摘要-{doc}-{i}", m["summary"], f"{pid} 的清单被别的 pack 覆盖了")
                    self.assertEqual(out["fingerprint_code"], m["fingerprint_code"])
                self.assertEqual(sorted(f"qf-001-{doc}-{k:02d}" for k in range(1, self.N + 1)), sorted(ids))
                self.assertEqual(self.N, len(list(self.packages_dir("qf-001").glob(f"qf-001-{doc}-*.json"))))


class TestR8SignLock(RegBase):
    """〔裁定 R8〕sign 追加前独占创建 signatures.jsonl.lock，最多等 10 秒，超时判 1；
    持锁后重读并校验链条再追加；写入后落盘；最后删除锁文件。"""

    def setUp(self):
        super().setUp()
        self.pid = self.pack_ok()[0]
        self.lock = self.sign_root / REPO_NAME / LOCK_NAME

    def test_lock_removed_after_sign(self):
        """正常签字后不留锁文件"""
        self.sign_ok(self.pid, "确认")
        self.assertFalse(self.lock.exists())

    def test_lock_timeout(self):
        """锁文件一直存在：sign 等待约 10 秒后判 1；不写入签字；不删别人的锁文件"""
        self.lock.parent.mkdir(parents=True)
        self.lock.write_bytes(b"")
        t0 = time.monotonic()
        res = self.sign(self.pid, "确认")
        elapsed = time.monotonic() - t0
        self.assertExit(res, EXIT_USAGE)
        self.assertChinese(res)
        self.assertGreaterEqual(elapsed, 9.0, f"应等待约 10 秒再放弃，实际 {elapsed:.1f} 秒")
        self.assertLess(elapsed, 60.0, f"等待过久：{elapsed:.1f} 秒")
        self.assertIsNone(self.sig_bytes())
        self.assertTrue(self.lock.exists(), "不得删除别的进程的锁文件")

    def test_lock_released_while_waiting(self):
        """锁文件在等待期间被释放（约 2 秒后）：sign 接着完成，退出码 0，只写一行，不留锁文件"""
        self.lock.parent.mkdir(parents=True)
        self.lock.write_bytes(b"")
        box = {}

        def run():
            t0 = time.monotonic()
            box["res"] = self.sign(self.pid, "确认")
            box["elapsed"] = time.monotonic() - t0

        t = threading.Thread(target=run)
        t.start()
        time.sleep(2.0)
        self.assertIsNone(self.sig_bytes(), "持锁期间不得写入")
        self.lock.unlink()
        t.join()
        self.assertExit(box["res"], EXIT_OK)
        self.assertGreaterEqual(box["elapsed"], 1.5, "应当等锁，而不是无视锁直接写")
        self.assertEqual(1, len(self.sig_lines()))
        self.assertFalse(self.lock.exists())

    def test_concurrent_signs_keep_chain(self):
        """6 个不同的包同时签字：全部成功，6 行，链条逐行正确，不留锁文件"""
        pids = [self.pid] + [self.pack_ok()[0] for _ in range(5)]
        results = {}

        def run(p):
            results[p] = self.sign(p, "确认")

        threads = [threading.Thread(target=run, args=(p,)) for p in pids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for p in pids:
            with self.subTest(package_id=p):
                self.assertExit(results[p], EXIT_OK)
        lines = self.sig_lines()
        self.assertEqual(6, len(lines))
        prev = ZERO64
        for line in lines:
            rec = json.loads(line.decode("utf-8"))
            self.assertEqual(prev, rec["prev_sha256"], "并发签字后链条分叉")
            prev = sha256_hex(line)
        self.assertEqual(sorted(pids), sorted(json.loads(x.decode("utf-8"))["package_id"] for x in lines))
        self.assertFalse(self.lock.exists())


class TestR8Misc(RegBase):
    """〔裁定 R8〕low_reason 不是字符串判 2；确认带理由判 1；pack 检查顺序；BOM。"""

    def test_low_reason_not_string(self):
        """定"低"时 low_reason 为数组、对象、数字、null、布尔：check 判 2（不得崩溃）"""
        for value in ([], ["纯命名"], {"a": 1}, 1, None, True):
            with self.subTest(low_reason=value):
                self.write_assumptions("qf-001", [self.valid_assumption(1, cost="低", low_reason=value)])
                res = self.run_tl("check", "--task", "qf-001", "--json")
                self.assertExit(res, EXIT_FIELD)
                self.assertNotIn("Traceback", res.stderr, res.describe())

    def test_confirm_with_reason(self):
        """决定为"确认"（或 confirm）时带了 --reason → 1，不写入"""
        pid = self.pack_ok()[0]
        for word in ("确认", "confirm"):
            with self.subTest(decision=word):
                self.assertExit(self.sign(pid, word, reason="其实挺好"), EXIT_USAGE)
                self.assertIsNone(self.sig_bytes())

    def test_pack_usage_checked_before_progress(self):
        """进度卡不合法（本应判 2）同时参数或文件有错：先报 1，不写清单"""
        d = self.valid_progress()
        d["channel"] = "CH-HOTFIX"
        self.write_progress("qf-001", d)
        (self.tmp / "outside.md").write_text("x\n", encoding="utf-8")
        self.write_file(".tianlong/work/qf-001/packages/notes.md", "x\n")
        cases = {
            "正文不存在": dict(main="docs/no-such.md", attach=()),
            "文书代码不合法": dict(doc="zz"),
            "附件在仓库外": dict(attach=("../outside.md",)),
            "附件在 packages/ 下": dict(attach=(".tianlong/work/qf-001/packages/notes.md",)),
        }
        for name, kw in cases.items():
            with self.subTest(case=name):
                self.assertExit(self.pack(**kw), EXIT_USAGE)
                self.assertEqual([], [p for p in self.list_package_files() if p.name != "notes.md"])
        self.assertExit(self.pack(), EXIT_FIELD, "对照：参数与文件都对时，才轮到进度卡判 2")

    def test_bom_progress(self):
        """进度卡开头带 BOM：check、status --task、pack 判 2，提示含“BOM”"""
        p = self.work_dir("qf-001") / "progress.json"
        p.write_bytes(BOM + p.read_bytes())
        for args in (("check", "--task", "qf-001"), ("status", "--task", "qf-001"),
                     ("pack", "--task", "qf-001", "--doc", "g1", "--step", "S1-SPD", "--main", MAIN_REL,
                      "--summary", "x")):
            with self.subTest(args=args[0]):
                res = self.run_tl(*args)
                self.assertExit(res, EXIT_FIELD)
                self.assertIn("BOM", res.stderr, res.describe())
        self.assertEqual([], self.list_package_files())

    def test_bom_manifest(self):
        """清单文件开头带 BOM（内容本身合格）：sign 判 2 且不写入，verify、status --task 判 2，提示含“BOM”"""
        pid, path, _ = self.pack_ok()
        path.write_bytes(BOM + path.read_bytes())
        res = self.sign(pid, "确认", json_out=False)
        self.assertExit(res, EXIT_FIELD)
        self.assertIn("BOM", res.stderr, res.describe())
        self.assertIsNone(self.sig_bytes())
        res = self.run_tl("verify", pid)
        self.assertExit(res, EXIT_FIELD)
        self.assertIn("BOM", res.stderr, res.describe())
        self.assertExit(self.status_task(), EXIT_FIELD)


if __name__ == "__main__":
    unittest.main()
