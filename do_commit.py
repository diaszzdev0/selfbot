import requests, os

API_KEY = '7a9131befcdc938552460cab541b28d56fcfbb2a-7b9d750bd4d78dcd0eabcb9d7f2d20615261ee2ee3a72232aa605783fcdfc544'
APP_ID = 'aef7374303dd46bcb0f02051c52541a5'
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
