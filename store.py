"""SQLite: drafts, dedupe, last seen source message."""
import json, os, sqlite3, time


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS drafts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src_msg_id INTEGER, src_text TEXT, fp TEXT, data TEXT, post TEXT,
            status TEXT DEFAULT 'pending',          -- pending|published|ignored|failed|skipped
            admin_msgs TEXT DEFAULT '{}',            -- {admin_id: message_id}
            channel_msg_id INTEGER, created REAL, updated REAL);
        CREATE INDEX IF NOT EXISTS ix_fp ON drafts(fp);
        CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
        """)
        self.db.commit()

    # --- kv
    def get(self, k, default=None):
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set(self, k, v):
        self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
        self.db.commit()

    # --- drafts
    def seen(self, fp, hours=24):
        r = self.db.execute("SELECT 1 FROM drafts WHERE fp=? AND created>?", (fp, time.time() - hours * 3600)).fetchone()
        return r is not None

    def add(self, src_msg_id, src_text, fp, status="pending", data=None, post=None):
        now = time.time()
        cur = self.db.execute("INSERT INTO drafts(src_msg_id,src_text,fp,data,post,status,created,updated) "
                              "VALUES(?,?,?,?,?,?,?,?)",
                              (src_msg_id, src_text, fp, json.dumps(data or {}, ensure_ascii=False), post,
                               status, now, now))
        self.db.commit()
        return cur.lastrowid

    def update(self, did, **kw):
        if "data" in kw:
            kw["data"] = json.dumps(kw["data"], ensure_ascii=False)
        if "admin_msgs" in kw:
            kw["admin_msgs"] = json.dumps({str(k): v for k, v in kw["admin_msgs"].items()})
        kw["updated"] = time.time()
        cols = ", ".join(f"{k}=?" for k in kw)
        self.db.execute(f"UPDATE drafts SET {cols} WHERE id=?", (*kw.values(), did))
        self.db.commit()

    def draft(self, did):
        r = self.db.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["data"] = json.loads(d["data"] or "{}")
        d["admin_msgs"] = {int(k): v for k, v in json.loads(d["admin_msgs"] or "{}").items()}
        return d

    def pending_ids(self, hours=24):
        rows = self.db.execute("SELECT id FROM drafts WHERE status='pending' AND created>?",
                               (time.time() - hours * 3600,)).fetchall()
        return [r["id"] for r in rows]

    def stats(self):
        day = time.time() - 86400
        q = lambda s: self.db.execute(s, (day,)).fetchone()[0]
        return {"pending": self.db.execute("SELECT COUNT(*) FROM drafts WHERE status='pending'").fetchone()[0],
                "published_24h": q("SELECT COUNT(*) FROM drafts WHERE status='published' AND updated>?"),
                "ignored_24h": q("SELECT COUNT(*) FROM drafts WHERE status='ignored' AND updated>?"),
                "skipped_24h": q("SELECT COUNT(*) FROM drafts WHERE status='skipped' AND created>?"),
                "failed_24h": q("SELECT COUNT(*) FROM drafts WHERE status='failed' AND created>?")}
