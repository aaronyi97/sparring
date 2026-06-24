"""种子邀请码生成：python -m sparring.seed_invite [数量] [备注]

发放对象记在 note 里，方便日后对账。一码一人。
"""
from __future__ import annotations

import sys

from .auth import AuthStore
from .config import load_config


def main() -> None:
    cfg = load_config()
    store = AuthStore(cfg.db_path)
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    note = sys.argv[2] if len(sys.argv) > 2 else ""
    print(f"生成 {n} 个邀请码（note={note!r}）：")
    for _ in range(n):
        print("  " + store.create_invite(note))


if __name__ == "__main__":
    main()
