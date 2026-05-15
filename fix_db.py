import psycopg2

conn = psycopg2.connect('postgresql://neondb_owner:npg_3RJa4bcMnUKm@ep-silent-cloud-anryyduw-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require')
cur = conn.cursor()

cur.execute('DELETE FROM bot_status WHERE user_id NOT IN (SELECT id FROM "user")')
print(f'bot_status orfaos removidos: {cur.rowcount}')

for t in ['threads_enviadas', 'pagamentos_usados', 'sala_historico']:
    try:
        cur.execute(f'DELETE FROM {t} WHERE user_id NOT IN (SELECT id FROM "user")')
        print(f'{t} orfaos removidos: {cur.rowcount}')
    except Exception as e:
        print(f'{t}: {e}')
        conn.rollback()

conn.commit()

cur.execute('SELECT id, username, is_admin FROM "user"')
print('Users:', cur.fetchall())

cur.execute('SELECT COUNT(*) FROM bot_status')
print('bot_status count:', cur.fetchone()[0])

conn.close()
print('OK!')
