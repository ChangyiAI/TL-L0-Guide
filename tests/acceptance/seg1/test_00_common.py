# -*- coding: utf-8 -*-
"""通用约定（接口规格第 2 节）：程序位置、仓库根、--json 输出。"""
import unittest

from seg1_support import EXIT_OK, EXIT_USAGE, TL_PATH, Seg1TestCase


class TestCommon(Seg1TestCase):

    def test_program_exists(self):
        """src/bin/tl.py 存在（接口规格第 2 节"运行方式"）"""
        self.assertTrue(TL_PATH.is_file())

    def test_outside_git_repo_is_error(self):
        """不在 git 仓库内即报错：退出码非 0，且不写任何文件（接口规格第 2 节"仓库根"；退出码见待定 Q3）"""
        nogit = self.tmp / "not-a-repo"
        nogit.mkdir()
        # 让 git 不往临时目录的上层找仓库
        env = {"GIT_CEILING_DIRECTORIES": str(self.tmp)}
        for args in (["status", "--json"], ["check", "--task", "qf-001", "--json"],
                     ["pack", "--task", "qf-001", "--doc", "g1", "--step", "S1-SPD",
                      "--main", "a.md", "--summary", "x", "--json"]):
            with self.subTest(args=args):
                (nogit / "a.md").write_text("x\n", encoding="utf-8")
                res = self.run_tl(*args, cwd=nogit, extra_env=env)
                self.assertNotEqual(EXIT_OK, res.code, res.describe())
                self.assertFalse((nogit / ".tianlong").exists(), "不在仓库内时不得写出 .tianlong/")

    def test_json_output_is_json(self):
        """--json 时标准输出是一份合法 JSON（status、check、pack、sign、verify 成功时；待定 Q2）"""
        self.setup_task()
        for args in (["status", "--json"], ["status", "--task", "qf-001", "--json"],
                     ["check", "--task", "qf-001", "--json"]):
            with self.subTest(args=args):
                res = self.run_tl(*args)
                self.assertExit(res, EXIT_OK)
                self.parse_json(res)
        res = self.pack()
        self.assertExit(res, EXIT_OK)
        pid = self.parse_json(res)["package_id"]
        res = self.sign(pid, "确认")
        self.assertExit(res, EXIT_OK)
        self.parse_json(res)
        for args in (["verify", pid, "--json"], ["verify", "--task", "qf-001", "--json"]):
            with self.subTest(args=args):
                res = self.run_tl(*args)
                self.assertExit(res, EXIT_OK)
                self.parse_json(res)


class TestOutsideGitRepo(Seg1TestCase):
    """〔裁定 Q3〕不在 git 仓库内：退出码为 1，且不写任何文件。"""

    def test_outside_git_repo_exit_code_1(self):
        """五个子命令在非 git 目录运行：退出码都为 1；目录里不多出任何文件，签字目录不被写入"""
        nogit = self.tmp / "not-a-repo"
        nogit.mkdir()
        (nogit / "a.md").write_text("x\n", encoding="utf-8")
        env = {"GIT_CEILING_DIRECTORIES": str(self.tmp)}
        cases = (
            ["status", "--json"],
            ["status", "--task", "qf-001", "--json"],
            ["check", "--task", "qf-001", "--json"],
            ["pack", "--task", "qf-001", "--doc", "g1", "--step", "S1-SPD",
             "--main", "a.md", "--summary", "x", "--json"],
            ["sign", "qf-001-g1-01", "确认", "--json"],
            ["verify", "qf-001-g1-01", "--json"],
            ["verify", "--task", "qf-001", "--json"],
        )
        for args in cases:
            with self.subTest(args=args):
                res = self.run_tl(*args, cwd=nogit, extra_env=env)
                self.assertExit(res, EXIT_USAGE)
                self.assertEqual(["a.md"], sorted(p.name for p in nogit.iterdir()), "不得写出任何文件")
                self.assertEqual([], list(self.sign_root.iterdir()), "不得写入签字目录")


if __name__ == "__main__":
    unittest.main()
