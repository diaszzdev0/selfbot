import requests

TOKEN = '7a9131befcdc938552460cab541b28d56fcfbb2a-916e86b458deb44fc8c869c10c98863a82dab435eb5097a6e504ec5ee372de8d'
APP_ID = 'd676168381434cdfb3126b40296de390'

# squarecloud SDK source uses PATCH /v2/apps/{id} with multipart
methods_endpoints = [
    ('PATCH', f'https://api.squarecloud.app/v2/apps/{APP_ID}'),
    ('PUT',   f'https://api.squarecloud.app/v2/apps/{APP_ID}'),
    ('POST',  f'https://api.squarecloud.app/v2/apps/{APP_ID}/commit'),
    ('PATCH', f'https://api.squarecloud.app/v2/apps/{APP_ID}/commit'),
]

results = []
for method, url in methods_endpoints:
    with open('deploy_final.zip', 'rb') as f:
        r = requests.request(method, url,
            headers={'Authorization': TOKEN},
            files={'file': ('deploy_final.zip', f, 'application/zip')})
    results.append(f'{method} {url} -> {r.status_code}: {r.text[:150]}')

with open('sq_result.txt', 'w', encoding='utf-8') as f:
    f.write('\n\n'.join(results))
