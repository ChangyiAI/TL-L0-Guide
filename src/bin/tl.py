#!/usr/bin/env python3
"""Tianlong segment-1 record layer command line tool."""
from __future__ import annotations

import argparse
import datetime as dt
import errno
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path, PureWindowsPath

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

TASK_RE = re.compile(r"^[a-z]{2,8}-[0-9]{3}$")
PKG_RE = re.compile(r"^([a-z]{2,8}-[0-9]{3})-(g0|g1|g2|fx|mn|pa|as)-([0-9]{2})$")
STEP_RE = re.compile(r"^S[0-7]-[A-Z]+$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
ISO_TZ_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
DOCS = {"g0":"需求确认书","g1":"规格确认书","g2":"发布确认书","fx":"简式确认书","mn":"改动登记单","pa":"过程审批单","as":"资产入库确认"}
LOW = {"纯命名","纯格式","不涉状态","不涉接口数据与权限"}
PROGRESS = ("task_id","channel","baseline_commit","current_step","conditions","token_holder","steps")
ZERO = "0" * 64

class Failure(Exception):
    def __init__(self, code, *errors): self.code, self.errors = code, list(errors)

def root_from(cwd=None):
    p = Path(cwd or Path.cwd()).resolve()
    for q in (p, *p.parents):
        if (q / ".git").exists(): return q
    raise Failure(1, "当前目录不在 git 仓库内")

def read_json(path):
    try: raw = path.read_bytes()
    except OSError as e: raise Failure(2, f"无法读取 {path}：{e}")
    if raw.startswith(b"\xef\xbb\xbf"): raise Failure(2, f"{path} 文件带 BOM，请改为无 BOM 的 UTF-8")
    try: obj = json.loads(raw.decode("utf-8"))
    except Exception: raise Failure(2, f"{path} 不是合法 JSON")
    if not isinstance(obj, dict): raise Failure(2, f"{path} 不是合法 JSON 对象")
    return obj

def read_utf8(path, label=None, reject_bom=False):
    try: raw = path.read_bytes()
    except OSError as e: raise Failure(2, f"无法读取 {path}：{e}")
    if reject_bom and raw.startswith(b"\xef\xbb\xbf"):
        raise Failure(2, f"{path} 文件带 BOM，请改为无 BOM 的 UTF-8")
    try: return raw.decode("utf-8")
    except UnicodeDecodeError: raise Failure(2, f"{label or path} 不是合法 UTF-8")

def now():
    value = os.environ.get("TL_NOW")
    if value is None or value == "": value = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        if not ISO_TZ_RE.fullmatch(value): raise ValueError
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError
    except ValueError: raise Failure(1, "TL_NOW 必须是带时区的 ISO 8601 时间")
    return value

def sign_file(root):
    raw = os.environ.get("TL_SIGN_DIR")
    if not raw: raw = r"C:\TianlongSign"
    base = Path(raw)
    if not base.is_absolute(): raise Failure(1, "签字根目录必须是绝对路径")
    base = base.resolve()
    try: base.relative_to(root.resolve())
    except ValueError: pass
    else: raise Failure(1, "签字根目录不能位于仓库内")
    path = base / root.name / "signatures.jsonl"
    parent = path.parent.resolve()
    try: parent.relative_to(root.resolve())
    except ValueError: pass
    else: raise Failure(1, "签字文件所在目录不能位于仓库内")
    return path

def digest(data): return hashlib.sha256(data).hexdigest()
def manifest_digest(m):
    body = {k:v for k,v in m.items() if k not in ("manifest_sha256","fingerprint_code")}
    return digest(json.dumps(body, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode())

def validate_progress(p, task):
    e=[]
    for k in PROGRESS:
        if k not in p: e.append(f"进度卡缺少字段 {k}")
    if e: return e
    if p["task_id"] != task: e.append("进度卡任务编号与目录不一致")
    if p["channel"] not in ("CH-FEAT","CH-FIX","CH-MINOR"): e.append("通道取值不合法")
    if not isinstance(p["baseline_commit"],str) or not re.fullmatch(r"[0-9a-f]{7,40}",p["baseline_commit"]): e.append("基线提交不合法")
    if not isinstance(p["current_step"],str) or not STEP_RE.fullmatch(p["current_step"]): e.append("当前步骤不合法")
    t=p["token_holder"]
    if not isinstance(t,dict) or not all(t.get(k) for k in ("role","since")): e.append("令牌持有者不合法")
    if not isinstance(p["conditions"],list): e.append("条件步骤必须是数组")
    else:
        for c in p["conditions"]:
            if not isinstance(c,dict) or c.get("readiness") not in ("就绪","适用未就绪"): e.append("条件步骤就绪状态不合法")
            elif c["readiness"] == "适用未就绪" and not c.get("exception_ref"): e.append("适用未就绪时缺少例外引用")
    if not isinstance(p["steps"],list): e.append("步骤必须是数组")
    else:
        for s in p["steps"]:
            if not isinstance(s,dict) or s.get("result") not in ("成功","失败","结果不明"): e.append("步骤结果不合法")
            if not isinstance(s,dict) or not isinstance(s.get("step"),str) or not STEP_RE.fullmatch(s["step"]): e.append("步骤代号不合法")
    return e

def progress(root, task):
    path=root/".tianlong/work"/task/"progress.json"
    if not path.is_file(): raise Failure(1,"任务进度卡不存在")
    p = read_json(path)
    e=validate_progress(p,task)
    if e: raise Failure(2,*e)
    return p

def package_path(root,pid):
    m=PKG_RE.fullmatch(pid or "")
    if not m: raise Failure(1,"包编号格式不合法")
    path=root/".tianlong/work"/m.group(1)/"packages"/(pid+".json")
    if not path.is_file(): raise Failure(1,"待签包不存在")
    return path

def validate_manifest(root,path,m,command_pid=None):
    errors=[]; filename=path.stem
    pm=PKG_RE.fullmatch(filename)
    pid=m.get("package_id")
    if pid != filename or command_pid is not None and pid != command_pid: errors.append("清单、文件名与命令行包编号不一致")
    match=PKG_RE.fullmatch(pid) if isinstance(pid,str) else None
    task=path.parent.parent.name
    if not match or m.get("task_id") != task or (match and match.group(1)!=task): errors.append("清单任务编号不一致")
    files=m.get("files")
    if not isinstance(files,list) or not files: errors.append("清单文件列表不能为空"); files=[]
    if sum(isinstance(x,dict) and x.get("role")=="正文" for x in files)!=1: errors.append("清单必须恰有一个正文")
    for x in files:
        if not isinstance(x,dict) or x.get("role") not in ("正文","附件"): errors.append("清单文件角色不合法"); continue
        rel=x.get("path")
        if not isinstance(rel,str) or not rel or "\\" in rel or Path(rel).is_absolute() or PureWindowsPath(rel).drive or ".." in Path(rel).parts:
            errors.append("清单文件路径不合法"); continue
        target=(root/rel).resolve()
        try: target.relative_to(root.resolve())
        except ValueError: errors.append("清单文件路径越出仓库"); continue
        relative_target = target.relative_to(root.resolve())
        parts = relative_target.parts
        normalized = [os.path.normcase(part.rstrip(". ")) if os.name == "nt" else part for part in parts]
        if "packages" in normalized and ".tianlong" in normalized: errors.append("清单不能指向 packages 目录")
    if errors: raise Failure(2,*errors)

def load_manifest(root,path,pid=None):
    m=read_json(path); validate_manifest(root,path,m,pid); return m

def check_manifest_files(root,m):
    actual=manifest_digest(m)
    if m.get("manifest_sha256") != actual or m.get("fingerprint_code") != actual[:8]: raise Failure(3,"清单指纹不符")
    for f in m["files"]:
        try: data=(root/f["path"]).read_bytes()
        except OSError: raise Failure(3,f"受审文件不存在：{f['path']}")
        if f.get("sha256") != digest(data): raise Failure(3,f"受审文件指纹不符：{f['path']}")
    return actual

def valid_signed_at(value):
    if not isinstance(value, str) or not ISO_TZ_RE.fullmatch(value): return False
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        return parsed.utcoffset() is not None
    except ValueError:
        return False

def validate_signature_record(r):
    if not isinstance(r,dict): return False
    required=("package_id","decision","fingerprint_code","manifest_sha256","reason","signed_at","signer","prev_sha256")
    return (
        all(k in r for k in required)
        and isinstance(r["package_id"],str) and PKG_RE.fullmatch(r["package_id"]) is not None
        and r["decision"] in ("已确认","退回")
        and r["signer"] == "APR:Fred"
        and valid_signed_at(r["signed_at"])
        and isinstance(r["manifest_sha256"],str) and HEX_RE.fullmatch(r["manifest_sha256"]) is not None
        and isinstance(r["prev_sha256"],str) and HEX_RE.fullmatch(r["prev_sha256"]) is not None
        and isinstance(r["fingerprint_code"],str) and r["fingerprint_code"] == r["manifest_sha256"][:8]
        and isinstance(r["reason"],str)
        and (r["decision"] != "退回" or bool(r["reason"].strip()))
    )

def signatures(path):
    if not path.exists(): return [],[]
    try: data=path.read_bytes()
    except PermissionError as e: raise Failure(1,f"签字文件被占用：{e}")
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EPERM): raise Failure(1,f"签字文件被占用：{e}")
        raise Failure(5,f"签字记录无法读取：{e}")
    if data and not data.endswith(b"\n"): raise Failure(5,"签字记录末行不完整")
    raws=data[:-1].split(b"\n") if data else []; recs=[]; prev=ZERO
    for raw in raws:
        try: r=json.loads(raw.decode("utf-8"))
        except Exception: raise Failure(5,"签字记录含非法 JSON")
        if not validate_signature_record(r): raise Failure(5,"签字记录格式损坏")
        if r.get("prev_sha256") != prev: raise Failure(5,"签字记录链式校验失败")
        prev=digest(raw); recs.append(r)
    return recs,raws

def status_signatures(path):
    """Best-effort reader: status reports corruption on packages instead of returning 5."""
    if not path.exists(): return [], set(), False
    try: data=path.read_bytes()
    except PermissionError as e: raise Failure(1,f"签字文件被占用：{e}")
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EPERM): raise Failure(1,f"签字文件被占用：{e}")
        raise Failure(1,f"签字文件无法读取：{e}")
    recs=[]; corrupt=set(); chain_bad=bool(data and not data.endswith(b"\n")); prev=ZERO
    raws=data.rstrip(b"\n").split(b"\n") if data else []
    for raw in raws:
        try: r=json.loads(raw.decode("utf-8"))
        except Exception:
            chain_bad=True; continue
        pid=r.get("package_id") if isinstance(r,dict) else None
        if isinstance(pid,str) and PKG_RE.fullmatch(pid):
            recs.append(r)
            if not validate_signature_record(r): corrupt.add(pid)
        else: chain_bad=True
        if not isinstance(r,dict) or r.get("prev_sha256") != prev: chain_bad=True
        prev=digest(raw)
    if chain_bad: corrupt.update(r.get("package_id") for r in recs if isinstance(r.get("package_id"),str))
    return recs,corrupt,chain_bad

def cmd_pack(a,root):
    if not TASK_RE.fullmatch(a.task): raise Failure(1,"任务编号格式不合法")
    if a.doc not in DOCS or not STEP_RE.fullmatch(a.step): raise Failure(1,"文书代码或步骤代号不合法")
    if not a.summary.strip(): raise Failure(1,"摘要不能为空")
    candidates=[("正文",a.main)]+[("附件",x) for x in a.attach]
    files=[]
    for role,name in candidates:
        p=Path(name); p=(Path.cwd()/p).resolve() if not p.is_absolute() else p.resolve()
        try: rel=p.relative_to(root.resolve())
        except ValueError: raise Failure(1,f"文件不在仓库内：{name}")
        if not p.is_file(): raise Failure(1,f"文件不存在：{name}")
        if ".tianlong" in rel.parts and "packages" in rel.parts: raise Failure(1,"不能打包 packages 目录内的文件")
        files.append({"path":rel.as_posix(),"sha256":digest(p.read_bytes()),"role":role})
    files.sort(key=lambda x:x["path"]); progress(root,a.task); created=now()
    d=root/".tianlong/work"/a.task/"packages"; d.mkdir(parents=True,exist_ok=True)
    for n in range(1,100):
        pid=f"{a.task}-{a.doc}-{n:02d}"; path=d/(pid+".json")
        m={"package_id":pid,"doc_type":DOCS[a.doc],"task_id":a.task,"step":a.step,"summary":a.summary,"created_at":created,"created_by":"tl","files":files}
        m["manifest_sha256"]=manifest_digest(m); m["fingerprint_code"]=m["manifest_sha256"][:8]; m["ok"]=True
        # ok is output metadata, not part of the persisted manifest/fingerprint.
        stored={k:v for k,v in m.items() if k!="ok"}
        try:
            with path.open("x",encoding="utf-8",newline="\n") as f: json.dump(stored,f,ensure_ascii=False,indent=2); f.write("\n")
            return m, f"{pid}｜{DOCS[a.doc]}｜指纹码 {stored['fingerprint_code']}｜{a.summary}"
        except FileExistsError: continue
    raise Failure(1,"包序号已经用完")

def acquire(lock):
    end=time.monotonic()+10
    while True:
        try: fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY); os.close(fd); return
        except OSError as e:
            occupied = isinstance(e, FileExistsError) or (os.name == "nt" and isinstance(e, PermissionError))
            if not occupied: raise
            if time.monotonic() >= end:
                raise Failure(1,f"等待签字锁超时：{lock}；确认没有别的签字进程在运行后，可以删除该锁文件再重试")
            time.sleep(.05)

def cmd_sign(a,root):
    path=package_path(root,a.package_id); m=load_manifest(root,path,a.package_id); full=check_manifest_files(root,m)
    dec={"确认":"已确认","confirm":"已确认","退回":"退回","reject":"退回"}.get(a.decision)
    if not dec: raise Failure(1,"决定只能是确认或退回")
    sf=sign_file(root); base=sf.parent.parent
    if not base.is_dir(): raise Failure(1,"签字根目录不存在")
    sf.parent.mkdir(exist_ok=True); lock=Path(str(sf)+".lock"); acquire(lock)
    try:
        # Duplicate detection is step 2 and deliberately precedes reason and
        # full chain validation.  Parseable occurrences are enough here; the
        # authoritative format/chain validation still happens below.
        if sf.exists():
            try:
                rough=[json.loads(x) for x in sf.read_text(encoding="utf-8").splitlines()]
            except Exception: rough=[]
            if any(isinstance(r,dict) and r.get("package_id")==a.package_id for r in rough): raise Failure(5,"同一个包不能重复签字")
        if dec=="退回" and not (a.reason and a.reason.strip()): raise Failure(1,"退回必须填写理由")
        if dec=="已确认" and a.reason is not None: raise Failure(1,"确认不能带理由")
        recs,raws=signatures(sf)
        rec={"package_id":a.package_id,"decision":dec,"fingerprint_code":m["fingerprint_code"],"manifest_sha256":full,"reason":a.reason.strip() if dec=="退回" else "","signed_at":now(),"signer":"APR:Fred","prev_sha256":digest(raws[-1]) if raws else ZERO}
        line=json.dumps(rec,ensure_ascii=False,separators=(",",":")).encode()+b"\n"
        try:
            with sf.open("ab") as f: f.write(line); f.flush(); os.fsync(f.fileno())
        except OSError as e: raise Failure(1,f"签字文件被占用或无法写入：{e}")
        return {"ok":True,**rec},f"{a.package_id}｜{dec}｜指纹码 {m['fingerprint_code']}"
    finally:
        try: lock.unlink()
        except OSError: pass

def verify_one(root,pid,recs=None):
    path=package_path(root,pid)
    sf=sign_file(root)
    if recs is None: recs,_=signatures(sf) # chain intentionally precedes structural/content checks
    m=load_manifest(root,path,pid)
    full=manifest_digest(m)
    if m.get("manifest_sha256") != full or m.get("fingerprint_code") != full[:8]: raise Failure(3,"清单指纹不符")
    hits=[r for r in recs if r["package_id"]==pid]
    if len(hits)>1: raise Failure(5,"同一个包有重复签字")
    if not hits: raise Failure(4,"待签包尚未签字")
    if hits[0]["manifest_sha256"] != full: raise Failure(3,"签字的完整清单指纹不符")
    if hits[0]["decision"] != "已确认": raise Failure(4,"待签包已退回")
    check_manifest_files(root,m)
    return {"package_id":pid,"ok":True,"code":0,"status":"已确认"}

def cmd_verify(a,root):
    if a.package_id: return verify_one(root,a.package_id)
    task=a.task
    if not TASK_RE.fullmatch(task) or not (root/".tianlong/work"/task).is_dir(): raise Failure(1,"任务编号不合法或任务不存在")
    recs,_=signatures(sign_file(root))
    paths=sorted((root/".tianlong/work"/task/"packages").glob("*.json")) if (root/".tianlong/work"/task/"packages").is_dir() else []
    known={p.stem for p in paths}; missing=[r for r in recs if r["package_id"].startswith(task+"-") and r["package_id"] not in known]
    results=[]; codes=[]
    for r in missing:
        results.append({"package_id":r["package_id"],"ok":False,"code":3,"errors":["已签包的清单不存在"]}); codes.append(3)
    if not paths and not missing: raise Failure(4,"任务没有任何待签包")
    for p in paths:
        try:
            m=load_manifest(root,p)
            results.append(verify_one(root,m["package_id"],recs)); codes.append(0)
        except Failure as e: results.append({"package_id":p.stem,"ok":False,"code":e.code,"errors":e.errors}); codes.append(e.code)
    code=next((x for x in (5,3,2,4) if x in codes),0)
    if code: raise Failure(code,*[f"{r['package_id']}：{'；'.join(r.get('errors',[]))}" for r in results if not r["ok"]])
    return {"ok":True,"packages":results}

def check_task(root,task):
    if not TASK_RE.fullmatch(task) or not (root/".tianlong/work"/task).is_dir(): raise Failure(1,"任务编号不合法或任务不存在")
    errors=[]
    try: progress(root,task)
    except Failure as e: errors+=e.errors
    ap=root/".tianlong/work"/task/"assumptions.jsonl"
    if ap.exists():
        for i,line in enumerate(read_utf8(ap,"假设日志",reject_bom=True).splitlines(),1):
            try: x=json.loads(line)
            except Exception: errors.append(f"假设日志第 {i} 行不是合法 JSON"); continue
            req=("id","step","decision","reason","cost","recorded_by","recorded_at")
            if not isinstance(x,dict) or any(k not in x or x[k]=="" for k in req): errors.append(f"假设日志第 {i} 行缺必填字段"); continue
            if x["cost"] not in ("低","中","高"): errors.append(f"假设日志第 {i} 行成本不合法")
            if x["cost"]=="低" and (not isinstance(x.get("low_reason"),str) or x["low_reason"] not in LOW): errors.append(f"假设日志第 {i} 行低成本理由不合法")
    forms=root/".tianlong/work"/task/"forms"
    if forms.is_dir():
        for f in forms.glob("07_交接卡*.md"):
            text=read_utf8(f,"交接卡"); found={}
            for line in text.splitlines():
                if line.strip().startswith("|"):
                    cells=line.strip().strip("|").split("|")
                    if len(cells)>=2:
                        for key in ("本步","产出","检查结果","未决事项","下一步","令牌移交"):
                            if cells[0].strip().startswith(key): found[key]=cells[1].strip().strip("　")
            for key in ("本步","产出","检查结果","未决事项","下一步","令牌移交"):
                if not found.get(key): errors.append(f"{f} 的{key}栏目为空")
    pkg=root/".tianlong/work"/task/"packages"
    if pkg.is_dir():
        for path in pkg.glob("*.json"):
            try: validate_manifest(root,path,read_json(path))
            except Failure as e: errors+=e.errors
    if errors: raise Failure(2,*errors)
    return {"ok":True,"task_id":task,"errors":[]}

def status_state(root,path,recs,corrupt):
    if path.stem in corrupt: return "签字记录损坏",None,0,["签字记录损坏"]
    try:
        m=load_manifest(root,path,path.stem); hits=[r for r in recs if r["package_id"]==path.stem]
        check_manifest_files(root,m)
    except Failure as e:
        if e.code==2:return "清单不合格",None,2,e.errors
        return ("签后文件已变" if 'hits' in locals() and hits else "需重新打包"),locals().get("m"),0,e.errors
    if len(hits)>1:return "签字记录损坏",m,0,["重复签字"]
    if not hits:return "未签",m,0,[]
    if hits[0]["manifest_sha256"] != m["manifest_sha256"]: return "签后文件已变",m,0,[]
    return ("已确认" if hits[0]["decision"]=="已确认" else "退回"),m,0,[]

def cmd_status(a,root):
    work=root/".tianlong/work"; tasks=[a.task] if a.task else sorted(p.name for p in work.iterdir() if p.is_dir()) if work.is_dir() else []
    if a.task and (not TASK_RE.fullmatch(a.task) or not (work/a.task).is_dir()): raise Failure(1,"任务编号不合法或任务不存在")
    recs,corrupt,_=status_signatures(sign_file(root)); output=[]; codes=[]
    for task in tasks:
        try: p=progress(root,task)
        except Failure as e: output.append({"task_id":task,"errors":e.errors}); codes.append(2); continue
        packs=[]
        for path in sorted((work/task/"packages").glob("*.json")) if (work/task/"packages").is_dir() else []:
            state,m,c,errors=status_state(root,path,recs,corrupt); codes.append(c); packs.append({"package_id":path.stem,"status":state,"manifest":m,"errors":errors})
        output.append({"task_id":task,"progress":p,"packages":packs})
    code=next((x for x in (5,3,2,4) if x in codes),0)
    return {"ok":code==0,"tasks":output,"code":code},output,code

class CliParser(argparse.ArgumentParser):
    def error(self,message):
        raise Failure(1,f"参数错误：{message}")

def parser():
    p=CliParser(prog="tl"); sp=p.add_subparsers(dest="cmd",required=True,parser_class=CliParser)
    q=sp.add_parser("pack"); q.add_argument("--task",required=True); q.add_argument("--doc",required=True); q.add_argument("--step",required=True); q.add_argument("--main",required=True); q.add_argument("--attach",action="append",default=[]); q.add_argument("--summary",required=True); q.add_argument("--json",action="store_true")
    q=sp.add_parser("sign"); q.add_argument("package_id"); q.add_argument("decision"); q.add_argument("--reason"); q.add_argument("--json",action="store_true")
    q=sp.add_parser("verify"); g=q.add_mutually_exclusive_group(required=True); g.add_argument("package_id",nargs="?"); g.add_argument("--task"); q.add_argument("--json",action="store_true")
    q=sp.add_parser("check"); q.add_argument("--task",required=True); q.add_argument("--json",action="store_true")
    q=sp.add_parser("status"); q.add_argument("--task"); q.add_argument("--json",action="store_true")
    return p

def human_status(items, summary=False):
    lines=[]
    for x in items:
        if "progress" not in x: lines.append(f"{x['task_id']}｜进度卡不合法"); continue
        p=x["progress"]; holder=p["token_holder"]
        heading=f"任务编号：{x['task_id']}｜通道：{p['channel']}｜基线提交：{p['baseline_commit']}｜当前步骤：{p['current_step']}｜令牌持有者：{holder.get('role')}｜自 {holder.get('since')}"
        if summary:
            pending=sum(z["status"]=="未签" for z in x["packages"])
            lines.append(f"{heading}｜待签包数：{pending}")
            continue
        lines.append(heading)
        for c in p["conditions"]: lines.append(f"条件步骤：{c.get('code','')}｜{c.get('readiness','')}")
        for s in p["steps"]: lines.append(f"步骤：{s.get('step','')}｜结果：{s.get('result','')}")
        for z in x["packages"]:
            m=z["manifest"] or {}; lines.append(f"{z['package_id']}｜{m.get('doc_type','')}｜指纹码 {m.get('fingerprint_code','')}｜{z['status']}")
            for f in m.get("files",[]): lines.append(f"  {f.get('role')}：{f.get('path')}")
    return "\n".join(lines)

def main():
    a=None
    try:
        a=parser().parse_args(); root=root_from()
        if a.cmd=="pack": obj,text=cmd_pack(a,root)
        elif a.cmd=="sign": obj,text=cmd_sign(a,root)
        elif a.cmd=="verify": obj=cmd_verify(a,root); text=json.dumps(obj,ensure_ascii=False,indent=2)
        elif a.cmd=="check": obj=check_task(root,a.task); text="核对通过"
        else:
            obj,items,code=cmd_status(a,root); text=human_status(items,summary=a.task is None)
            if code:
                if not a.json: print(text)
                details=[f"{z['package_id']}：{e}" for x in items for z in x.get("packages",[]) for e in z.get("errors",[])]
                details += [e for x in items for e in x.get("errors",[])]
                raise Failure(code,*details or ["状态核对未通过"])
        print(json.dumps(obj,ensure_ascii=False) if a.json else text); return 0
    except Failure as e:
        for msg in e.errors: print(msg,file=sys.stderr)
        json_requested=(a is not None and getattr(a,"json",False)) or "--json" in sys.argv[1:]
        if json_requested: print(json.dumps({"ok":False,"code":e.code,"errors":e.errors},ensure_ascii=False))
        return e.code
    except Exception as e:
        print(f"用法或文件处理错误：{e}",file=sys.stderr)
        if a is not None and getattr(a,"json",False): print(json.dumps({"ok":False,"code":1,"errors":[f"用法或文件处理错误：{e}"]},ensure_ascii=False))
        return 1

if __name__=="__main__": raise SystemExit(main())
