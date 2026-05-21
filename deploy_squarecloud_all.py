#!/usr/bin/env python3
"""deploy_squarecloud_all.py

Orquestra o fluxo de Deploy na Square Cloud:
- Gera deploy_final.zip (via make_zip.py)
- Faz git add/commit/push (se houver mudanças)
- Faz commit forçado quando a app estiver running (via check_deploy.py)
- Gera um resumo local (deploy_squarecloud_all_status.txt)

Uso:
  python deploy_squarecloud_all.py

Observação:
- Este script não inclui novos tokens: usa os mesmos tokens hardcoded
  já existentes nos scripts do projeto.
- Se o commit forçado falhar, ele tenta restart+commit novamente (melhor esforço).
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime


def run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)


def ensure_project_root() -> None:
    required = ["make_zip.py", "check_deploy.py", "update_squarecloud.py"]
    missing = [p for p in required if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(
            "Arquivos obrigatórios não encontrados no diretório atual: " + ", ".join(missing)
        )


def git_add_commit_push() -> tuple[bool, str]:
    """Retorna (ok, message)."""
    # Add
    r_add = run(["git", "add", "."])
    if r_add.returncode != 0:
        return False, f"git add falhou: {r_add.stderr.strip()[:500]}"

    # Check changes
    r_status = run(["git", "status", "--porcelain"])
    if r_status.returncode != 0:
        return False, f"git status falhou: {r_status.stderr.strip()[:500]}"

    if not r_status.stdout.strip():
        return True, "Sem mudanças para commit/push."

    # Commit
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_msg = f"Atualizacao automacao deploy - {timestamp}"
    r_commit = run(["git", "commit", "-m", commit_msg])
    if r_commit.returncode != 0:
        # Às vezes falha por outro motivo (ex: configurações git). Retorna erro.
        return False, f"git commit falhou: {r_commit.stderr.strip()[:500]} || stdout={r_commit.stdout.strip()[:500]}"

    # Push
    r_push = run(["git", "push", "origin", "main"])
    if r_push.returncode != 0:
        return False, f"git push falhou: {r_push.stderr.strip()[:500]}"

    return True, "Commit e push realizados com sucesso."


def generate_zip() -> tuple[bool, str]:
    r = run([sys.executable, "make_zip.py"])
    if r.returncode != 0:
        return False, r.stderr.strip()[:800]
    return True, r.stdout.strip()[-800:]


def run_check_deploy() -> tuple[bool, str]:
    r = run([sys.executable, "check_deploy.py"])
    if r.returncode != 0:
        return False, r.stderr.strip()[:800]
    return True, (r.stdout or "").strip()[-800:]


def main() -> int:
    ensure_project_root()

    status_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy_squarecloud_all_status.txt")


    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("DEPLOY ORQUESTADOR - SQUARE CLOUD")
    lines.append("=" * 70)
    lines.append(f"Start: {datetime.now().isoformat(timespec='seconds')}")

    # 1) zip
    lines.append("\n[1/4] Gerando deploy_final.zip...")
    ok_zip, msg_zip = generate_zip()
    lines.append(f"ZIP_OK={ok_zip} | {msg_zip}")
    if not ok_zip:
        lines.append("Falha ao gerar zip. Abortando.")
        with open(status_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("Falha ao gerar deploy_final.zip. Veja:", status_path)
        return 1

    # 2) git
    lines.append("\n[2/4] Git add/commit/push...")
    ok_git, msg_git = git_add_commit_push()
    lines.append(f"GIT_OK={ok_git} | {msg_git}")
    if not ok_git:
        # Melhor esforço: ainda assim tentar deploy/commit forçado pelo zip.
        lines.append("Falha no git. Seguindo para deploy (melhor esforço).")

    # 3) commit if running
    lines.append("\n[3/4] Executando check_deploy.py (commit forçado se running)...")
    ok_check, msg_check = run_check_deploy()
    lines.append(f"CHECK_OK={ok_check} | {msg_check}")

    # 4) fallback: if check failed, try deploy_now.py (restart)
    if not ok_check:
        lines.append("\n[4/4] Fallback: restart via deploy_now.py...")
        r_restart = run([sys.executable, "deploy_now.py"])
        lines.append(f"RESTART_RC={r_restart.returncode} | {((r_restart.stdout or '') + (r_restart.stderr or ''))[:800]}")

        lines.append("\n[5/4] Após restart, tentando check_deploy.py novamente...")
        ok_check2, msg_check2 = run_check_deploy()
        lines.append(f"CHECK2_OK={ok_check2} | {msg_check2}")

    lines.append("\nFim:", datetime.now().isoformat(timespec='seconds'))

    with open(status_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("Resumo salvo em:", status_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

