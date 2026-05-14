import requests

TOKEN = '7a9131befcdc938552460cab541b28d56fcfbb2a-e1a28679adb5704fad6875951576563fee84060a1b6d8ecbf7f6a633cbfb5de7'
APP_ID = 'd676168381434cdfb3126b40296de390'
HEADERS = {'Authorization': TOKEN}

with open('deploy_final.zip', 'rb') as f:
    r = requests.post(
        f'https://api.squarecloud.app/v2/apps/{APP_ID}/commit',
        headers=HEADERS,
        files={'file': ('deploy_final.zip', f, 'application/zip')}
    )
print('commit:', r.status_code, r.text[:200])

r2 = requests.post(f'https://api.squarecloud.app/v2/apps/{APP_ID}/restart', headers=HEADERS)
print('restart:', r2.status_code, r2.text[:200])
