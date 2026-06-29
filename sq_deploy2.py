import requests, sys, os

TOKEN = '7a9131befcdc938552460cab541b28d56fcfbb2a-916e86b458deb44fc8c869c10c98863a82dab435eb5097a6e504ec5ee372de8d'
APP_ID = 'd676168381434cdfb3126b40296de390'

out = open('sq_result.txt', 'w', encoding='utf-8')

# status
r = requests.get(f'https://api.squarecloud.app/v2/apps/{APP_ID}/status', headers={'Authorization': TOKEN})
out.write(f'STATUS: {r.status_code}\n{r.text[:400]}\n\n')

# logs
r2 = requests.get(f'https://api.squarecloud.app/v2/apps/{APP_ID}/logs', headers={'Authorization': TOKEN})
out.write(f'LOGS: {r2.status_code}\n{r2.text[:600]}\n\n')

# try deploy endpoints
for ep in ['deploy', 'upload', 'files', 'files/commit', 'redeploy']:
    with open('deploy_final.zip', 'rb') as f:
        r3 = requests.post(
            f'https://api.squarecloud.app/v2/apps/{APP_ID}/{ep}',
            headers={'Authorization': TOKEN},
            files={'file': ('deploy_final.zip', f, 'application/zip')}
        )
    out.write(f'POST {ep}: {r3.status_code} {r3.text[:200]}\n')

out.close()
print('done')
