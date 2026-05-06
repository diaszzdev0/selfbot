import requests
import os
import sys

# ID da aplicação na Square Cloud
APP_ID = "67188c49ce2042fabe286376f92bf9f2"

def deploy_squarecloud():
    print("=" * 60)
    print("DEPLOY AUTOMATICO - SQUARE CLOUD")
    print("=" * 60)
    print()
    
    # Solicita o token da API
    print("Para fazer o deploy automatico, preciso do seu token da Square Cloud API.")
    print("Voce pode encontrar em: https://squarecloud.app/account")
    print()
    
    api_token = input("Cole seu token da Square Cloud API aqui: ").strip()
    
    if not api_token:
        print("Erro: Token nao fornecido!")
        sys.exit(1)
    
    print()
    print("Fazendo restart da aplicacao...")
    
    # Endpoint para restart
    url = f"https://api.squarecloud.app/v2/apps/{APP_ID}/restart"
    
    headers = {
        "Authorization": api_token
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
            print()
            print("Possivel solucao:")
            print("1. Verifique se o token esta correto")
            print("2. Acesse manualmente: https://squarecloud.app/dashboard")
            print("3. Clique em 'Restart' na sua aplicacao")
    
    except Exception as e:
        print(f"Erro na requisicao: {e}")
        print()
        print("Faca o deploy manual:")
        print("1. Acesse: https://squarecloud.app/dashboard")
        print("2. Encontre 'Selfbot Manager'")
        print("3. Clique em 'Restart'")

if __name__ == "__main__":
    deploy_squarecloud()
