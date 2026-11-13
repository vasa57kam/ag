_MIGRATED = False

def _migrate(c):
    """Старые БД v4/v5 (session_id/timestamp) -> v6 (sid/ts). Идемпотентно."""
    mc = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
    if mc and "sid" not in mc and "session_id" in mc:
        ts = "timestamp" if "timestamp" in mc else "CURRENT_TIMESTAMP"
        c.execute("ALTER TABLE messages RENAME TO messages_old")
        c.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, role TEXT, content TEXT, ts DATETIME DEFAULT CURRENT_TIMESTAMP)")
        c.execute(f"INSERT INTO messages(id,sid,role,content,ts) SELECT id,session_id,role,content,{ts} FROM messages_old")
        c.execute("DROP TABLE messages_old")
    tc = {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
    if tc and "sid" not in tc and "session_id" in tc:
        ts = "timestamp" if "timestamp" in tc else "CURRENT_TIMESTAMP"
        info = "COALESCE(action,'') || ' ' || COALESCE(progress,'')" if "action" in tc else "''"
        c.execute("ALTER TABLE tasks RENAME TO tasks_old")
        c.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, status TEXT, info TEXT, ts DATETIME DEFAULT CURRENT_TIMESTAMP)")
        c.execute(f"INSERT INTO tasks(id,sid,status,info,ts) SELECT id,session_id,status,{info},{ts} FROM tasks_old")
        c.execute("DROP TABLE tasks_old")
    c.commit()

def db():
    global _MIGRATED
    c = sqlite3.connect(DB, timeout=30)
    if not _MIGRATED:
        _migrate(c)
        _MIGRATED = True
    c.execute("CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, role TEXT, content TEXT, ts DATETIME DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, status TEXT, info TEXT, ts DATETIME DEFAULT CURRENT_TIMESTAMP)")
    return c
