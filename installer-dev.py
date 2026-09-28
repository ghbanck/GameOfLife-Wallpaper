#!/usr/bin/env python3
"""
Game of Life Wallpaper - ambiente de desenvolvimento (sem compilar).

Cria o ambiente virtual, instala o numpy, roda os testes e mostra como rodar
o wallpaper direto dos fontes.

Uso:  python installer-dev.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
VENV_PYW = ROOT / "venv" / "Scripts" / "pythonw.exe"


def run(cmd, **kw) -> int:
    return subprocess.run([str(c) for c in cmd], cwd=ROOT, **kw).returncode


def main() -> int:
    if sys.version_info < (3, 10):
        print(f"É preciso Python 3.10 ou mais novo (este é {sys.version.split()[0]}).")
        return 1
    if not VENV_PY.exists():
        print("· criando o ambiente virtual...")
        if run([sys.executable, "-m", "venv", ROOT / "venv"]) != 0:
            return 1
    print("· instalando o numpy...")
    if run([VENV_PY, "-m", "pip", "install", "--upgrade", "--quiet", "pip", "numpy>=2.0"]) != 0:
        return 1
    print("· rodando os testes...")
    tests = run([VENV_PY, "tests.py"])
    print()
    print("Pronto." if tests == 0 else "Atenção: há testes falhando (veja acima).")
    print()
    print("Rodar como wallpaper (sem janela de console):")
    print(f'  "{VENV_PYW}" main.py')
    print("Rodar numa janela, com o log no terminal:")
    print(f'  "{VENV_PY}" main.py --windowed')
    print("Diagnóstico do que o wallpaper faria nesta área de trabalho:")
    print(f'  "{VENV_PY}" main.py --diagnose')
    return tests


if __name__ == "__main__":
    sys.exit(main())
