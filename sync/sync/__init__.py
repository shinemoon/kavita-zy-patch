"""掌阅 ↔ Kavita 双向进度同步

模块构成:
    sync.py       CLI 入口 (push / pull / status / daemon)
    zy_client.py  掌阅 cloud API 包装
    kavita_db.py  直接 sqlite 读/写 Kavita 进度表
    mapping.py    SeriesId/chapter_id 桥接 (Kavita int ↔ 掌阅 str)

设计选择:
    - 走 sqlite 写 Kavita, 不用 REST (0.9.0.2 REST 端点不全, sql 直接)
    - 走 cloud API (uploadProcess/getProcess) 与掌阅通信
    - 不启后台 daemon:  --once 单次同步, --daemon 长循环
    - 状态持久: state/sync.sqlite (last_push_at / last_pull_at / 已同步指针)
    - 凭据从环境变量 + 已有 /vol2/1001/zhangyue-bridge/.auth.json 读

触发 (来自 SKILL.md):
    "同步掌阅进度" / "把掌阅的进度推过来" / "把 Kavita 的进度推上去" /
    "掌阅进度 sync" / "zhangyue-kavita sync" / "掌阅同步 状态"
"""
__version__ = "0.1.0"
