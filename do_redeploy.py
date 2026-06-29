import requests, os

API_KEY = '7a9131befcdc938552460cab541b28d56fcfbb2a-d9729f5757fcc261043691aabca91bd35b33f19f210390335793566a066ded69'
APP_ID = 'd676168381434cdfb3126b40296de390'
result_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deploy_status.txt')

lines = []

# Força redeploy (git pull + restart)
r = requests.post(f'https://api.squarecloud.app/v2/apps/{APP_ID}/redeploy', headers={'Authorization': API_KEY}, timeout=30)
lines.append(f'REDEPLOY: {r.status_code} {r.text}')

# Se não tiver redeploy, tenta restart simples
r2 = requests.post(f'https://api.squarecloud.app/v2/apps/{APP_ID}/restart', headers={'Authorization': API_KEY}, timeout=30)
lines.append(f'RESTART: {r2.status_code} {r2.text}')

with open(result_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
