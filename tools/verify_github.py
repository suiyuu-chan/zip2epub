"""核对 GitHub 仓库内容，并把引导提交压成一个干净的根提交。"""

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
    if status != 200:
        print(f"[FAIL] {status} {info.get('message')}")
        return 1
    print(f"repo: {info['full_name']}  private={info['private']}  branch={info.get('default_branch')}")

    status, commits = gh.api("GET", f"/repos/{owner}/{repo}/commits", token)
    print(f"commits: {len(commits)}")
    for commit in commits:
        print(f"  {commit['sha'][:8]} {commit['commit']['message'].splitlines()[0]}")

    status, tree = gh.api("GET", f"/repos/{owner}/{repo}/git/trees/{info['default_branch']}?recursive=1", token)
    if status != 200:
        print(f"[FAIL] tree {status}")
        return 1
    paths = sorted(item["path"] for item in tree["tree"] if item["type"] == "blob")
    print(f"files: {len(paths)}")
    for path in paths:
        print(f"  {path}")

    # 把 bootstrap + 主提交压成一个根提交（树保持不变）
    head = commits[0]
    status, commit_detail = gh.api("GET", f"/repos/{owner}/{repo}/git/commits/{head['sha']}", token)
    tree_sha = commit_detail["tree"]["sha"]
    status, root_commit = gh.api("POST", f"/repos/{owner}/{repo}/git/commits", token, {
        "message": "zip2epub: 批量 ZIP 转 EPUB（源码 + 测试 + 文档）",
        "tree": tree_sha,
        "parents": [],
    })
    if status not in (200, 201):
        print(f"[FAIL] squash {status} {root_commit.get('message')}")
        return 1
    status, _ = gh.api("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{info['default_branch']}", token,
                       {"sha": root_commit["sha"], "force": True})
    if status not in (200, 201):
        print(f"[FAIL] ref {status}")
        return 1

    status, commits = gh.api("GET", f"/repos/{owner}/{repo}/commits", token)
    print(f"after squash: {len(commits)} commit(s) -> {commits[0]['sha'][:8]}")
    print(f"[OK] {info['html_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
