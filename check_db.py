import sqlite3, os

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot.db")
with sqlite3.connect(db) as con:
    rows = con.execute("SELECT hash, nome, valor, usado_em FROM pagamentos_usados ORDER BY usado_em DESC LIMIT 20").fetchall()
    with open("db_check.txt", "w", encoding="utf-8") as f:
        f.write(f"Total: {len(rows)}\n\n")
        for r in rows:
            f.write(f"hash={r[0]} | nome={r[1]} | valor={r[2]} | em={r[3]}\n")
