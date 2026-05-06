import requests

APP_ID = "67188c49ce2042fabe286376f92bf9f2"
API_TOKEN = "7a9131befcdc938552460cab541b28d56fcfbb2a-ec2626eb6056848595b82c31b713285efefc8e94ed20b1d1bb4e502d231312ec"

print("=" * 60)
print("VERIFICANDO STATUS DA APLICACAO")
print("=" * 60)
print()

# Verifica status
url_status = f"https://api.squarecloud.app/v2/apps/{APP_ID}/status"
headers = {"Authorization": API_TOKEN}

try:
    response = requests.get(url_status, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print("STATUS DA APLICACAO:")
        print(f"  Status: {data.get('response', {}).get('status', 'N/A')}")
        print(f"  CPU: {data.get('response', {}).get('cpu', 'N/A')}")
        print(f"  RAM: {data.get('response', {}).get('ram', 'N/A')}")
        print(f"  Uptime: {data.get('response', {}).get('uptimeFormatted', 'N/A')}")
        print()
        
        # Se estiver rodando, faz commit forçado
        if data.get('response', {}).get('status') == 'running':
            print("Aplicacao esta rodando. Fazendo commit forcado...")
            url_commit = f"https://api.squarecloud.app/v2/apps/{APP_ID}/commit"
            
            commit_response = requests.post(url_commit, headers=headers)
            
            if commit_response.status_code == 200:
                print("SUCESSO! Commit forcado realizado.")
                print("A aplicacao vai baixar o codigo atualizado do GitHub.")
                print()
                print("Aguarde 2-3 minutos para o deploy completar.")
            else:
                print(f"Erro ao fazer commit: {commit_response.status_code}")
                print(commit_response.text)
    else:
        print(f"Erro ao verificar status: {response.status_code}")
        print(response.text)
        
except Exception as e:
    print(f"Erro: {e}")

print()
print("Acompanhe em: https://squarecloud.app/dashboard/app/" + APP_ID)
