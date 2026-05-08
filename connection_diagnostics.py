import asyncio
import aiohttp
import socket
import time
from datetime import datetime

async def test_discord_connectivity():
    """Testa conectividade com servidores do Discord"""
    results = {}
    
    # Endpoints do Discord para testar
    endpoints = {
        "Discord API": "https://discord.com/api/v10/gateway",
        "Discord CDN": "https://cdn.discordapp.com"
    }
    
    print("Testando conectividade com Discord...")
    
    for name, url in endpoints.items():
        try:
            start_time = time.time()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as response:
                    latency = (time.time() - start_time) * 1000
                    results[name] = {"status": f"OK {response.status}", "latency": f"{latency:.0f}ms"}
        except Exception as e:
            results[name] = {"status": f"ERRO {str(e)[:50]}", "latency": "N/A"}
    
    return results

def test_dns_resolution():
    """Testa resolução DNS"""
    domains = ["discord.com", "gateway.discord.gg", "cdn.discordapp.com"]
    results = {}
    
    print("Testando resolucao DNS...")
    
    for domain in domains:
        try:
            start_time = time.time()
            ip = socket.gethostbyname(domain)
            latency = (time.time() - start_time) * 1000
            results[domain] = {"ip": ip, "latency": f"{latency:.0f}ms", "status": "OK"}
        except Exception as e:
            results[domain] = {"ip": "N/A", "latency": "N/A", "status": f"ERRO {e}"}
    
    return results

def check_system_resources():
    """Verifica recursos do sistema"""
    import psutil
    
    return {
        "CPU": f"{psutil.cpu_percent()}%",
        "RAM": f"{psutil.virtual_memory().percent}%",
        "Conexões de rede": len(psutil.net_connections()),
        "Processos": len(psutil.pids())
    }

async def run_full_diagnostics():
    """Executa diagnóstico completo"""
    print("Iniciando diagnostico completo...")
    print("=" * 50)
    
    # Teste de conectividade
    connectivity = await test_discord_connectivity()
    print("\nCONECTIVIDADE DISCORD:")
    for service, data in connectivity.items():
        print(f"  {service}: {data['status']} ({data['latency']})")
    
    # Teste DNS
    dns = test_dns_resolution()
    print("\nRESOLUCAO DNS:")
    for domain, data in dns.items():
        print(f"  {domain}: {data['status']} -> {data['ip']} ({data['latency']})")
    
    # Recursos do sistema
    try:
        resources = check_system_resources()
        print("\nRECURSOS DO SISTEMA:")
        for resource, value in resources.items():
            print(f"  {resource}: {value}")
    except ImportError:
        print("\nRECURSOS: psutil nao instalado")
    
    print("\n" + "=" * 50)
    print(f"Diagnostico concluido em {datetime.now().strftime('%H:%M:%S')}")

if __name__ == "__main__":
    asyncio.run(run_full_diagnostics())