"""通过 GitHub REST API 把 zip2epub 发布到 suiyu-ygl/zip2epub。

本机到 github.com:443 不通，但 api.github.com 可用，因此用 Git Data API 建提交，
不依赖 git push。凭据从 Windows 凭据管理器读取，只在内存里使用，不打印。
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
OWNER = "suiyu-ygl"
REPO = "zip2epub"

INCLUDE_DIRS = ["gui", "z2e", "tests", "tools", "docs", "build"]
INCLUDE_FILES = [
    "README.md", "requirements.txt", "run.bat", "build.ps1",
    "launcher.py", "__main__.py", ".gitignore", "LICENSE",
]
EXCLUDE = {
    "tools/read_credential.ps1",
    "tools/publish_to_github.py",
    "docs/preview-comic.html",
    "docs/app.ico",
    "docs/exe-gui.png",
}


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR), ("LastWritten", wt.FILETIME),
        ("CredentialBlobSize", wt.DWORD), ("CredentialBlob", ctypes.c_void_p),
        ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wt.LPWSTR), ("UserName", wt.LPWSTR),
    ]


def read_token() -> str:
    target = f"LegacyGeneric:target=git:https://{OWNER}@github.com"
    advapi = ctypes.windll.advapi32
    ptr = ctypes.c_void_p()
    if not advapi.CredReadW(target, 1, 0, ctypes.byref(ptr)):
        raise SystemExit("读取 GitHub 凭据失败（凭据管理器里没有记录）")
    try:
        cred = ctypes.cast(ptr, ctypes.POINTER(CREDENTIAL)).contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return blob.decode("utf-16-le").rstrip("\x00")
    finally:
        advapi.CredFree(ptr)


def api(method: str, path: str, token: str, payload: dict | None = None) -> tuple[int, dict]:
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"token {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "zip2epub-publisher")
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, {"message": body[:400]}


def collect_files() -> list[Path]:
    files: list[Path] = []
    for name in INCLUDE_FILES:
        path = ROOT / name
        if path.exists():
            files.append(path)

    for folder in INCLUDE_DIRS:
        base = ROOT / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel in EXCLUDE:
                continue
            if "__pycache__" in rel or path.suffix in {".pyc", ".tmp"}:
                continue
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            files.append(path)

    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--public", action="store_true", help="创建公开仓库（默认私有）")
    parser.add_argument("--message", default="zip2epub: 批量 ZIP 转 EPUB（源码 + 测试 + 文档）")
    args = parser.parse_args()

    token = read_token()
    status, me = api("GET", "/user", token)
    if status != 200:
        print(f"[FAIL] 凭据无效：{status} {me.get('message')}")
        return 1
    login = me["login"]
    print(f"account: {login}")

    status, repo = api("GET", f"/repos/{OWNER}/{REPO}", token)
    exists = status == 200
    visibility = repo.get("private") if exists else None
    print(f"repo {OWNER}/{REPO}: {'exists (private=%s)' % visibility if exists else 'missing'}")

    files = collect_files()
    print(f"files: {len(files)}")
    for path in files:
        print(f"  {path.relative_to(ROOT).as_posix():40s} {path.stat().st_size:>8,d} B")

    if args.dry_run:
        print("[DRY-RUN] 不做任何写操作")
        return 0

    if not exists:
        status, payload = api("POST", "/user/repos", token, {
            "name": REPO,
            "description": "批量把 ZIP 内图片/文字装订成 EPUB 3，带编辑与预览；单文件 exe，纯本地",
            "private": not args.public,
            "has_issues": True,
            "has_wiki": False,
            "auto_init": False,
        })
        if status not in (200, 201):
            print(f"[FAIL] 创建仓库失败：{status} {payload.get('message')}")
            return 1
        repo = payload
        print(f"created: {repo['html_url']} (private={repo['private']})")

    blobs = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        content = base64.b64encode(path.read_bytes()).decode("ascii")
        status, payload = api("POST", f"/repos/{OWNER}/{REPO}/git/blobs", token,
                              {"content": content, "encoding": "base64"})
        if status not in (200, 201):
            # 空仓库第一次调用 Git Data API 会返回 409：先用 Contents API 建一个根提交
            if status == 409:
                print("  empty repo -> bootstrap commit via contents API")
                readme = ROOT / "README.md"
                status2, payload2 = api(
                    "PUT", f"/repos/{OWNER}/{REPO}/contents/README.md", token,
                    {"message": "chore: bootstrap", "content": base64.b64encode(readme.read_bytes()).decode("ascii")},
                )
                if status2 not in (200, 201):
                    print(f"[FAIL] bootstrap 失败：{status2} {payload2.get('message')}")
                    return 1
                status, payload = api("POST", f"/repos/{OWNER}/{REPO}/git/blobs", token,
                                      {"content": content, "encoding": "base64"})
            if status not in (200, 201):
                print(f"[FAIL] 上传 {rel} 失败：{status} {payload.get('message')}")
                return 1
        blobs.append({"path": rel, "mode": "100644", "type": "blob", "sha": payload["sha"]})
        print(f"  blob {rel}")

    status, ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/main", token)
    parents = [ref["object"]["sha"]] if status == 200 else []
    base_tree = None
    if parents:
        status, parent_commit = api("GET", f"/repos/{OWNER}/{REPO}/git/commits/{parents[0]}", token)
        if status == 200:
            base_tree = parent_commit["tree"]["sha"]

    tree_payload = {"tree": blobs}
    if base_tree:
        tree_payload["base_tree"] = base_tree
    status, tree = api("POST", f"/repos/{OWNER}/{REPO}/git/trees", token, tree_payload)
    if status not in (200, 201):
        print(f"[FAIL] 建 tree 失败：{status} {tree.get('message')}")
        return 1

    status, commit = api("POST", f"/repos/{OWNER}/{REPO}/git/commits", token, {
        "message": args.message,
        "tree": tree["sha"],
        "parents": parents,
    })
    if status not in (200, 201):
        print(f"[FAIL] 建 commit 失败：{status} {commit.get('message')}")
        return 1

    if parents:
        status, _ = api("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/main", token,
                        {"sha": commit["sha"], "force": False})
    else:
        status, _ = api("POST", f"/repos/{OWNER}/{REPO}/git/refs", token,
                        {"ref": "refs/heads/main", "sha": commit["sha"]})
    if status not in (200, 201):
        print(f"[FAIL] 更新分支失败：{status}")
        return 1

    print(f"[OK] https://github.com/{OWNER}/{REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
