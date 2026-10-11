"""MySQL 8 collation compatibility for langgraph-checkpoint-mysql 3.0.0."""


def align_checkpoint_collation():
    # JSON_TABLE in the upstream saver uses MySQL 8's utf8mb4 default collation.
    from ..db.mysql_client import get_mysql_client
    mysql = get_mysql_client()
    names = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
    rows = mysql.execute("SELECT TABLE_NAME, TABLE_COLLATION FROM information_schema.TABLES "
                         "WHERE TABLE_SCHEMA=DATABASE()")
    for row in rows or []:
        name = row["TABLE_NAME"]
        if name in names and row["TABLE_COLLATION"] != "utf8mb4_0900_ai_ci":
            mysql.execute(f"ALTER TABLE `{name}` CONVERT TO CHARACTER SET utf8mb4 "
                          "COLLATE utf8mb4_0900_ai_ci")
