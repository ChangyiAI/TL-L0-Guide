#!/usr/bin/env python3
"""天龙段 1 记录层命令行工具（仅使用 Python 标准库）。"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

TASK_RE = re.compile(r"^[a-z]{2,8}-[0-9]{3}$")
PACKAGE_RE = re.compile(r"^([a-z]{2,8}-[0-9]{3})-(g0|g1|g2|fx|mn|pa|as)-([0-9]{2})$")
STEP_RE = re.compile(r"^S[0-7]-[A-Z]+$")
BASELINE_RE = re.compile(r"^[0-9a-f]{7,40}$")
DOCS = {"g0": "需求确认书", "g1": "规格确认书", "g2": "发布确认书",
        "fx": "简式确认书", "mn": "改动登记单", "pa": "过程审批单", "as": "资产入库确认"}
PROGRESS_REQUIRED = ("task_id", "channel", "baseline_commit", "current_step",
                     "conditions", "token_holder", "steps")
ASSUMPTION_REQUIRED = ("id", "step", "decision", "reason", "cost", "recorded_by", "recorded_at")
LOW_REASONS = {"纯命名", "纯格式", "不涉状态", "不涉接口数据与权限"}
HANDOVER_COLUMNS = ("本步", "产出", "检查结果", "未决事项", "下一步", "令牌移交")
ZERO64 = "0" * 64


def sha(data):
    return hashlib.sha256(data).hexdigest()


def emit(value, as_json=False):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    elif isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def repo_root():
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                encoding="utf-8")
        return Path(result.stdout.strip()).resolve()
    except (OSError, subprocess.CalledProcessError):
        return None


def load_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def progress_errors(path, expected_task):
    data = load_json(path)
    if data is None:
        return [f"{path}: 不是合法 JSON 对象"]
    errors = []
    for key in PROGRESS_REQUIRED:
        if key not in data:
            errors.append(f"{path}: 缺少字段 {key}")
    if errors:
        return errors
    if not TASK_RE.fullmatch(str(data["task_id"])) or data["task_id"] != expected_task:
        errors.append(f"{path}: task_id 不合法或与目录不一致")
    if data["channel"] not in ("CH-FEAT", "CH-FIX", "CH-MINOR"):
        errors.append(f"{path}: channel 不合法")
    if not BASELINE_RE.fullmatch(str(data["baseline_commit"])):
        errors.append(f"{path}: baseline_commit 不合法")
    if not STEP_RE.fullmatch(str(data["current_step"])):
        errors.append(f"{path}: current_step 不合法")
    holder = data["token_holder"]
    if not isinstance(holder, dict) or "role" not in holder or "since" not in holder:
        errors.append(f"{path}: token_holder 缺 role 或 since")
    conditions = data["conditions"]
    if not isinstance(conditions, list):
        errors.append(f"{path}: conditions 不是数组")
    else:
        for i, item in enumerate(conditions, 1):
            if not isinstance(item, dict) or item.get("readiness") not in ("就绪", "适用未就绪"):
                errors.append(f"{path}: conditions 第 {i} 项 readiness 不合法")
            elif item["readiness"] == "适用未就绪" and not str(item.get("exception_ref", "")).strip():
                errors.append(f"{path}: conditions 第 {i} 项缺 exception_ref")
    steps = data["steps"]
    if not isinstance(steps, list):
        errors.append(f"{path}: steps 不是数组")
    else:
        for i, item in enumerate(steps, 1):
            if not isinstance(item, dict) or item.get("result") not in ("成功", "失败", "结果不明"):
                errors.append(f"{path}: steps 第 {i} 项 result 不合法")
            # M1：采用保守口径，历史步骤代号也按同一格式检查。
            elif "step" in item and not STEP_RE.fullmatch(str(item["step"])):
                errors.append(f"{path}: steps 第 {i} 项 step 不合法")
    return errors


def manifest_digest(manifest):
    body = {k: v for k, v in manifest.items() if k not in ("manifest_sha256", "fingerprint_code")}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha(raw)


def locate_manifest(root, package_id):
    match = PACKAGE_RE.fullmatch(package_id or "")
    if not match:
        return None
    path = root / ".tianlong" / "work" / match.group(1) / "packages" / f"{package_id}.json"
    return path if path.is_file() else None


def manifest_integrity(root, manifest, check_files=True):
    try:
        digest = manifest_digest(manifest)
        if manifest.get("manifest_sha256") != digest or manifest.get("fingerprint_code") != digest[:8]:
            return False
        if check_files:
            for entry in manifest["files"]:
                path = (root / entry["path"]).resolve()
                if not path.is_file() or sha(path.read_bytes()) != entry["sha256"]:
                    return False
        return True
    except (KeyError, TypeError, OSError):
        return False


def signature_path(root):
    base = Path(os.environ.get("TL_SIGN_DIR", r"C:\TianlongSign"))
    return base, base / root.name / "signatures.jsonl"


def read_signatures(path):
    if not path.exists():
        return [], [], True
    try:
        data = path.read_bytes()
    except OSError:
        return [], [], False
    if data and not data.endswith(b"\n"):
        return [], [], False
    lines = data[:-1].split(b"\n") if data else []
    records = []
    previous = ZERO64
    for line in lines:
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return lines, records, False
        if not isinstance(record, dict) or record.get("prev_sha256") != previous:
            return lines, records, False
        records.append(record)
        previous = sha(line)
    return lines, records, True


def command_pack(root, args):
    # M2：先校验所有用法参数，再读取进度卡。
    if not TASK_RE.fullmatch(args.task or "") or args.doc not in DOCS or not STEP_RE.fullmatch(args.step or ""):
        return 1
    progress = root / ".tianlong" / "work" / args.task / "progress.json"
    if not progress.is_file():
        return 1
    errors = progress_errors(progress, args.task)
    if errors:
        emit({"ok": False, "errors": errors}, args.json)
        return 2
    file_args = [(args.main, "正文")] + [(item, "附件") for item in args.attach]
    entries = []
    for name, role in file_args:
        try:
            candidate = Path(name)
            path = (candidate if candidate.is_absolute() else root / candidate).resolve()
            rel = path.relative_to(root).as_posix()
        except (OSError, ValueError):
            return 1
        if not path.is_file() or re.search(r"(?:^|/)\.tianlong/work/[^/]+/packages(?:/|$)", rel):
            return 1
        entries.append({"path": rel, "sha256": sha(path.read_bytes()), "role": role})
    package_dir = root / ".tianlong" / "work" / args.task / "packages"
    existing = []
    if package_dir.is_dir():
        prefix = f"{args.task}-{args.doc}-"
        for path in package_dir.glob(prefix + "[0-9][0-9].json"):
            try:
                existing.append(int(path.stem[-2:]))
            except ValueError:
                pass
    number = max(existing, default=0) + 1
    if number > 99:
        return 1
    package_id = f"{args.task}-{args.doc}-{number:02d}"
    manifest = {"package_id": package_id, "doc_type": DOCS[args.doc], "task_id": args.task,
                "step": args.step, "summary": args.summary,
                "created_at": os.environ.get("TL_NOW") or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "created_by": "tl", "files": sorted(entries, key=lambda item: item["path"])}
    manifest["manifest_sha256"] = manifest_digest(manifest)
    manifest["fingerprint_code"] = manifest["manifest_sha256"][:8]
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / f"{package_id}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                     encoding="utf-8", newline="\n")
    if args.json:
        emit(manifest, True)
    else:
        emit(f"{package_id}｜{DOCS[args.doc]}｜指纹码 {manifest['fingerprint_code']}｜{args.summary}")
    return 0


def command_sign(root, args):
    path = locate_manifest(root, args.package_id)
    if path is None or args.decision not in ("确认", "退回", "confirm", "reject"):
        return 1
    manifest = load_json(path)
    if manifest is None or not manifest_integrity(root, manifest, True):
        return 3
    base, sig_path = signature_path(root)
    lines, records, chain_ok = read_signatures(sig_path)
    if any(record.get("package_id") == args.package_id for record in records):
        return 5
    decision = "已确认" if args.decision in ("确认", "confirm") else "退回"
    if decision == "退回" and not (args.reason or "").strip():
        return 1
    if not base.is_dir():
        return 1
    if not chain_ok:
        return 5
    record = {"package_id": args.package_id, "decision": decision,
              "fingerprint_code": manifest["fingerprint_code"], "reason": args.reason or "",
              "signed_at": os.environ.get("TL_NOW") or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
              "signer": "APR:Fred", "prev_sha256": sha(lines[-1]) if lines else ZERO64}
    sig_path.parent.mkdir(parents=True, exist_ok=True)
    with sig_path.open("ab") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    if args.json:
        emit(record, True)
    else:
        emit(f"{record['package_id']}｜{decision}｜指纹码 {record['fingerprint_code']}")
    return 0


def verify_one(root, package_id, records, chain_ok):
    path = locate_manifest(root, package_id)
    if path is None:
        return 1, "包不存在或编号不合法"
    if not chain_ok:
        return 5, "签字记录损坏"
    manifest = load_json(path)
    if manifest is None or not manifest_integrity(root, manifest, False):
        return 3, "清单指纹不符"
    matches = [r for r in records if r.get("package_id") == package_id]
    if len(matches) > 1:
        return 5, "重复签字"
    if not matches:
        return 4, "未签"
    record = matches[0]
    if record.get("decision") != "已确认":
        return 4, "退回"
    if record.get("fingerprint_code") != manifest.get("fingerprint_code"):
        return 4, "签字指纹不符"
    if not manifest_integrity(root, manifest, True):
        return 3, "签后文件已变"
    return 0, "已确认"


def command_verify(root, args):
    _, sig_path = signature_path(root)
    _, records, chain_ok = read_signatures(sig_path)
    if args.task is not None:
        # M3：非法或不存在的任务均是用法错误。
        task_dir = root / ".tianlong" / "work" / args.task
        if not TASK_RE.fullmatch(args.task) or not task_dir.is_dir():
            return 1
        paths = sorted((task_dir / "packages").glob("*.json")) if (task_dir / "packages").is_dir() else []
        results = []
        for path in paths:
            code, message = verify_one(root, path.stem, records, chain_ok)
            results.append({"package_id": path.stem, "code": code, "result": message})
        codes = [item["code"] for item in results]
        code = 5 if 5 in codes else 3 if 3 in codes else 4 if 4 in codes else 1 if 1 in codes else 0
        emit({"task_id": args.task, "ok": code == 0, "packages": results}, args.json)
        return code
    code, message = verify_one(root, args.package_id, records, chain_ok)
    emit({"package_id": args.package_id, "ok": code == 0, "code": code, "result": message}, args.json)
    return code


def command_check(root, args):
    errors = []
    task_dir = root / ".tianlong" / "work" / args.task
    if not TASK_RE.fullmatch(args.task or "") or not task_dir.is_dir():
        return 1
    errors.extend(progress_errors(task_dir / "progress.json", args.task))
    assumptions = task_dir / "assumptions.jsonl"
    if assumptions.exists():
        try:
            lines = assumptions.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            lines = [""]
        for number, line in enumerate(lines, 1):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                errors.append(f"{assumptions}:{number}: 不是合法 JSON")
                continue
            if not isinstance(item, dict) or any(key not in item for key in ASSUMPTION_REQUIRED):
                errors.append(f"{assumptions}:{number}: 缺必填字段")
                continue
            if item["cost"] not in ("低", "中", "高"):
                errors.append(f"{assumptions}:{number}: cost 不合法")
            elif item["cost"] == "低" and item.get("low_reason") not in LOW_REASONS:
                errors.append(f"{assumptions}:{number}: low_reason 不合法")
    forms = task_dir / "forms"
    if forms.is_dir():
        for card in sorted(forms.glob("07_交接卡*.md")):
            found = {}
            try:
                lines = card.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                lines = []
            for line in lines:
                stripped = line.strip()
                if not stripped.startswith("|"):
                    continue
                cells = stripped.strip("|").split("|")
                if len(cells) < 2:
                    continue
                label, value = cells[0].strip(), cells[1].strip().strip("　").strip()
                for column in HANDOVER_COLUMNS:
                    if label.startswith(column):
                        found[column] = value
            for column in HANDOVER_COLUMNS:
                if not found.get(column):
                    errors.append(f"{card}: 栏目 {column} 缺失或为空")
    packages = task_dir / "packages"
    if packages.is_dir():
        for path in packages.glob("*.json"):
            manifest = load_json(path)
            package_id = manifest.get("package_id") if manifest else None
            match = PACKAGE_RE.fullmatch(str(package_id or ""))
            if not match or match.group(1) != args.task or path.stem != package_id:
                errors.append(f"{path}: 包编号不合法、任务不一致或文件名不一致")
    emit({"task_id": args.task, "ok": not errors, "errors": errors}, args.json)
    return 2 if errors else 0


def status_state(root, package_id, records, chain_ok):
    code, message = verify_one(root, package_id, records, chain_ok)
    # M5：清单被改也采用醒目的“签后文件已变”。
    if code == 3:
        return "签后文件已变"
    return message


def command_status(root, args):
    work = root / ".tianlong" / "work"
    task_ids = [args.task] if args.task else sorted(p.name for p in work.iterdir() if p.is_dir()) if work.is_dir() else []
    all_errors, output = [], []
    _, sig_path = signature_path(root)
    _, records, chain_ok = read_signatures(sig_path)
    for task_id in task_ids:
        path = work / task_id / "progress.json"
        errors = progress_errors(path, task_id)
        if errors:
            all_errors.extend(errors)
            continue
        progress = load_json(path)
        packages = []
        package_dir = work / task_id / "packages"
        for package_path in sorted(package_dir.glob("*.json")) if package_dir.is_dir() else []:
            manifest = load_json(package_path) or {}
            packages.append({"package_id": package_path.stem, "doc_type": manifest.get("doc_type", ""),
                             "fingerprint_code": manifest.get("fingerprint_code", ""),
                             "state": status_state(root, package_path.stem, records, chain_ok),
                             "files": [f.get("path", "") for f in manifest.get("files", []) if isinstance(f, dict)]})
        output.append({"task_id": task_id, "current_step": progress["current_step"], "channel": progress["channel"],
                       "token_holder": progress["token_holder"], "packages": packages})
    if args.json:
        emit({"tasks": output, "errors": all_errors}, True)
    else:
        for task in output:
            holder = task["token_holder"].get("role", "")
            if not args.task:
                pending = sum(1 for p in task["packages"] if p["state"] == "未签")
                emit(f"{task['task_id']}｜{task['current_step']}｜{task['channel']}｜令牌 {holder}｜待签 {pending}")
            else:
                emit(f"任务 {task['task_id']}｜步骤 {task['current_step']}｜通道 {task['channel']}｜令牌 {holder}")
                for package in task["packages"]:
                    emit(f"{package['package_id']}｜{package['doc_type']}｜指纹码 {package['fingerprint_code']}｜{package['state']}")
                    for name in package["files"]:
                        emit(f"  - {name}")
        for error in all_errors:
            emit(error)
    return 2 if all_errors else 0


def parser():
    p = argparse.ArgumentParser(prog="tl")
    sub = p.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--task")
    status.add_argument("--json", action="store_true")
    pack = sub.add_parser("pack")
    pack.add_argument("--task", required=True)
    pack.add_argument("--doc", required=True)
    pack.add_argument("--step", required=True)
    pack.add_argument("--main", required=True)
    pack.add_argument("--attach", action="append", default=[])
    pack.add_argument("--summary", required=True)
    pack.add_argument("--json", action="store_true")
    sign = sub.add_parser("sign")
    sign.add_argument("package_id")
    sign.add_argument("decision")
    sign.add_argument("--reason")
    sign.add_argument("--json", action="store_true")
    verify = sub.add_parser("verify")
    group = verify.add_mutually_exclusive_group(required=True)
    group.add_argument("package_id", nargs="?")
    group.add_argument("--task")
    verify.add_argument("--json", action="store_true")
    check = sub.add_parser("check")
    check.add_argument("--task", required=True)
    check.add_argument("--json", action="store_true")
    return p


def main():
    try:
        args = parser().parse_args()
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    root = repo_root()
    if root is None:
        return 1
    commands = {"status": command_status, "pack": command_pack, "sign": command_sign,
                "verify": command_verify, "check": command_check}
    return commands[args.command](root, args)


if __name__ == "__main__":
    sys.exit(main())
