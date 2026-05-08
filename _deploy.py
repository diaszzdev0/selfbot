import requests

API_KEY = '7a9131befcdc938552460cab541b28d56fcfbb2a-72ed07f1cf2412fd0bf6e7562e663119d40078b4fa523ac1841c2fba54c2965a'
APP_ID = '67188c49ce2042fabe286376f92bf9f2'
h = {'Authorization': API_KEY}

arquivos = ['imap_optimizer.py', 'bot_logic.py', 'squarecloud.app']

for nome in arquivos:
    with open(nome, 'rb') as f:
        content = f.read().decode('utf-8')
    r = requests.put(
        f'https://api.squarecloud.app/v2/apps/{APP_ID}/files',
        headers={**h, 'Content-Type': 'application/json'},
        json={'path': nome, 'content': content},
        timeout=30
    )
    # Verifica tamanho
    r2 = requests.get(f'https://api.squarecloud.app/v2/apps/{APP_ID}/files', headers=h, timeout=15)
    files = {f['name']: f for f in r2.json()['response'] if f['type'] == 'file'}
    cloud_size = files.get(nome, {}).get('size', '?')
    local_size = len(content.encode('utf-8'))
    status = "OK" if cloud_size == local_size else "ERRO"
    print(f"{status} {nome} -> HTTP {r.status_code} | local={local_size}b cloud={cloud_size}b")

r = requests.post(f'https://api.squarecloud.app/v2/apps/{APP_ID}/restart', headers=h, timeout=15)
print(f"\nRestart -> {r.status_code}: {r.text}")
