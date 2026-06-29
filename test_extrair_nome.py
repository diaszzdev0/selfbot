# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
Teste da função _extrair_nome melhorada
"""

import re
import unicodedata

def _extrair_nome(conteudo: str):
    """Extrai nome de comandos de pagamento com múltiplas variações"""
    c = conteudo.strip()
    cl = c.lower()
    
    # Padrões mais flexíveis para detectar comandos de pagamento
    prefixos = [
        "pg ", "pago ", "paguei ", "pagou ", "pag ", "p ",
        "pg: ", "pago: ", "paguei: ", "pagou: ",
        "pg- ", "pago- ", "paguei- ", "pagou- ",
        "pg.", "pago.", "paguei.", "pagou.",
        "verificar ", "check ", "buscar ", "consultar "
    ]
    
    sufixos = [
        " pg", " pago", " paguei", " pagou", " pag",
        " :pg", " :pago", " :paguei", " :pagou",
        " -pg", " -pago", " -paguei", " -pagou"
    ]
    
    # Verifica prefixos
    for p in prefixos:
        if cl.startswith(p):
            nome = c[len(p):].strip()
            # Remove caracteres especiais do início
            nome = re.sub(r'^[:\-.,;!?\s]+', '', nome)
            if nome and len(nome) >= 2:
                return nome
    
    # Verifica sufixos
    for s in sufixos:
        if cl.endswith(s):
            nome = c[:-len(s)].strip()
            # Remove caracteres especiais do final
            nome = re.sub(r'[:\-.,;!?\s]+$', '', nome)
            if nome and len(nome) >= 2:
                return nome
    
    # Verifica padrões no meio da mensagem (ex: "verificar pagamento de João Silva")
    patterns = [
        r'(?:verificar|check|buscar|consultar)\s+(?:pagamento\s+(?:de|do|da)?\s*)?([a-záàâãéêíóôõúç\s]{2,})',
        r'(?:pg|pago|paguei|pagou)\s*[:\-.,;]?\s*([a-záàâãéêíóôõúç\s]{2,})',
        r'([a-záàâãéêíóôõúç\s]{2,})\s+(?:pg|pago|paguei|pagou)\s*$'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, cl, re.IGNORECASE)
        if match:
            nome = match.group(1).strip()
            # Remove palavras comuns que não são nomes
            palavras_ignorar = ['pagamento', 'de', 'do', 'da', 'para', 'por', 'em', 'no', 'na']
            palavras = nome.split()
            palavras_filtradas = [p for p in palavras if p.lower() not in palavras_ignorar]
            
            if len(palavras_filtradas) >= 1:
                nome_final = ' '.join(palavras_filtradas)
                if len(nome_final) >= 2:
                    return nome_final
    
    return None

def testar_funcao():
    """Testa a função com vários exemplos"""
    
    testes = [
        # Formatos tradicionais
        "pg João Silva",
        "pago Maria Santos",
        "paguei Pedro Costa",
        
        # Com pontuação
        "pg: João Silva",
        "pago- Maria Santos",
        "pg. Pedro Costa",
        
        # Sufixos
        "João Silva pg",
        "Maria Santos pago",
        "Pedro Costa paguei",
        
        # Variações
        "p João Silva",
        "pag Maria Santos",
        "pagou Pedro Costa",
        
        # Comandos mais naturais
        "verificar João Silva",
        "check Maria Santos",
        "buscar Pedro Costa",
        "consultar Ana Oliveira",
        
        # Frases completas
        "verificar pagamento de João Silva",
        "check pagamento do Pedro",
        "buscar pagamento da Maria",
        
        # Casos que NÃO devem funcionar
        "olá pessoal",
        "como vocês estão?",
        "pg",  # muito curto
        "p",   # muito curto
        
        # Casos edge
        "pg   João Silva   ",  # espaços extras
        "PG JOÃO SILVA",       # maiúsculas
        "Pg João Silva",       # misto
        "pg:João Silva",       # sem espaço após :
        "João Silva:pg",       # com : no sufixo
    ]
    
    print("TESTANDO FUNCAO _extrair_nome MELHORADA")
    print("=" * 50)
    
    sucessos = 0
    total = 0
    
    for teste in testes:
        resultado = _extrair_nome(teste)
        total += 1
        
        if resultado:
            print(f"OK '{teste}' -> '{resultado}'")
            sucessos += 1
        else:
            print(f"FAIL '{teste}' -> None")
    
    print("=" * 50)
    print(f"Resultados: {sucessos}/{total} testes com resultado")
    print(f"Taxa de detecção: {(sucessos/total)*100:.1f}%")
    
    # Testes específicos esperados
    print("\nTESTES ESPECÍFICOS:")
    
    casos_esperados = [
        ("pg João Silva", "João Silva"),
        ("pago Maria", "Maria"),
        ("João pg", "João"),
        ("verificar Pedro Costa", "Pedro Costa"),
        ("check pagamento de Ana", "Ana"),
        ("pg: Carlos Santos", "Carlos Santos"),
        ("olá pessoal", None),
        ("pg", None),
    ]
    
    acertos = 0
    for entrada, esperado in casos_esperados:
        resultado = _extrair_nome(entrada)
        if resultado == esperado:
            print(f"OK '{entrada}' -> '{resultado}' (esperado: '{esperado}')")
            acertos += 1
        else:
            print(f"FAIL '{entrada}' -> '{resultado}' (esperado: '{esperado}')")
    
    print(f"\nPrecisão: {acertos}/{len(casos_esperados)} = {(acertos/len(casos_esperados))*100:.1f}%")

if __name__ == "__main__":
    testar_funcao()