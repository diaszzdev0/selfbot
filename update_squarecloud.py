#!/usr/bin/env python3
"""
Script para atualizar aplicação na SquareCloud
"""

import os
import sys
import subprocess
import webbrowser
from datetime import datetime

def print_header():
    print("=" * 60)
    print("🚀 ATUALIZADOR SQUARECLOUD - SELFBOT MANAGER")
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
        print("📤 Fazendo push para GitHub...")
        
        # Add all changes
        subprocess.run(['git', 'add', '.'], check=True, cwd='.')
        
        # Commit with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_msg = f"Atualização automática - {timestamp}"
        subprocess.run(['git', 'commit', '-m', commit_msg], check=True, cwd='.')
        
        # Push to main
        subprocess.run(['git', 'push', 'origin', 'main'], check=True, cwd='.')
        
        print("✅ Push realizado com sucesso!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Erro no git: {e}")
        return False

def open_squarecloud():
    """Abre o painel da SquareCloud"""
    print("🌐 Abrindo painel da SquareCloud...")
    webbrowser.open("https://squarecloud.app/dashboard")

def main():
    print_header()
    
    # Verifica se está no diretório correto
    if not os.path.exists('squarecloud.app'):
        print("❌ Arquivo squarecloud.app não encontrado!")
        print("Execute este script na pasta raiz do projeto.")
        sys.exit(1)
    
    print("🔍 Verificando status do Git...")
    
    # Verifica se há mudanças
    if check_git_status():
        print("ℹ️  Nenhuma mudança detectada no Git.")
        choice = input("Deseja abrir o painel da SquareCloud mesmo assim? (s/N): ")
        if choice.lower() != 's':
            print("Operação cancelada.")
            sys.exit(0)
    else:
        print("📝 Mudanças detectadas. Fazendo commit e push...")
        if not git_push():
            print("❌ Falha no push. Verifique manualmente.")
            sys.exit(1)
    
    print()
    print("🎯 PRÓXIMOS PASSOS NA SQUARECLOUD:")
    print("1. Encontre sua aplicação 'Selfbot Manager'")
    print("2. Clique no botão 'Redeploy'")
    print("3. Aguarde o build (2-5 minutos)")
    print("4. Verifique os logs para confirmar funcionamento")
    print()
    
    # Abre o painel
    open_squarecloud()
    
    print("✅ Processo iniciado! Painel da SquareCloud aberto.")
    print("📋 Siga os passos acima para completar a atualização.")

if __name__ == "__main__":
    main()