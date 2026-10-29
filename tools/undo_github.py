"""撤销上传：优先删除仓库；无 delete_repo 权限时，退化为清空仓库内容。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import publish_to_github as gh  # noqa: E402


def main() -> int:
    token = gh.read_token()
    owner, repo = gh.OWNER, gh.REPO

    status, info = gh.api("GET", f"/repos/{owner}/{repo}", token)
    if status == 404:
        print("[OK] 仓库已不存在")
        return 0
    if status != 200:
        print(f"[FAIL] 查询失败 {status}: {info.get('message')}")
        return 1

    branch = info.get("default_branch", "main")
    print(f"target: {info['full_name']} private={info['private']} branch={branch}")

    status, payload = gh.api("DELETE", f"/repos/{owner}/{repo}", token)
    print(f"delete: {status}")
    if status in (200, 204):
        status, _ = gh.api("GET", f"/repos/{owner}/{repo}", token)
        print(f"verify: {status} (404 = 已删除)")
        return 0

    print(f"deletion not permitted: {payload.get('message')}")

    # 退化方案：把默认分支指向一棵空树，抹掉所有文件
    status, ref = gh.api("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}", token)
    if status != 200:
        print(f"[FAIL] 读取分支失败 {status}")
        return 1
    parent = ref["object"]["sha"]

    status, tree = gh.api("POST", f"/repos/{owner}/{repo}/git/trees", token, {"tree": []})
    if status not in (200, 201):
        print(f"[FAIL] 建空树失败 {status} {tree.get('message')}")
        return 1

    status, commit = gh.api("POST", f"/repos/{owner}/{repo}/git/commits", token, {
        "message": "chore: remove uploaded content",
        "tree": tree["sha"],
        "parents": [parent],
    })
    if status not in (200, 201):
        print(f"[FAIL] 建清空提交失败 {status} {commit.get('message')}")
        return 1

    status, _ = gh.api("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}", token,
                       {"sha": commit["sha"], "force": True})
    print(f"wipe ref: {status}")

    status, listing = gh.api("GET", f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1", token)
    files = [i for i in listing.get("tree", []) if i["type"] == "blob"]
    print(f"remaining files: {len(files)}")
    print(f"repo still exists: https://github.com/{owner}/{repo} (需要 delete_repo 权限或手动删除)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
