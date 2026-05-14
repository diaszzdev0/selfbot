import requests, os

API_KEY = '7a9131befcdc938552460cab541b28d56fcfbb2a-d9729f5757fcc261043691aabca91bd35b33f19f210390335793566a066ded69'
APP_ID = 'd676168381434cdfb3126b40296de390'
result_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deploy_status.txt')

lines = []

# Status atual
r = requests.get(f'https://api.squarecloud.app/v2/apps/{APP_ID}/status', headers={'Authorization': API_KEY})
lines.append(f'STATUS: {r.status_code} {r.text}')

# Tenta o deploy
with open('deploy_final.zip', 'rb') as f:
    r2 = requests.post(
        f'https://api.squarecloud.app/v2/apps/{APP_ID}/commit',
        headers={'Authorization': API_KEY},
        files={'file': ('deploy_final.zip', f, 'application/zip')},
        timeout=120
    )
lines.append(f'COMMIT: {r2.status_code} {r2.text}')

# Verifica deployments
r3 = requests.get(f'https://api.squarecloud.app/v2/apps/{APP_ID}/deployments', headers={'Authorization': API_KEY})
lines.append(f'DEPLOYMENTS: {r3.status_code} {r3.text[:300]}')

with open(result_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
