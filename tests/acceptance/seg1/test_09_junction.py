# -*- coding: utf-8 -*-
"""〔收尾待办 N9〕目录联接（junction）用例：与三个符号链接用例一一对应，只把"符号链接"换成"目录联接"。

背景：本机 Windows 普通账户没有建符号链接的特权（WinError 1314），下列三个锁定用例在 Windows 上被跳过；
而目录联接不需要特权，这条路在本机实际存在。
  ① test_07_regression.TestR2ManifestStructure.test_link_to_outside       （规格 10.2）
  ② test_08_regression2.TestV5PackagesPath.test_link_into_packages         （规格 11.3）
  ③ test_08_regression2.TestV5SignFileLocation.test_repo_subdir_links_into_repo（规格 11.1）

目录联接只能指向目录，不能指向单个文件，因此：
  ① 原用例是"文件链接 → 仓库外的文件"，这里改为"联接 → 仓库外的目录"，受审路径经联接指到该目录里的同一个文件；
  ② 原用例有目录链接、文件链接两个子场景，这里只有目录子场景；
  ③ 原用例本来就是目录链接，一一对应。
场景搭建、执行的命令、期望的退出码和断言，除上述不得不改之处外，与原用例相同。

规则：
- 只在 Windows 上运行；其他系统跳过（目录联接仅 Windows）。Windows 上建联接失败按失败报告，不跳过。
- 联接用 _winapi.CreateJunction(<目标绝对路径>, <联接路径>) 建立；目标一律在本用例的临时目录内。
- 清理时先用 os.rmdir 删掉联接本身，再清理临时目录，防止删除操作顺着联接删掉目标里的内容。
- 沿用 seg1_support.py 的公共部件（签字目录强制落在本用例的临时目录内）。
"""
import json
import os
import unittest

from seg1_support import (
    ATTACH_REL, EXIT_FIELD, EXIT_USAGE, FIXED_NOW, MAIN_REL, REPO_NAME, ZERO64,
    Seg1TestCase, seal_manifest, sha256_hex,
)

IS_WINDOWS = os.name == "nt"
LOCK_NAME = "signatures.jsonl.lock"


@unittest.skipUnless(IS_WINDOWS, "目录联接仅 Windows")
class JunctionBase(Seg1TestCase):
    """本文件的公共部件。"""

    PID = "qf-001-g1-01"

    def setUp(self):
        super().setUp()
        self.setup_task()

    def junction(self, target, link):
        """建目录联接 link → target。target 必须是本用例临时目录内已存在的目录；建不成按失败报告，不跳过。
        登记清理：用 os.rmdir 只删联接本身（先于临时目录的整体清理执行）。"""
        target = os.path.abspath(str(target))
        link = os.path.abspath(str(link))
        self.assertTrue(os.path.normcase(target).startswith(os.path.normcase(str(self.tmp))),
                        f"测试护栏：联接目标必须在本用例临时目录内，拒绝建立：{target}")
        self.assertTrue(os.path.isdir(target), f"联接目标必须是已存在的目录：{target}")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        try:
            import _winapi
            _winapi.CreateJunction(target, link)
        except OSError as e:
            self.fail(f"建立目录联接失败（Windows 普通账户不需要特权，不应失败）：{link} → {target}：{e}")
        self.addCleanup(os.rmdir, link)
        return link

    # ------------------------------------------------ 与原用例相同的判定

    def verify(self, pid):
        return self.run_tl("verify", pid, "--json")

    def status_task(self, task="qf-001"):
        return self.run_tl("status", "--task", task)

    def assertLineHas(self, text, package_id, state):
        lines = [line for line in text.splitlines() if package_id in line]
        self.assertTrue(lines, f"输出中找不到包编号 {package_id}\n{text}")
        self.assertTrue(any(state in line for line in lines), f"包编号 {package_id} 所在行应显示“{state}”\n{text}")

    def clear_packages(self):
        for p in self.list_package_files():
            p.unlink()

    def write_manifest(self, m, filename=None):
        name = filename or m["package_id"]
        return self.write_file(f".tianlong/work/qf-001/packages/{name}.json",
                               json.dumps(m, ensure_ascii=False, indent=2) + "\n")


# ====================================================================== ① 对应 test_link_to_outside

class TestN9JunctionToOutside(JunctionBase):
    """〔收尾待办 N9 ①〕对应 test_07_regression.TestR2ManifestStructure.test_link_to_outside（规格 10.2）。"""

    def setUp(self):
        super().setUp()
        # 与原用例的类夹具相同：仓库外一个文件，两个任务的 packages/ 下各一个文件
        self.outside_dir = self.tmp / "outside-dir"
        self.outside_dir.mkdir()
        self.outside = self.outside_dir / "outside.md"
        self.outside.write_text("仓库外的文件\n", encoding="utf-8")
        self.write_file(".tianlong/work/qf-001/packages/notes.md", "本任务 packages 下的文件\n")
        self.write_file(".tianlong/work/qf-002/packages/notes.md", "别的任务 packages 下的文件\n")

    def entry(self, path, role, sha=None):
        if sha is None:
            p = self.repo / path
            sha = sha256_hex(p.read_bytes()) if p.is_file() else ZERO64
        return {"path": path, "sha256": sha, "role": role}

    def hand_manifest(self, package_id, files, task_id="qf-001"):
        return seal_manifest({
            "package_id": package_id, "doc_type": "规格确认书", "task_id": task_id, "step": "S1-SPD",
            "summary": "手写清单", "created_at": FIXED_NOW, "created_by": "导引技能",
            "files": sorted(files, key=lambda f: f["path"]),
        })

    def test_junction_to_outside(self):
        """文件项经仓库内的目录联接指向仓库外的文件 → sign、verify、status 都判 2，sign 不写入，status 显示“清单不合格”"""
        self.junction(self.outside_dir, self.path("docs/outside-link"))
        good = [self.entry(MAIN_REL, "正文"), self.entry(ATTACH_REL, "附件")]
        m = self.hand_manifest(self.PID, good + [self.entry("docs/outside-link/outside.md", "附件",
                                                            sha256_hex(self.outside.read_bytes()))])
        self.clear_packages()
        self.write_manifest(m, filename=self.PID)
        res = self.sign(self.PID, "确认")
        self.assertExit(res, EXIT_FIELD, "sign 应判 2")
        self.assertIsNone(self.sig_bytes(), "结构不合格时不得写入签字")
        self.assertExit(self.verify(self.PID), EXIT_FIELD, "verify 应判 2")
        res = self.status_task()
        self.assertExit(res, EXIT_FIELD, "status --task 应判 2")
        self.assertLineHas(res.stdout, self.PID, "清单不合格")


# ====================================================================== ② 对应 test_link_into_packages

class TestN9JunctionIntoPackages(JunctionBase):
    """〔收尾待办 N9 ②〕对应 test_08_regression2.TestV5PackagesPath.test_link_into_packages（规格 11.3）。"""

    NOTES = ".tianlong/work/qf-001/packages/notes.md"

    def setUp(self):
        super().setUp()
        self.write_file(self.NOTES, "packages 下的文件\n")

    def file_sha(self, rel):
        return sha256_hex(self.path(rel).read_bytes())

    def test_junction_into_packages(self):
        """经仓库内的目录联接 docs/pk → packages/ 指向 packages/notes.md：sign、verify、status 判 2，sign 不写入"""
        self.junction(self.packages_dir("qf-001"), self.path("docs/pk"))
        variant = "docs/pk/notes.md"
        self.clear_packages()
        m = seal_manifest({
            "package_id": self.PID, "doc_type": "规格确认书", "task_id": "qf-001", "step": "S1-SPD",
            "summary": "手写清单", "created_at": FIXED_NOW, "created_by": "导引技能",
            "files": sorted(({"path": p, "sha256": s, "role": r} for p, r, s in
                             ((MAIN_REL, "正文", self.file_sha(MAIN_REL)),
                              (variant, "附件", self.file_sha(self.NOTES)))), key=lambda f: f["path"]),
        })
        self.write_manifest(m)
        res = self.sign(self.PID, "确认")
        self.assertExit(res, EXIT_FIELD, "sign 应判 2")
        self.assertIsNone(self.sig_bytes(), "不得写入签字")
        self.assertExit(self.verify(self.PID), EXIT_FIELD, "verify 应判 2")
        self.assertExit(self.status_task(), EXIT_FIELD, "status --task 应判 2")


# ====================================================================== ③ 对应 test_repo_subdir_links_into_repo

class TestN9SignSubdirJunction(JunctionBase):
    """〔收尾待办 N9 ③〕对应 test_08_regression2.TestV5SignFileLocation.test_repo_subdir_links_into_repo（规格 11.1）。"""

    def setUp(self):
        super().setUp()
        self.pid = self.pack_ok()[0]

    def test_repo_subdir_junction_into_repo(self):
        """签字根目录本身在仓库外，但其下的 <仓库名> 是指向仓库内的目录联接：sign 判 1、verify 判 1，
        联接目标里不写入；仓库内任何位置都不出现签字文件和锁文件"""
        target = self.repo / ".sig-in-repo"
        target.mkdir()
        self.junction(target, self.sign_root / REPO_NAME)
        self.assertExit(self.sign(self.pid, "确认"), EXIT_USAGE)
        self.assertExit(self.verify(self.pid), EXIT_USAGE)
        self.assertEqual([], list(target.iterdir()), "不得经联接把签字写进仓库")
        found = [str(p) for p in self.repo.rglob("*") if p.name in ("signatures.jsonl", LOCK_NAME)]
        self.assertEqual([], found, "仓库内不得出现签字文件或锁文件")


if __name__ == "__main__":
    unittest.main()
