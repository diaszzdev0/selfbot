import requests

TOKEN = '7a9131befcdc938552460cab541b28d56fcfbb2a-c88083c0496a1c318e051555ae19a27e393e2092102dfe7ef9b2ac250f15768f'
APP_ID = 'ab6685b8e21b42bea4e8f0d65c800795'
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
