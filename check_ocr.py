import requests

OCR_API_KEY = "helloworld"

print("=" * 60)
print("VERIFICANDO USO DA API OCR.SPACE")
print("=" * 60)
print()

# Testa a API
url = "https://api.ocr.space/parse/image"
data = {
    "url": "https://via.placeholder.com/150",
    "apikey": OCR_API_KEY,
    "language": "por"
}

try:
    response = requests.post(url, data=data, timeout=10)
    result = response.json()
    
    if result.get("IsErroredOnProcessing"):
        error_msg = result.get("ErrorMessage", [])
        if error_msg:
            print(f"Status: {error_msg[0]}")
            
            # Verifica se atingiu o limite
            if "limit" in str(error_msg).lower():
                print()
                print("LIMITE ATINGIDO!")
                print()
                print("Opcoes:")
                print("1. Aguardar reset (meia-noite UTC)")
                print("2. Criar conta gratuita em: https://ocr.space/ocrapi")
                print("3. Upgrade para plano pago")
            else:
                print()
                print("API funcionando normalmente")
        else:
            print("API funcionando normalmente")
    else:
        print("API funcionando normalmente")
        print()
        print("Limites da chave 'helloworld':")
        print("  - 500 requisicoes por dia")
        print("  - 25.000 requisicoes por mes")
        print("  - 1 requisicao por segundo")
        print()
        print("Para aumentar limites:")
        print("  1. Crie conta gratuita: https://ocr.space/ocrapi")
        print("  2. Obtenha sua chave pessoal")
        print("  3. Atualize OCR_API_KEY no .env")
        
except Exception as e:
    print(f"Erro ao verificar: {e}")

print()
print("=" * 60)
