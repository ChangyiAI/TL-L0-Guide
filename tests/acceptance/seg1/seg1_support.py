# -*- coding: utf-8 -*-
"""段1 验收用例的公共部件。

- 每个用例在临时目录里新建一个 git 仓库作为测试项目；
- 用 TL_SIGN_DIR 指向临时签字根目录、用 TL_NOW 固定时间，绝不触碰真实签字目录；
- 通过子进程调用 src/bin/tl.py，按接口规格第 2 节的退出码判定；
- 清单指纹、文件指纹、签字链按接口规格第 4 节在本文件里独立重算，不调用被测程序的任何代码。

依据：docs/spec/04_段1接口规格_v0.1.md。凡需要猜的地方，见 open_questions.md，注释里以"待定 Qn"标出。
"""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------- 位置与常量

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # tests/acceptance/seg1 → 仓库根
TL_PATH = REPO_ROOT / "src" / "bin" / "tl.py"
HANDOVER_TEMPLATE = REPO_ROOT / "src" / "templates" / "07_交接卡.md"

FIXED_NOW = "2026-10-03T21:14:05+08:00"  # 接口规格第 2 节示例时间
REPO_NAME = "tl-test-proj"  # 测试项目的仓库名（仓库根文件夹名）
ZERO64 = "0" * 64

# 接口规格第 2 节：退出码
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FIELD = 2
EXIT_FINGERPRINT = 3
EXIT_UNSIGNED = 4
EXIT_CORRUPT = 5

# 接口规格第 3 节：文书代码
DOC_CODES = {
    "g0": "需求确认书",
    "g1": "规格确认书",
    "g2": "发布确认书",
    "fx": "简式确认书",
    "mn": "改动登记单",
    "pa": "过程审批单",
    "as": "资产入库确认",
}
TASK_ID_RE = re.compile(r"^[a-z]{2,8}-[0-9]{3}$")
PACKAGE_ID_RE = re.compile(r"^[a-z]{2,8}-[0-9]{3}-(g0|g1|g2|fx|mn|pa|as)-[0-9]{2}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# 接口规格 4.2：清单字段
MANIFEST_FIELDS = (
    "package_id", "doc_type", "task_id", "step", "summary",
    "created_at", "created_by", "files", "manifest_sha256", "fingerprint_code",
)
# 接口规格 4.3：签字记录字段
SIGNATURE_FIELDS = (
    "package_id", "decision", "fingerprint_code", "reason",
    "signed_at", "signer", "prev_sha256",
)
# 接口规格 4.1：进度卡必填字段
PROGRESS_REQUIRED = (
    "task_id", "channel", "baseline_commit", "current_step",
    "conditions", "token_holder", "steps",
)
# 接口规格 4.4：定"低"时的四个固定理由
LOW_REASONS = ("纯命名", "纯格式", "不涉状态", "不涉接口数据与权限")

# 交接卡六个栏目（src/templates/07_交接卡.md 表格第一列的开头文字）
HANDOVER_COLUMNS = ("本步", "产出", "检查结果", "未决事项", "下一步", "令牌移交")

# 标准测试任务里的文件（仿照 src/templates/09_待签包清单_示例.json）
MAIN_REL = ".tianlong/work/qf-001/forms/02_规格确认书_v1.md"
ATTACH_REL = ".specify/specs/qf-001/spec.md"


# ---------------------------------------------------------------- 按规格独立重算

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def spec_manifest_sha256(manifest: dict) -> str:
    """接口规格 4.2：去掉 manifest_sha256、fingerprint_code 两个字段，
    键名排序、紧凑格式（逗号、冒号，无空格）、UTF-8、非 ASCII 不转义，再算 SHA-256。"""
    body = {k: v for k, v in manifest.items() if k not in ("manifest_sha256", "fingerprint_code")}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_hex(text.encode("utf-8"))


def seal_manifest(manifest: dict) -> dict:
    """按规格补上 manifest_sha256 与 fingerprint_code（测试自造清单时用）。"""
    m = dict(manifest)
    m["manifest_sha256"] = spec_manifest_sha256(m)
    m["fingerprint_code"] = m["manifest_sha256"][:8]
    return m


def _force_rmtree(path):
    def _retry(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:
        shutil.rmtree(path, onerror=_retry)


# ---------------------------------------------------------------- 运行结果

class TlResult:
    def __init__(self, args, returncode, stdout, stderr):
        self.args = args
        self.code = returncode
        self.stdout = stdout
        self.stderr = stderr

    def describe(self):
        return (
            f"\n命令：tl {' '.join(self.args)}\n退出码：{self.code}"
            f"\n标准输出：\n{self.stdout}\n标准错误：\n{self.stderr}"
        )


# ---------------------------------------------------------------- 用例基类

class Seg1TestCase(unittest.TestCase):
    """每个用例：一个新的临时 git 仓库 ＋ 一个临时签字根目录。"""

    maxDiff = None

    def setUp(self):
        # 被测程序不存在时直接判失败。不能让它"碰巧通过"：
        # python 找不到脚本时退出码为 2，恰与规格中的"记录字段不合法"同码。
        if not TL_PATH.is_file():
            self.fail(f"被测程序不存在：{TL_PATH}（段1 尚未实现时本用例失败属预期）")

        self.tmp = Path(tempfile.mkdtemp(prefix="tl-seg1-")).resolve()
        self.addCleanup(_force_rmtree, str(self.tmp))

        self.repo = self.tmp / REPO_NAME
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(self.repo), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 签字根目录（TL_SIGN_DIR）存在，<仓库名> 子目录不预先建立（由 sign 第 4 步自动建立）
        self.sign_root = self.tmp / "sign-root"
        self.sign_root.mkdir()
        self.sig_file = self.sign_root / REPO_NAME / "signatures.jsonl"

    # ------------------------------------------------ 调用被测程序

    def run_tl(self, *args, sign_dir=None, cwd=None, extra_env=None):
        sign_dir = Path(sign_dir) if sign_dir is not None else self.sign_root
        # 安全护栏：签字目录必须落在本用例的临时目录内
        self.assertTrue(
            str(sign_dir.resolve()).startswith(str(self.tmp)),
            f"测试签字目录不在临时目录内，拒绝运行：{sign_dir}",
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("TL_")}
        env["TL_SIGN_DIR"] = str(sign_dir)
        env["TL_NOW"] = FIXED_NOW
        env["PYTHONUTF8"] = "1"  # 待定 Q16：输出编码
        env["PYTHONIOENCODING"] = "utf-8"
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            [sys.executable, str(TL_PATH), *args],
            cwd=str(cwd if cwd is not None else self.repo),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        return TlResult(
            list(args), proc.returncode,
            proc.stdout.decode("utf-8", errors="replace"),
            proc.stderr.decode("utf-8", errors="replace"),
        )

    def assertExit(self, res: TlResult, expected: int, msg: str = ""):
        if res.code != expected:
            self.fail(f"{msg}\n期望退出码 {expected}，实际 {res.code}{res.describe()}")

    def parse_json(self, res: TlResult):
        try:
            return json.loads(res.stdout)
        except ValueError as e:
            self.fail(f"--json 输出不是合法 JSON：{e}{res.describe()}")

    # ------------------------------------------------ 仓库内文件

    def path(self, rel: str) -> Path:
        return self.repo / Path(rel)

    def write_file(self, rel: str, content):
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        p.write_bytes(data)
        return p

    def work_dir(self, task_id: str) -> Path:
        return self.repo / ".tianlong" / "work" / task_id

    def packages_dir(self, task_id: str) -> Path:
        return self.work_dir(task_id) / "packages"

    def list_package_files(self):
        base = self.repo / ".tianlong" / "work"
        if not base.exists():
            return []
        return sorted(p for p in base.glob("*/packages/*.json"))

    # ------------------------------------------------ 进度卡

    def valid_progress(self, task_id: str = "qf-001") -> dict:
        """段1 设计 v0.5 第 2 节的示例进度卡（任务编号替换为 task_id）。"""
        return {
            "task_id": task_id,
            "channel": "CH-FEAT",
            "pre_phase": {"used": True, "solution_pack": "SOL-v1.0"},
            "baseline_commit": "a1b2c3d",
            "current_step": "S1-SPD",
            "skipped_steps": [],
            "conditions": [
                {
                    "code": "COND-UI",
                    "applicable": True,
                    "readiness": "适用未就绪",
                    "exception_ref": f".tianlong/work/{task_id}/forms/06_过程审批单_003.md",
                }
            ],
            "token_holder": {
                "role": "ARC",
                "client": "Claude 对话端",
                "model": "（装机日核准）",
                "since": "2026-10-01T09:00:00+08:00",
            },
            "steps": [
                {
                    "step": "S0-INI",
                    "result": "成功",
                    "handover": "forms/07_交接卡_s0.md",
                    "signature": f"{task_id}-g0-01",
                }
            ],
            "handovers": [],
            "tech_validations": [],
            "isolation_downgrade": {"active": False},
        }

    def write_progress(self, task_id: str, data):
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        return self.write_file(f".tianlong/work/{task_id}/progress.json", text)

    # ------------------------------------------------ 假设日志

    def valid_assumption(self, n: int = 1, **overrides) -> dict:
        """段1 设计 v0.5 第 4 节的全部字段。"""
        item = {
            "id": f"A-{n:03d}",
            "step": "S3-DEV",
            "task_item": "T-01",
            "decision": "日志文件名统一用小写",
            "reason": "与目录命名规范一致",
            "cost": "中",
            "files": ["src/app.py"],
            "recorded_by": "DEV:Codex",
            "recorded_at": "2026-10-02T10:00:00+08:00",
            "s6_route": "归档",
        }
        item.update(overrides)
        return item

    def write_assumptions(self, task_id: str, items):
        lines = []
        for it in items:
            lines.append(it if isinstance(it, str) else json.dumps(it, ensure_ascii=False))
        text = "".join(line + "\n" for line in lines)
        return self.write_file(f".tianlong/work/{task_id}/assumptions.jsonl", text)

    # ------------------------------------------------ 交接卡

    def handover_text(self, task_id: str = "qf-001", blank=(), whitespace=(), drop=()) -> str:
        """以 src/templates/07_交接卡.md 为底，填满文书头与六个栏目。
        blank：置为空的栏目；whitespace：只填空白的栏目；drop：整行删去的栏目。"""
        self.assertTrue(HANDOVER_TEMPLATE.is_file(), f"交接卡模板不存在：{HANDOVER_TEMPLATE}")
        header_values = {
            "任务编号": task_id,
            "通道": "CH-FEAT",
            "文书版本": "v1",
            "基线提交": "a1b2c3d",
            "所基于的上游文书": f"{task_id}-g0-01",
            "待签包编号": f"{task_id}-g1-01",
        }
        column_values = {
            "本步": f"S1-SPD，CH-FEAT，{task_id}",
            "产出": "forms/02_规格确认书_v1.md（sha256 前 8 位 3f9a1c7e）",
            "检查结果": "tl check 通过；放行人 REV",
            "未决事项": "无",
            "下一步": "DEV 读规格确认书与接口登记表",
            "令牌移交": "ARC → DEV，2026-10-03T21:30:00+08:00",
        }
        out, found = [], set()
        for line in HANDOVER_TEMPLATE.read_text(encoding="utf-8").splitlines():
            label = self._row_label(line)
            col = next((c for c in HANDOVER_COLUMNS if label and label.startswith(c)), None)
            head = next((h for h in header_values if label and label.startswith(h)), None)
            if col:
                found.add(col)
                if col in drop:
                    continue
                if col in blank:
                    value = ""
                elif col in whitespace:
                    value = "　 "
                else:
                    value = column_values[col]
                out.append(f"| {label} | {value} |")
            elif head:
                out.append(f"| {label} | {header_values[head]} |")
            else:
                out.append(line)
        self.assertEqual(set(HANDOVER_COLUMNS), found, "交接卡模板中没找齐六个栏目，测试夹具需随模板更新")
        return "\n".join(out) + "\n"

    @staticmethod
    def _row_label(line: str):
        s = line.strip()
        if not s.startswith("|") or s.startswith("| :--") or s.startswith("| 栏 ") or s.startswith("| 文书头"):
            return None
        cells = s.strip("|").split("|")
        return cells[0].strip() if cells else None

    def write_handover(self, task_id: str = "qf-001", name: str = "07_交接卡_s0.md", **kw):
        return self.write_file(f".tianlong/work/{task_id}/forms/{name}", self.handover_text(task_id, **kw))

    # ------------------------------------------------ 标准测试任务

    def setup_task(self, task_id: str = "qf-001"):
        """建一个合法的任务：进度卡、假设日志、交接卡、例外批准单；qf-001 另建正文与附件。"""
        self.write_progress(task_id, self.valid_progress(task_id))
        self.write_assumptions(task_id, [self.valid_assumption(1)])
        self.write_handover(task_id)
        self.write_file(f".tianlong/work/{task_id}/forms/06_过程审批单_003.md", "# 过程审批单\n\n（测试夹具）\n")
        if task_id == "qf-001":
            self.write_file(MAIN_REL, "# 规格确认书\n\n## 一、正常表现\n\n测试正文。\n")
            self.write_file(ATTACH_REL, "# spec\n\nattachment body\n")

    # ------------------------------------------------ pack

    def pack(self, task="qf-001", doc="g1", step="S1-SPD", main=MAIN_REL, attach=(ATTACH_REL,),
             summary="规格确认书第 1 版，共十五节", json_out=True):
        args = ["pack", "--task", task, "--doc", doc, "--step", step, "--main", main]
        for a in attach:
            args += ["--attach", a]
        args += ["--summary", summary]
        if json_out:
            args.append("--json")
        return self.run_tl(*args)

    def pack_ok(self, **kw):
        """运行 pack 并要求成功；返回 (包编号, 清单文件路径, 清单内容)。
        清单文件按"运行前后 packages/ 下新增的文件"找，不依赖 --json 的输出结构。"""
        before = set(self.list_package_files())
        res = self.pack(**kw)
        self.assertExit(res, EXIT_OK, "pack 应成功")
        new = sorted(set(self.list_package_files()) - before)
        self.assertEqual(1, len(new), f"pack 应恰好新增一个清单文件，实际新增：{new}{res.describe()}")
        path = new[0]
        manifest = json.loads(path.read_bytes().decode("utf-8"))
        return manifest["package_id"], path, manifest

    # ------------------------------------------------ sign 与签字记录

    def sign(self, package_id, decision="确认", reason=None, json_out=True, **kw):
        args = ["sign", package_id, decision]
        if reason is not None:
            args += ["--reason", reason]
        if json_out:
            args.append("--json")
        return self.run_tl(*args, **kw)

    def sign_ok(self, package_id, decision="确认", reason=None):
        res = self.sign(package_id, decision, reason)
        self.assertExit(res, EXIT_OK, f"sign {package_id} {decision} 应成功")
        return res

    def sig_bytes(self):
        return self.sig_file.read_bytes() if self.sig_file.exists() else None

    def sig_lines(self):
        """签字记录各行原文（bytes，不含行尾换行）。"""
        data = self.sig_bytes()
        if not data:
            return []
        self.assertTrue(data.endswith(b"\n"), "签字记录最后一行应以换行结尾")
        return data[:-1].split(b"\n")

    def sig_records(self):
        return [json.loads(line.decode("utf-8")) for line in self.sig_lines()]

    def append_sig_record(self, record: dict, chain_ok=True):
        """在签字记录末尾手工追加一行（模拟伪造）。chain_ok=True 时按规格正确填 prev_sha256。"""
        lines = self.sig_lines()
        rec = dict(record)
        if chain_ok:
            rec["prev_sha256"] = sha256_hex(lines[-1]) if lines else ZERO64
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.sig_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.sig_file, "ab") as f:
            f.write(line + b"\n")

    def replace_sig_line(self, index: int, new_line: bytes):
        lines = self.sig_lines()
        lines[index] = new_line
        self.sig_file.write_bytes(b"\n".join(lines) + b"\n")

    def tamper_sig_line(self, index: int):
        """改动签字记录第 index 行的内容（改 reason），其余行原样保留。"""
        rec = json.loads(self.sig_lines()[index].decode("utf-8"))
        rec["reason"] = (rec.get("reason") or "") + "（被改）"
        self.replace_sig_line(index, json.dumps(rec, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def forged_record(self, package_id, fingerprint_code, decision="已确认"):
        return {
            "package_id": package_id,
            "decision": decision,
            "fingerprint_code": fingerprint_code,
            "reason": "",
            "signed_at": FIXED_NOW,
            "signer": "APR:Fred",
        }

    # ------------------------------------------------ 其他

    def modify(self, rel: str, extra: str = "签后追加的一行。\n"):
        p = self.path(rel)
        p.write_bytes(p.read_bytes() + extra.encode("utf-8"))

    # ------------------------------------------------ 〔v0.4〕接口规格第 10 节回归用例的公共部件（只增不改）

    def run_tl_env(self, *args, env=None, cwd=None):
        """〔v0.4〕按指定环境变量运行 tl：先按 run_tl 的做法建默认环境（TL_SIGN_DIR 指向本用例的签字根目录、
        TL_NOW 固定），再用 env 覆盖；env 中值为 None 的变量从环境中删去。
        安全护栏：凡是 sign，TL_SIGN_DIR 必须非空，且（相对路径按运行目录解析后）落在本用例临时目录内，
        绝不让测试碰到真实签字目录。其他子命令只读签字目录，不设此限。"""
        run_cwd = Path(cwd) if cwd is not None else self.repo
        full = {k: v for k, v in os.environ.items() if not k.startswith("TL_")}
        full.update({"TL_SIGN_DIR": str(self.sign_root), "TL_NOW": FIXED_NOW,
                     "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        for key, value in (env or {}).items():
            if value is None:
                full.pop(key, None)
            else:
                full[key] = value
        if args and args[0] == "sign":
            sign_dir = full.get("TL_SIGN_DIR", "")
            self.assertTrue(sign_dir, "测试护栏：sign 必须带非空的 TL_SIGN_DIR")
            p = Path(sign_dir)
            p = p if p.is_absolute() else run_cwd / p
            self.assertTrue(str(p.resolve()).startswith(str(self.tmp)),
                            f"测试护栏：签字目录不在临时目录内，拒绝运行：{sign_dir}")
        proc = subprocess.run(
            [sys.executable, str(TL_PATH), *args],
            cwd=str(run_cwd), env=full,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        )
        return TlResult(
            list(args), proc.returncode,
            proc.stdout.decode("utf-8", errors="replace"),
            proc.stderr.decode("utf-8", errors="replace"),
        )

    def forged_record_v4(self, package_id, fingerprint_code, manifest_sha256, decision="已确认"):
        """〔v0.4 裁定 R3〕格式完整的签字记录（含 manifest_sha256），用于伪造、重复签字等用例。"""
        rec = self.forged_record(package_id, fingerprint_code, decision)
        rec["manifest_sha256"] = manifest_sha256
        return rec

    # ------------------------------------------------ 〔v0.5〕接口规格第 11 节回归用例的公共部件（只增不改）

    def rewrite_sig_records(self, records):
        """〔v0.5〕用给定记录重写整份签字记录，按规格重新串好 prev_sha256（模拟"整份重写、链条自洽"的篡改）。"""
        out, prev = [], ZERO64
        for rec in records:
            r = dict(rec)
            r["prev_sha256"] = prev
            line = json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            out.append(line)
            prev = sha256_hex(line)
        self.sig_file.parent.mkdir(parents=True, exist_ok=True)
        self.sig_file.write_bytes(b"".join(line + b"\n" for line in out))

    def symlink_or_skip(self, target, link, target_is_directory=False):
        """〔v0.5〕建符号链接；本机没有建链接的特权（如普通 Windows 账户，WinError 1314）时跳过本用例并写明原因。"""
        Path(link).parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(str(target), str(link), target_is_directory=target_is_directory)
        except (OSError, NotImplementedError) as e:
            self.skipTest(f"本机无法建立符号链接（需要特权）：{e}")

    def make_unreadable_or_skip(self, path):
        """〔v0.5〕让另一个进程读不了 path：Windows 上由本进程对整个文件加字节锁（模拟被独占打开），
        其他系统上去掉全部权限。随后用子进程实读一次确认；读得到（例如以 root 运行）就跳过本用例并写明原因。
        返回一个恢复函数（本用例结束时也会自动恢复）。"""
        path = Path(path)
        size = max(path.stat().st_size, 1)
        if os.name == "nt":
            import msvcrt
            fh = open(path, "r+b")
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, size)

            def restore():
                if not fh.closed:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, size)
                    fh.close()
        else:
            mode = stat.S_IMODE(path.stat().st_mode)
            os.chmod(path, 0)

            def restore():
                os.chmod(path, mode)
        self.addCleanup(restore)
        probe = subprocess.run([sys.executable, "-c", "import sys; open(sys.argv[1], 'rb').read()", str(path)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            restore()
            self.skipTest("无法构造“签字文件读不了”：本机当前账户仍能读取该文件（例如以 root 运行）")
        return restore
