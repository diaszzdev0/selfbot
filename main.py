"""Entry point para SquareCloud.

A SquareCloud espera um arquivo MAIN=main.py (conforme squarecloud.app).

Este main.py só inicializa o Flask no arquivo app.py existente.
"""

from app import app  # noqa: F401

# Em vez de reaproveitar o código do Flask, apenas executa o app importado.
# SquareCloud chama: python3 main.py

if __name__ == "__main__":
    # host=0.0.0.0 para aceitar conexões externas na nuvem
    # debug=False para não depender de modo dev
    app.run(host="0.0.0.0", port=80, debug=False, use_reloader=False)

