import requests
TOKEN = '7a9131befcdc938552460cab541b28d56fcfbb2a-c88083c0496a1c318e051555ae19a27e393e2092102dfe7ef9b2ac250f15768f'
APP_ID = 'ab6685b8e21b42bea4e8f0d65c800795'

r = requests.get(f'https://api.squarecloud.app/v2/apps/{APP_ID}/status', headers={'Authorization': TOKEN})
r2 = requests.get(f'https://api.squarecloud.app/v2/apps/{APP_ID}/logs', headers={'Authorization': TOKEN})

with open('status_out.txt', 'w', encoding='utf-8') as f:
    f.write(r.text)
with open('sq_logs.txt', 'w', encoding='utf-8') as f:
    f.write(r2.text[-4000:])
