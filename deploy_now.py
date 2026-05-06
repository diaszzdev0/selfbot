import requests

APP_ID = "67188c49ce2042fabe286376f92bf9f2"
API_TOKEN = "7a9131befcdc938552460cab541b28d56fcfbb2a-ec2626eb6056848595b82c31b713285efefc8e94ed20b1d1bb4e502d231312ec"

print("=" * 60)
print("DEPLOY AUTOMATICO - SQUARE CLOUD")
print("=" * 60)
print()
print("Fazendo restart da aplicacao...")
print()

url = f"https://api.squarecloud.app/v2/apps/{APP_ID}/restart"

headers = {
    "Authorization": API_TOKEN
}

try:
    response = requests.post(url, headers=headers)
    
    if response.status_code == 200:
        print("SUCESSO! Aplicacao reiniciada.")
        print()
        print("A aplicacao vai:")
        print("1. Parar")
        print("2. Baixar o codigo atualizado do GitHub")
        print("3. Reinstalar dependencias")
        print("4. Iniciar novamente")
        print()
        print("Isso leva cerca de 2-5 minutos.")
        print()
        print("Acompanhe em: https://squarecloud.app/dashboard/app/" + APP_ID)
    else:
        print(f"Erro: {response.status_code}")
        print(response.text)

except Exception as e:
    print(f"Erro: {e}")
