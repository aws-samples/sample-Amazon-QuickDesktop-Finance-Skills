# -*- coding: utf-8 -*-
"""output_guard.py —— 结果文件受控写出与本地审计留痕（纯标准库）。

本文件在每个带 scripts/ 的财务 Skill 下各放一份相同副本，使每个 Skill 可独立复制使用；
改动时必须同步全部 20 份（shasum skills/*/scripts/output_guard.py 应全部相同）。

约束（违规抛 OutputPathError，命令行入口捕获后 exit 2，不写任何文件）：
  - 输出基目录 = 显式 --out-dir（函数参数 out_dir）；未给出时为 当前目录/finance_outputs（新建为 0700，
    已存在则收紧为 0700，是符号链接则拒绝）。
  - 写任何结果之前先校验同目录 audit_log.jsonl：符号链接、非普通文件、多重硬链接一律拒绝。
  - 结果文件只能是直接位于基目录内的文件名：不允许子目录、..、基目录外的绝对路径。
  - 后缀必须在白名单内；目标已是符号链接（或非普通文件、多重硬链接）时拒绝。
  - 文件以 O_NOFOLLOW 打开并设为 0600。
  - stdout 只打印一行状态与路径，不打印金额、姓名、对手方等明细。
  - 每次写出向基目录下 audit_log.jsonl 追加一行哈希链记录（prev_sha256 = 上一行字节的 SHA-256），
    只含时间、操作者、引擎及其哈希、非路径参数、输入/输出文件名与完整 SHA-256、结论计数。
残余窗口：结果文件先写出、审计行后追加；若两步之间审计日志被换成符号链接，或追加时 I/O 失败（如磁盘写满），
  追加抛错（命令行入口 exit 2），但结果文件已写出且没有对应审计行。
"""
import os
import sys
import stat
import json
import hashlib
import getpass
from datetime import datetime

DEFAULT_OUT_SUBDIR = "finance_outputs"
AUDIT_LOG = "audit_log.jsonl"

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class OutputPathError(ValueError):
    """输出路径或命令行选项不符合约束。"""


def parse_cli(argv, value_opts=(), multi_opts=()):
    """剥离 --选项，返回 (positional, opts)。positional 保留 argv[0]。
    value_opts：取一个值的选项（--out-dir 总是支持）；multi_opts：可重复、取值为列表的选项。
    支持 --opt 值 与 --opt=值 两种写法；缺值或未知选项抛 OutputPathError。"""
    single = set(value_opts) | {"--out-dir"}
    multi = set(multi_opts)
    positional, opts = [argv[0]] if argv else [], {}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if name not in single and name not in multi:
                raise OutputPathError(f"未知选项 {name}")
            if not eq:
                if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                    raise OutputPathError(f"选项 {name} 缺少取值")
                i += 1
                val = argv[i]
            if val == "":
                raise OutputPathError(f"选项 {name} 缺少取值")
            if name in multi:
                opts.setdefault(name, []).append(val)
            else:
                opts[name] = val
        else:
            positional.append(a)
        i += 1
    return positional, opts


def resolve_base(out_dir=None):
    """返回输出基目录的 realpath；不存在则以 0700 新建。
    默认目录（当前目录/finance_outputs）是符号链接时拒绝；默认目录已存在时收紧为 0700。"""
    base = out_dir if out_dir else os.path.join(os.getcwd(), DEFAULT_OUT_SUBDIR)
    if "\x00" in str(base):
        raise OutputPathError("输出目录含非法字符")
    base = os.path.abspath(base)
    if not out_dir and os.path.islink(base):
        raise OutputPathError(f"默认输出目录是符号链接，拒绝写入：{base}")
    if os.path.lexists(base) and not os.path.isdir(base):
        raise OutputPathError(f"输出目录不是目录：{base}")
    os.makedirs(base, mode=0o700, exist_ok=True)
    if not out_dir:
        os.chmod(base, 0o700)
    return os.path.realpath(base)


def check_audit_log(base):
    """写任何结果之前校验 base/audit_log.jsonl 可安全追加：符号链接、非普通文件、多重硬链接一律拒绝。"""
    log = os.path.join(base, AUDIT_LOG)
    if os.path.islink(log):
        raise OutputPathError(f"审计日志是符号链接，拒绝写入：{log}")
    if os.path.lexists(log):
        st = os.lstat(log)
        if not stat.S_ISREG(st.st_mode):
            raise OutputPathError(f"审计日志不是普通文件：{log}")
        if st.st_nlink > 1:
            raise OutputPathError(f"审计日志存在多重硬链接，拒绝写入：{log}")
    return log


def resolve_output(dest=None, out_dir=None, default_name="analysis.json", suffixes=(".json",)):
    """校验结果文件名与同目录审计日志，返回基目录内的目标路径。"""
    base = resolve_base(out_dir)
    check_audit_log(base)
    name = dest or default_name
    if "\x00" in name:
        raise OutputPathError("输出文件名含非法字符")
    cand = name if os.path.isabs(name) else os.path.join(base, name)
    if os.path.realpath(os.path.dirname(cand)) != base:
        raise OutputPathError(f"输出文件必须直接位于输出目录 {base} 内：{name}")
    fname = os.path.basename(cand)
    if not fname or fname in (".", ".."):
        raise OutputPathError(f"输出文件名为空：{name}")
    if not fname.lower().endswith(tuple(s.lower() for s in suffixes)):
        raise OutputPathError(f"输出文件后缀须为 {'/'.join(suffixes)}：{fname}")
    path = os.path.join(base, fname)
    if os.path.islink(path):
        raise OutputPathError(f"输出文件是符号链接，拒绝写入：{path}")
    if os.path.lexists(path) and not os.path.isfile(path):
        raise OutputPathError(f"输出目标不是普通文件：{path}")
    return path


def _open_private(path, append=False):
    flags = os.O_WRONLY | os.O_CREAT | _O_NOFOLLOW | _O_CLOEXEC | (os.O_APPEND if append else 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as e:
        raise OutputPathError(f"无法安全打开输出文件 {path}：{e.strerror}")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OutputPathError(f"输出目标不是普通文件：{path}")
        if st.st_nlink > 1:
            raise OutputPathError(f"输出文件存在多重硬链接，拒绝写入：{path}")
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        if not append:
            os.ftruncate(fd, 0)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _write_all(fd, data):
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    finally:
        os.close(fd)


def write_bytes(path, data):
    _write_all(_open_private(path), bytes(data))
    return path


def write_text(path, text):
    return write_bytes(path, text.encode("utf-8"))


def write_json(path, obj):
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def sha256_file(path):
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def file_fingerprint(path, role):
    return {"file": role, "sha256": sha256_file(path)}


def _last_line_sha256(log):
    if not os.path.exists(log):
        return None
    try:
        fd = os.open(log, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC)
    except OSError as e:
        raise OutputPathError(f"无法安全读取审计日志 {log}：{e.strerror}")
    with os.fdopen(fd, "rb") as f:
        lines = [ln for ln in f.read().split(b"\n") if ln.strip()]
    return hashlib.sha256(lines[-1]).hexdigest() if lines else None


def append_audit(base, engine_file, params, inputs, outputs, verdict, result_sha256):
    """向 base/audit_log.jsonl 追加一行哈希链记录，返回日志路径。"""
    log = check_audit_log(base)
    try:
        operator = getpass.getuser()
    except Exception:
        operator = "unknown"
    rec = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "operator": operator,
        "engine": os.path.basename(engine_file),
        "engine_sha256": sha256_file(engine_file),
        "params": params or {},
        "inputs": [{"file": i.get("file"), "sha256": i.get("sha256")} for i in (inputs or []) if isinstance(i, dict)],
        "outputs": [{"file": os.path.basename(p), "sha256": sha256_file(p)} for p in (outputs or [])],
        "result_sha256": result_sha256,
        "verdict": verdict,
        "prev_sha256": _last_line_sha256(log),
    }
    line = json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str) + "\n"
    _write_all(_open_private(log, append=True), line.encode("utf-8"))
    return log


def status_line(result, out_path, log_path, ok_key="all_ok"):
    head = f"完成 | 全平:{result.get('all_tie')}" if ok_key == "all_tie" else f"完成 | 全部通过:{result.get(ok_key)}"
    total = result.get("total")
    if total is None and isinstance(result.get("results"), list):
        total = len(result["results"])
    rec = f" | 记录 {total} 条" if total is not None else ""   # 结果无记录总数时省略该段
    return (f"{head}{rec} | 数据质量问题 {len(result.get('data_issues') or [])} 处"
            f" | 问题标签 {len(result.get('issue_types') or {})} 类 | 结果文件 {out_path} | 审计日志 {log_path}")


def finish_cli(engine_file, result, dest, out_dir, default_name, params, inputs, ok_key="all_ok"):
    """命令行收尾：校验路径 → 写结果 JSON（0600）→ 追加审计日志 → 打印一行状态。"""
    path = resolve_output(dest, out_dir, default_name)
    write_json(path, result)
    verdict = {ok_key: result.get(ok_key), "data_issues": len(result.get("data_issues") or [])}
    log = append_audit(os.path.dirname(path), engine_file, params, inputs, [path], verdict, sha256_json(result))
    print(status_line(result, path, log, ok_key))
    return path


def reject(err):
    print("输出被拒绝：" + str(err), file=sys.stderr)
    sys.exit(2)
