@echo off
chcp 65001 >nul
title Atualizador SquareCloud - Selfbot Manager

echo.
echo ========================================
echo 🚀 ATUALIZADOR SQUARECLOUD
echo ========================================
echo.

cd /d "%~dp0"

echo 🔍 Verificando Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python não encontrado!
    echo Instale Python 3.7+ e tente novamente.
    pause
    exit /b 1
)

echo ✅ Python encontrado!
echo.

echo 📤 Iniciando processo de atualização...
python update_squarecloud.py

echo.
echo 📋 INSTRUÇÕES FINAIS:
echo 1. No painel da SquareCloud que abriu:
echo 2. Encontre "Selfbot Manager"
echo 3. Clique em "Redeploy"
echo 4. Aguarde o build completar
echo 5. Verifique os logs
echo.

pause