import requests

TOKEN = '7a9131befcdc938552460cab541b28d56fcfbb2a-916e86b458deb44fc8c869c10c98863a82dab435eb5097a6e504ec5ee372de8d'
APP_ID = 'd676168381434cdfb3126b40296de390'

endpoints = [
    f'https://api.squarecloud.app/v2/apps/{APP_ID}/deploy',
    f'https://api.squarecloud.app/v2/apps/{APP_ID}/upload',
    f'https://api.squarecloud.app/v2/apps/{APP_ID}/files/upload',
    f'https://api.squarecloud.app/v2/apps/{APP_ID}/redeploy',
]

results = []
for url in endpoints:
    with open('deploy_final.zip', 'rb') as f:
        r = requests.post(url, headers={'Authorization': TOKEN},
                          files={'file': ('deploy_final.zip', f, 'application/zip')})
    results.append(f'{url}\n  -> {r.status_code}: {r.text[:200]}')

with open('find_app_out.txt', 'w', encoding='utf-8') as f:
    f.write('\n\n'.join(results))
