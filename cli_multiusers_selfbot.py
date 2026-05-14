import os
import time
import argparse
import multiprocessing
from typing import Dict, Any

from dotenv import load_dotenv

from bot_logic import run_selfbot, parar_selfbot


def build_config_dict_from_env() -> Dict[str, Any]:
    """Monta o mesmo dict que bot_logic.run_selfbot espera.

    Campos principais esperados em run_selfbot(config, user_id):
      - discord_token
      - server_id
      - categoria_id
      - email_user
      - email_pass
      - imap_server
      - mensagem_entrada
      - imagem_entrada
      - prefixo_sala
      - rate_limit_categorias (JSON string)
      - max_threads
      - modo_sala_id
    """

    def getenv(name: str, default: str = "") -> str:
        v = os.getenv(name, default)
        return v.strip() if isinstance(v, str) else v

    discord_token = getenv("DISCORD_TOKEN")
    server_id = int(getenv("SERVER_ID", "0") or "0")
    categoria_id = int(getenv("CATEGORIA_ID", "0") or "0")

    # IMAP / Email
    email_user = getenv("EMAIL_USER")
    email_pass = getenv("EMAIL_PASS")
    imap_server = getenv("IMAP_SERVER", "imap.gmail.com")

    # Mensagem
    mensagem_entrada = getenv(
        "MENSAGEM_ENTRADA",
        "Olá! Estou aqui para ajudar. Use `pg Nome Sobrenome` para verificar seu pagamento."
    )
    imagem_entrada = getenv("IMAGEM_ENTRADA", "")
    imagem_entrada = imagem_entrada if imagem_entrada else None

    # Sala / Controle
    prefixo_sala = getenv("PREFIXO_SALA", "")
    prefixo_sala = prefixo_sala if prefixo_sala else None

    modo_sala_id = getenv("MODO_SALA_ID", "")
    modo_sala_id = modo_sala_id if modo_sala_id else None

    # Rate limit categorias: esperado no bot_logic como JSON string (ex: ["gel normal", "vip"])
    rate_limit_categorias = getenv("RATE_LIMIT_CATEGORIAS", "")

    try:
        max_threads = int(getenv("MAX_THREADS", "3") or "3")
    except ValueError:
        max_threads = 3

    return {
        "discord_token": discord_token,
        "server_id": str(server_id),
        "categoria_id": str(categoria_id),
        "email_user": email_user,
        "email_pass": email_pass,
        "imap_server": imap_server,
        "mensagem_entrada": mensagem_entrada,
        "imagem_entrada": imagem_entrada,
        "prefixo_sala": prefixo_sala,
        "rate_limit_categorias": rate_limit_categorias,
        "max_threads": max_threads,
        "modo_sala_id": modo_sala_id,
    }


def parse_multi_users() -> list[dict[str, Any]]:
    """Suporte multi-usuários via env.

    Espera um formato simples e limpo:
      SELF_USERS='[
        {"user_id":1, "DISCORD_TOKEN":"...", "SERVER_ID":"...", ...},
        {"user_id":2, "DISCORD_TOKEN":"...", ...}
      ]'

    Se SELF_USERS não existir, assume single user usando variáveis padrão do .env.
    """
    raw = os.getenv("SELF_USERS", "")
    if raw.strip():
        import json
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("SELF_USERS precisa ser uma lista JSON")
        return data

    # single user
    user_id = os.getenv("SELF_USER_ID")
    if not user_id:
        raise SystemExit("Defina SELF_USERS (lista) ou SELF_USER_ID para single user")
    return [
        {
            "user_id": int(user_id),
        }
    ]


def start_processes(users: list[dict[str, Any]], check_interval: int = 10):
    """Inicia/respawna selfbots por user_id.

    Observação: sem painel, aqui fazemos apenas o ciclo de vida (start/respawn).
    Para stop manual, você pode matar o processo do script.
    """
    processes: dict[int, multiprocessing.Process] = {}

    def launch_for(u: dict[str, Any]):
        user_id = int(u["user_id"])

        # Se o usuário trouxer config inline, sobrescreve env durante a execução do filho.
        # Estratégia: o próprio filho lê env; então vamos injetar via variáveis antes de chamar run_selfbot.
        # Como a instância roda em processo separado, ajustamos no subprocesso.

        cfg = build_config_dict_from_env()
        # aplica overrides
        # campos permitidos no JSON de usuário
        overrides_map = {
            "DISCORD_TOKEN": "discord_token",
            "SERVER_ID": "server_id",
            "CATEGORIA_ID": "categoria_id",
            "EMAIL_USER": "email_user",
            "EMAIL_PASS": "email_pass",
            "IMAP_SERVER": "imap_server",
            "MENSAGEM_ENTRADA": "mensagem_entrada",
            "IMAGEM_ENTRADA": "imagem_entrada",
            "PREFIXO_SALA": "prefixo_sala",
            "RATE_LIMIT_CATEGORIAS": "rate_limit_categorias",
            "MAX_THREADS": "max_threads",
            "MODO_SALA_ID": "modo_sala_id",
        }
        # Overrides vindos do user dict (por exemplo, {"DISCORD_TOKEN":"..."})
        for env_key, cfg_key in overrides_map.items():
            if env_key in u and u[env_key] is not None:
                if cfg_key in ("server_id", "categoria_id"):
                    cfg[cfg_key] = str(u[env_key])
                elif cfg_key == "max_threads":
                    cfg[cfg_key] = int(u[env_key])
                else:
                    cfg[cfg_key] = u[env_key]

        p = multiprocessing.Process(
            target=run_selfbot,
            args=(cfg, user_id),
            daemon=True,
            name=f"selfbot_user_{user_id}",
        )
        p.start()
        processes[user_id] = p

    # initial launch
    for u in users:
        uid = int(u["user_id"])
        launch_for(u)

    while True:
        time.sleep(check_interval)
        # respawn mortos
        for u in users:
            uid = int(u["user_id"])
            p = processes.get(uid)
            if not p or not p.is_alive():
                # evita respawn frenético: pequena pausa
                time.sleep(2)
                launch_for(u)


def stop_all():
    """Sinalização simples: como cada selfbot roda em subprocessos daemon do próprio script,
    não existe controle externo aqui sem painel/IPC.

    Recomendação: pare o script (Ctrl+C) e remova processos.
    """
    raise SystemExit("Modo stop_all não implementado sem IPC/integração com painel. Pare o script.)")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Selfbot CLI multi-usuários (sem painel web)")
    parser.add_argument("cmd", choices=["start", "stop"], help="start | stop")
    parser.add_argument("--interval", type=int, default=10, help="intervalo para checar processos")
    args = parser.parse_args()

    users = parse_multi_users()

    if args.cmd == "start":
        start_processes(users, check_interval=args.interval)
    else:
        stop_all()


if __name__ == "__main__":
    main()

