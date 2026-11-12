#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行前安全自检：路径白名单、网络模块、输出体积上限。

不依赖外部沙箱：外部沙箱（sandbox-exec / firejail / Job Objects）是增强层，
即使没有，本 Skill 也靠“只读 + 无网络 + 白名单 + 体积上限”成立。
"""

import os
import sys
from pathlib import Path

DENY_MODULES = ("requests", "httpx", "aiohttp", "urllib3", "http.client")
DENY_PATH_PARTS = (".ssh", ".gnupg", ".aws", ".config/gcloud", "Keychains")
SENSITIVE_NAMES = (".env", "id_rsa", "id_ed25519", "credentials", "cookies")
MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB


class SandboxError(RuntimeError):
    pass


def safe_roots(extra=None):
    """允许读取的根目录：本机 Agent 数据目录 + 当前工作目录。"""
    home = Path.home()
    roots = [
        home / ".workbuddy",
        home / ".codebuddy",
        home / ".openclaw",
        home / ".claude",
        Path.cwd(),
    ]
    for p in extra or []:
        if p:
            roots.append(Path(p).expanduser())
    out = []
    for r in roots:
        try:
            out.append(r.resolve())
        except OSError:
            continue
    return out


def assert_safe_path(path, roots=None):
    """resolve 后必须落在白名单根内，且不是敏感目录；否则抛 SandboxError。"""
    roots = roots or safe_roots()
    p = Path(path).expanduser()
    try:
        rp = p.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise SandboxError("路径无法解析：%s（%s）" % (p, e))

    s = rp.as_posix()
    for bad in DENY_PATH_PARTS:
        if bad in s:
            raise SandboxError("拒绝访问敏感目录：%s" % rp)

    for root in roots:
        if rp == root or root in rp.parents:
            return rp
    raise SandboxError("路径不在白名单内：%s" % rp)


def should_skip(name):
    """枚举阶段直接跳过的文件名/后缀。"""
    low = name.lower()
    if low.endswith((".pem", ".key", ".p12", ".pfx")):
        return "certificate_or_key"
    if any(t in low for t in ("credential", "cookie", ".env")):
        return "credential_like"
    if any(t in low for t in ("id_rsa", "id_ed25519")):
        return "private_key"
    return None


def check_network_clean():
    loaded = [m for m in DENY_MODULES if m in sys.modules]
    # urllib3 / requests 可能被第三方库间接载入，只有在“本 Skill 主进程”中才算风险。
    # 这里给出明确清单，由 audit 决定是否阻断。
    return loaded


def run_checks(out_dir=None, manifest=None, strict_network=True):
    """返回自检报告；发现硬性风险时抛 SandboxError。"""
    report = {"ok": True, "checks": [], "warnings": []}

    roots = safe_roots()
    report["checks"].append({"name": "safe_roots", "ok": True,
                             "detail": [str(r) for r in roots]})

    if out_dir:
        od = Path(out_dir).expanduser()
        try:
            od.mkdir(parents=True, exist_ok=True)
            probe = od / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            report["checks"].append({"name": "output_writable", "ok": True,
                                     "detail": str(od.resolve())})
        except OSError as e:
            raise SandboxError("输出目录不可写：%s（%s）" % (od, e))

    loaded = check_network_clean()
    if loaded and strict_network:
        raise SandboxError("检测到外联模块已加载：%s；本 Skill 不允许联网。" % ", ".join(loaded))
    if loaded:
        report["warnings"].append("已加载外联模块：%s" % ", ".join(loaded))
    report["checks"].append({"name": "no_network_modules", "ok": not loaded,
                             "detail": loaded})

    if manifest:
        perms = (manifest.get("permissions") or {})
        if (perms.get("network") or {}).get("allow"):
            raise SandboxError("manifest 声明了网络白名单，与只读离线原则冲突。")
        report["checks"].append({"name": "manifest_readonly", "ok": True,
                                 "detail": "network.allow = []"})
    return report


def guard_output_size(paths):
    total = 0
    for p in paths:
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    if total > MAX_OUTPUT_BYTES:
        raise SandboxError("输出总大小 %.1f MB 超过上限 %.1f MB"
                           % (total / 1048576.0, MAX_OUTPUT_BYTES / 1048576.0))
    return total


# ------------------------------------------------------------ 闸门 1：安装期静态审查

# audit-static:ignore-begin —— 以下是规则表本身，扫描时跳过，避免自我命中
FORBIDDEN_CALLS = ("eval(", "exec(", "os.system(", "subprocess.call(", "subprocess.Popen(",
                   "pickle.loads(", "marshal.loads(")
FORBIDDEN_WRITES = ("shutil.rmtree(", "os.remove(", "os.rmdir(", "Path.unlink(",
                    "open(\".workbuddy", "rm -rf")
# audit-static:ignore-end


def audit_static(root=None):
    """安装期静态审查：不执行任何业务代码，只读源码做文本级判定。

    返回 (ok, findings)。findings 每项含 file / line / kind / detail。
    """
    root = Path(root or Path(__file__).resolve().parent.parent)
    findings = []

    py_files = sorted((root / "scripts").rglob("*.py")) if (root / "scripts").exists() else []
    for f in py_files:
        rel = f.relative_to(root).as_posix()
        in_ignore = False
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            findings.append({"file": rel, "line": 0, "kind": "unreadable", "detail": str(e)})
            continue
        for i, line in enumerate(text.splitlines(), 1):
            low = line.strip()
            if "audit-static:ignore-begin" in low:
                in_ignore = True
                continue
            if "audit-static:ignore-end" in low:
                in_ignore = False
                continue
            if in_ignore:
                continue
            if low.startswith("#") or low.startswith('"""') or low.startswith("'''"):
                continue
            for mod in DENY_MODULES:
                if ("import %s" % mod) in low or ("from %s" % mod) in low:
                    findings.append({"file": rel, "line": i, "kind": "network_import",
                                     "detail": mod})
            for call in FORBIDDEN_CALLS:
                if call in low:
                    findings.append({"file": rel, "line": i, "kind": "dynamic_exec",
                                     "detail": call.rstrip("(")})
            for w in FORBIDDEN_WRITES:
                if w in low:
                    findings.append({"file": rel, "line": i, "kind": "destructive_write",
                                     "detail": w})

    # manifest 网络白名单必须为空
    mf = root / "manifest.json"
    if mf.exists():
        try:
            import json as _json
            m = _json.loads(mf.read_text(encoding="utf-8"))
            if (m.get("permissions", {}).get("network", {}) or {}).get("allow"):
                findings.append({"file": "manifest.json", "line": 0, "kind": "network_allow",
                                 "detail": "network.allow 必须为空数组"})
        except ValueError as e:
            findings.append({"file": "manifest.json", "line": 0, "kind": "bad_manifest",
                             "detail": str(e)})

    hard = [f for f in findings if f["kind"] in
            ("network_import", "dynamic_exec", "network_allow", "bad_manifest")]
    return (not hard), findings


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="安全自检：安装期静态审查 / 运行前自检")
    ap.add_argument("--audit-static", action="store_true", help="安装期静态审查（只读源码）")
    ap.add_argument("--out", help="同时校验输出目录可写")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--strict", action="store_true", help="把警告也视为失败")
    args = ap.parse_args(argv)

    if args.audit_static:
        ok, findings = audit_static()
        if args.json:
            import json as _json
            print(_json.dumps({"ok": ok, "findings": findings}, ensure_ascii=False, indent=2))
        else:
            print("安装期静态审查：%s" % ("通过" if ok else "未通过"))
            if not findings:
                print("  未发现网络导入、动态执行、破坏性写入、网络白名单。")
            for f in findings[:50]:
                print("  [%s] %s:%s %s" % (f["kind"], f["file"], f["line"], f["detail"]))
            soft = [f for f in findings if f["kind"] in ("destructive_write", "unreadable")]
            if soft:
                print("  提示：%d 处写入/删除类调用，需人工确认为输出目录范围。" % len(soft))
        return 0 if ok else 1

    try:
        mf = Path(__file__).resolve().parent.parent / "manifest.json"
        manifest = {}
        if mf.exists():
            import json as _json
            manifest = _json.loads(mf.read_text(encoding="utf-8"))
        rep = run_checks(args.out, manifest)
    except SandboxError as e:
        print("运行前自检未通过：%s" % e)
        return 1
    if args.json:
        import json as _json
        print(_json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("运行前自检：通过")
        for c in rep["checks"]:
            print("  [%s] %s" % ("ok" if c["ok"] else "warn", c["name"]))
        for w in rep["warnings"]:
            print("  警告：%s" % w)
    if args.strict and rep["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
