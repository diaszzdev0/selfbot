import requests

# Primeiro faz login para pegar o cookie de sessão
BASE = 'https://d676168381434cdfb3126b40296de390.cloud.squareweb.app'

s = requests.Session()

# Login como admin
r = s.post(BASE + '/', data={'username': 'DiasDev', 'password': 'DiasDev0'}, allow_redirects=True, verify=False)
with open('migrate_result.txt', 'w', encoding='utf-8') as f:
    f.write(f'LOGIN: {r.status_code} url={r.url}\n')
    r2 = s.get(BASE + '/admin/_migrate_db', verify=False)
    f.write(f'MIGRATE: {r2.status_code} {r2.text[:300]}\n')
