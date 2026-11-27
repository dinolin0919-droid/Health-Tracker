"""
把電腦上 data/ 裡的帳號和紀錄「複製」到雲端資料庫（本機資料不會刪掉）

    python migrate_to_cloud.py            # 全部帳號
    python migrate_to_cloud.py test demo  # 只搬這幾個
    python migrate_to_cloud.py --force    # 雲端已經有同名帳號時也覆蓋

雲端設定：.streamlit/secrets.toml 或環境變數 SUPABASE_URL、SUPABASE_KEY（見 README.md）
"""

import argparse
import os

from user_management import LocalStorage, cloud_storage_from_secrets


def main():
    ap = argparse.ArgumentParser(description="把本機帳號複製到雲端")
    ap.add_argument("users", nargs="*", help="要搬的帳號（不填就全部）")
    ap.add_argument("--force", action="store_true", help="雲端已有同名帳號時覆蓋")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    local = LocalStorage(os.path.join(here, "data"))
    cloud = cloud_storage_from_secrets(here)

    names = args.users or [k.split(":", 1)[1] for k in local.keys("account:")]
    for name in names:
        acc = local.get(f"account:{name}")
        if acc is None:
            print(f"  ✗ 本機沒有帳號 {name}")
            continue
        if cloud.get(f"account:{name}") is not None and not args.force:
            print(f"  - 雲端已經有 {name}，跳過（要覆蓋請加 --force）")
            continue
        doc = local.get(f"user:{acc['user_id']}")
        cloud.put(f"user:{acc['user_id']}", doc)
        truth = local.get(f"truth:{name}")
        if truth:
            cloud.put(f"truth:{name}", truth)
        cloud.put(f"account:{name}", acc)
        print(f"  ✓ {name}：{len(doc.get('records', {}))} 天的紀錄")

    for kind in ("foods", "exercises"):              # 自訂食物、運動：合併
        mine = local.get(f"custom:{kind}") or {}
        if mine:
            cloud.put(f"custom:{kind}", {**(cloud.get(f"custom:{kind}") or {}), **mine})
            print(f"  ✓ 自訂{'食物' if kind == 'foods' else '運動'} {len(mine)} 項")
    print("完成")


if __name__ == "__main__":
    main()
