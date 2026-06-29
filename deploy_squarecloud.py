#!/usr/bin/env python3
"""
Script para atualizar aplicação na SquareCloud via API
"""

import os
import sys
import subprocess
import webbrowser
import zipfile
from datetime import datetime


def print_header():
    print("=" * 60)
    print("DEPLOY SQUARECLOUD - SELFBOT MANAGER")
    print("=" * 60)
    print()


def check_git_status():
    """Verifica se há mudanças não commitadas"""
    try:
        result = subprocess.run(['git', 'status', '--porcelain'], 
                              capture_output=True, text=True, cwd='.')
        return result.stdout.strip() == ""
    except:
        return False


def git_push():
    """Faz push das mudanças para o GitHub"""
    try:
        print("Fazendo push para GitHub...")
        
        subprocess.run(['git', 'add', '.'], check=True, cwd='.')
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_msg = f"Atualização automática - {timestamp}"
        subprocess.run(['git', 'commit', '-m', commit_msg], check=True, cwd='.')
        
        subprocess.run(['git', 'push', 'origin', 'main'], check=True, cwd='.')
        
        print("Push realizado com sucesso!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Erro no git: {e}")
        return False
    except Exception as e:
        print(f"Erro: {e}")
        return False


def create_deploy_zip():
    """Cria o arquivo zip para deploy"""
    print("Criando pacote de deploy...")
    
    include_files = [
        'app.py', 'models.py', 'bot.py', 'bot_logic.py', 'main.py',
        'requirements.txt', 'squarecloud.app', '.squareignore',
        'cli_multiusous_selfbot.py', 'cliente_app.py', 'imap_optimizer.py',
    ]
    
    templates_dir = 'templates'
    
    with zipfile.ZipFile('deploy_final.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in include_files:
            if os.path.exists(f):
                zf.write(f)
                print(f"  + {f}")
            else:
                print(f"  - {f} (não encontrado)")
        
        if os.path.exists(templates_dir):
            for root, dirs, files in os.walk(templates_dir):
                for file in files:
                    path = os.path.join(root, file)
                    zf.write(path)
    
    print("Pacote criado: deploy_final.zip")
    return True


def deploy_via_api():
    """Faz deploy via API da SquareCloud"""
    try:
        import requests
        
        TOKEN = '7a9131befcdc938552460cab541b28d56fcfbb2a-a53eac121622dc57f38f43db364273eb6f54e007621728393760a142be11f75e'
        APP_ID = 'd676168381434cdfb3126b40296de390'
        
        print("Fazendo upload via API...")
        
        # Check status
        r = requests.get(
            f'https://api.squarecloud.app/v1/apps/{APP_ID}',
            headers={'Authorization': TOKEN},
            timeout=10
        )
        print(f"Status check: {r.status_code} - {r.text[:200]}")
        
        # Deploy
        if os.path.exists('deploy_final.zip'):
            with open('deploy_final.zip', 'rb') as f:
                files = {'file': ('deploy_final.zip', f, 'application/zip')}
                r = requests.patch(
                    f'https://api.squarecloud.app/v2/apps/{APP_ID}',
                    headers={'Authorization': TOKEN},
                    files=files,
                    timeout=60
                )
            print(f"Deploy response: {r.status_code}")
            print(f"Response: {r.text[:500]}")
            return r.status_code == 200
        else:
            print("Arquivo deploy_final.zip não encontrado")
            return False
    except Exception as e:
        print(f"Erro na API: {e}")
        return False


def open_dashboard():
    """Abre o painel da SquareCloud"""
    print("Abrindo painel da SquareCloud...")
    webbrowser.open("https://squarecloud.app/dashboard")


def main():
    print_header()
    
    if not os.path.exists('squarecloud.app'):
        print("Arquivo squarecloud.app não encontrado!")
        sys.exit(1)
    
    print("Verificando status do Git...")
    
    has_changes = not check_git_status()
    
    if has_changes:
        print("Mudanças detectadas. Fazendo commit e push...")
        if not git_push():
            print("Falha no push. Continuando mesmo assim...")
    else:
        print("Nenhuma mudança detectada.")
    
    if not create_deploy_zip():
        print("Falha ao criar pacote de deploy")
        sys.exit(1)
    
    print()
    print("Tentando deploy via API...")
    deploy_via_api()
    
    print()
    print("PRÓXIMOS PASSOS:")
    print("1. Acesse https://squarecloud.app/dashboard")
    print("2. Encontre 'Selfbot RateLimit Python'")
    print("3. Clique em 'Redeploy'")
    print("4. Aguarde o build (2-5 minutos)")
    print()
    
    open_dashboard()
    
    print("Processo iniciado!")


if __name__ == "__main__":
    main()
