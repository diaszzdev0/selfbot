import requests

APP_ID = "aef7374303dd46bcb0f02051c52541a5"
API_TOKEN = "7a9131befcdc938552460cab541b28d56fcfbb2a-7b9d750bd4d78dcd0eabcb9d7f2d20615261ee2ee3a72232aa605783fcdfc544"

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
            print("Aplicacao esta rodando. Fazendo commit forcado via deploy_final.zip...")
            url_commit = f"https://api.squarecloud.app/v2/apps/{APP_ID}/commit"

            # A API do SquareCloud espera um multipart/form-data com o zip.
            # Isso evita o erro 415 INVALID_CONTENT_TYPE.
            try:
                with open('deploy_final.zip', 'rb') as f:
                    commit_response = requests.post(
                        url_commit,
                        headers=headers,
                        files={'file': ('deploy_final.zip', f, 'application/zip')},
                        timeout=180
                    )

                if commit_response.status_code == 200:
                    print("SUCESSO! Commit forcado realizado.")
                    print("A aplicacao vai baixar o codigo atualizado do GitHub.")
                    print()
                    print("Aguarde 2-3 minutos para o deploy completar.")
                else:
                    print(f"Erro ao fazer commit: {commit_response.status_code}")
                    print(commit_response.text)
            except FileNotFoundError:
                print("Erro: deploy_final.zip nao encontrado no diretorio atual.")
                print("Gere o deploy_final.zip antes de rodar este script.")
            except Exception as exc:
                print(f"Erro ao fazer commit forcado: {type(exc).__name__}: {exc}")
    else:
        print(f"Erro ao verificar status: {response.status_code}")
        print(response.text)
        
except Exception as e:
    print(f"Erro: {e}")

print()
print("Acompanhe em: https://squarecloud.app/dashboard/app/" + APP_ID)
