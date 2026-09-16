"""评测卫生：备份当前 meeting.db 后清空所有业务表。

用户要求（2026-09-16）：每次评测前"把现在的备份出来然后清空一下"，
避免历史会议数据干扰新测试（会议列表堆积、GT 对不上号）。

用法（项目根目录）:
    python server/scripts/reset_eval_db.py            # 备份+清空
    python server/scripts/reset_eval_db.py --dry      # 只打印不执行

备份：server/meeting.db.bak-<YYYYmmdd-HHMMSS>
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "server" / "meeting.db"
TABLES = ["summaries", "utterances_re", "utterances", "meetings"]


def main() -> None:
    dry = "--dry" in sys.argv
    if not DB.exists():
        sys.exit(f"找不到数据库: {DB}")
    bak = DB.with_name(f"meeting.db.bak-{datetime.now():%Y%m%d-%H%M%S}")
    print(f"备份: {DB.name} -> {bak.name}")
    if not dry:
        shutil.copy2(DB, bak)
    con = sqlite3.connect(DB)
    try:
        for t in TABLES:
            n = con.execute(f"select count(*) from {t}").fetchone()[0]
            print(f"  清空 {t}: {n} 行")
            if not dry:
                con.execute(f"delete from {t}")
        if not dry:
            con.commit()
            con.execute("vacuum")
    finally:
        con.close()
    print("完成（--dry 未实际执行）" if dry else "完成")


if __name__ == "__main__":
    main()
