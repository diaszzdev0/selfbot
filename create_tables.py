import psycopg2

conn = psycopg2.connect('postgresql://neondb_owner:npg_3RJa4bcMnUKm@ep-silent-cloud-anryyduw-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require')
cur = conn.cursor()

cur.execute('''CREATE TABLE IF NOT EXISTS pagamentos_usados (
    hash TEXT PRIMARY KEY,
    user_id INTEGER,
    thread_id BIGINT,
    discord_user_id BIGINT,
    nome TEXT,
    valor TEXT,
    usado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')

cur.execute('''CREATE TABLE IF NOT EXISTS threads_enviadas (
    user_id INTEGER,
    thread_id BIGINT,
    PRIMARY KEY(user_id, thread_id)
)''')

cur.execute('''CREATE TABLE IF NOT EXISTS sala_historico (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')

conn.commit()

cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
print('Tabelas:', [r[0] for r in cur.fetchall()])
conn.close()
