import requests
import time

APP_ID = "67188c49ce2042fabe286376f92bf9f2"
API_TOKEN = "7a9131befcdc938552460cab541b28d56fcfbb2a-ec2626eb6056848595b82c31b713285efefc8e94ed20b1d1bb4e502d231312ec"

print("=" * 60)
print("FORCANDO DEPLOY COMPLETO")
print("=" * 60)
print()

headers = {"Authorization": API_TOKEN}

# 1. Para a aplicação
print("1. Parando aplicacao...")
url_stop = f"https://api.squarecloud.app/v2/apps/{APP_ID}/stop"
response = requests.post(url_stop, headers=headers)
if response.status_code == 200:
    print("   OK - Aplicacao parada")
else:
    print(f"   Erro: {response.status_code}")

time.sleep(3)

# 2. Inicia novamente (vai baixar código novo)
print("2. Iniciando aplicacao (vai baixar codigo novo)...")
url_start = f"https://api.squarecloud.app/v2/apps/{APP_ID}/start"
response = requests.post(url_start, headers=headers)
if response.status_code == 200:
    print("   OK - Aplicacao iniciando")
    print()
    print("SUCESSO! Deploy completo iniciado.")
    print()
    print("A aplicacao vai:")
    print("1. Baixar codigo atualizado do GitHub")
    print("2. Instalar dependencias")
    print("3. Iniciar com o novo codigo")
    print()
    print("Aguarde 3-5 minutos.")
else:
    print(f"   Erro: {response.status_code}")
    print(response.text)

print()
print("Acompanhe: https://squarecloud.app/dashboard/app/" + APP_ID)
