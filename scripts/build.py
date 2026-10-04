#!/usr/bin/env python3
"""
Game of Life Wallpaper - instalação automática (gera o executável).

Cria o ambiente virtual, instala as dependências, roda os testes, compila
dist/GameOfLifeWallpaper.exe com o PyInstaller, testa o executável e, se
você quiser, liga o "Iniciar com o Windows".

Uso:  python scripts/build.py [--yes] [--skip-tests]
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / "venv"
DIST = ROOT / "dist"
EXE = DIST / "GameOfLifeWallpaper.exe"
SPEC = ROOT / "packaging" / "GameOfLifeWallpaper.spec"
PACKAGES = ["numpy>=2.0", "pyinstaller>=6.0"]


# -- saída colorida -------------------------------------------------------------
class C:
    BLUE, CYAN, GREEN, YELLOW, RED, BOLD, END = ("\033[94m", "\033[96m", "\033[92m", "\033[93m",
                                                 "\033[91m", "\033[1m", "\033[0m")


def _enable_ansi() -> None:
    # Piped or redirected output is cp1252 on Windows, which has no "✓";
    # never let a progress mark crash the installation.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass


def header(text: str) -> None:
    print(f"\n{C.BLUE}{'=' * 64}{C.END}\n{C.BOLD}{C.BLUE}{text:^64}{C.END}\n{C.BLUE}{'=' * 64}{C.END}\n")


def step(n: int, total: int, text: str) -> None:
    print(f"{C.CYAN}{C.BOLD}[{n}/{total}] {text}{C.END}")


def ok(text: str) -> None:
    print(f"{C.GREEN}  ✓ {text}{C.END}")


def warn(text: str) -> None:
    print(f"{C.YELLOW}  ! {text}{C.END}")


def fail(text: str) -> None:
    print(f"{C.RED}  ✗ {text}{C.END}")


def info(text: str) -> None:
    print(f"  · {text}")


def ask(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{C.YELLOW}{question} (s/n): {C.END}").strip().lower() in ("s", "sim", "y", "yes")
    except EOFError:
        return False


# -- passos -------------------------------------------------------------------------
def venv_python() -> Path:
    return VENV / "Scripts" / "python.exe"


def run(cmd: list, timeout: int = 900, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], cwd=ROOT, capture_output=capture, text=True,
                          timeout=timeout)


def check_python() -> bool:
    if sys.version_info < (3, 10):
        fail(f"é preciso Python 3.10 ou mais novo (este é {sys.version.split()[0]})")
        return False
    if os.name != "nt":
        fail("este programa é para Windows 10/11")
        return False
    ok(f"Python {sys.version.split()[0]}")
    return True


def make_venv() -> bool:
    if venv_python().exists():
        ok("ambiente virtual já existe")
        return True
    result = run([sys.executable, "-m", "venv", VENV])
    if result.returncode != 0:
        fail(f"não foi possível criar o ambiente virtual:\n{result.stderr}")
        return False
    ok("ambiente virtual criado")
    return True


def install() -> bool:
    run([venv_python(), "-m", "pip", "install", "--upgrade", "pip"], timeout=300)
    result = run([venv_python(), "-m", "pip", "install", "--upgrade", *PACKAGES], timeout=900)
    if result.returncode != 0:
        fail("falha ao instalar as dependências:")
        print(result.stdout[-2000:], result.stderr[-2000:])
        return False
    probe = run([venv_python(), "-c", "import numpy, tkinter, PyInstaller; print(numpy.__version__)"])
    if probe.returncode != 0:
        fail(f"as dependências não importam:\n{probe.stderr}")
        return False
    ok(f"numpy {probe.stdout.strip()}, PyInstaller e tkinter prontos")
    return True


def run_tests() -> bool:
    info("rodando os testes (simulação, formatos, renderizador na GPU)...")
    result = run([venv_python(), ROOT / "tests" / "test_wallpaper.py"], timeout=900)
    summary = [line for line in result.stdout.splitlines() if line.startswith(("FAIL", "all checks", "skip"))]
    for line in summary[-12:]:
        (ok if line.startswith("all") else warn)(line)
    if result.returncode != 0:
        fail("há testes falhando; o executável não será gerado")
        print(result.stderr[-2000:])
        return False
    return True


def stop_running_copy() -> None:
    """Fecha o wallpaper se ele estiver aberto, senão o .exe fica travado."""
    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = ctypes.c_void_p
        hwnd = user32.FindWindowW("GolWallpaperControl", None)
        if not hwnd:
            return
        info("o wallpaper está rodando; fechando-o para atualizar o executável...")
        user32.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0)          # WM_CLOSE
        for _ in range(50):
            time.sleep(0.1)
            if not user32.FindWindowW("GolWallpaperControl", None):
                ok("wallpaper fechado")
                time.sleep(0.5)
                return
        warn("o wallpaper não fechou a tempo; feche-o pelo ícone da bandeja")
    except Exception:
        pass


_MISSING = re.compile(r"missing module named\s+'?([\w.]+)'?\s+-\s+imported by")


def missing_critical_modules() -> list[str]:
    warn_file = ROOT / "build" / "GameOfLifeWallpaper" / "warn-GameOfLifeWallpaper.txt"
    if not warn_file.exists():
        return []
    critical = []
    for line in warn_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = _MISSING.search(line)
        if match:
            name = match.group(1)
            if name in ("numpy", "tkinter", "_tkinter") or name == "golwall" or name.startswith("golwall."):
                critical.append(line.strip())
    return critical


def build() -> bool:
    stop_running_copy()
    shutil.rmtree(ROOT / "build", ignore_errors=True)       # dist/ guarda sua config: fica
    info("compilando com o PyInstaller (alguns minutos)...")
    result = run([venv_python(), "-m", "PyInstaller", SPEC, "--clean", "--noconfirm"], timeout=1200)
    if result.returncode != 0 or not EXE.exists():
        fail("o PyInstaller falhou:")
        print(result.stderr[-4000:])
        return False
    missing = missing_critical_modules()
    if missing:
        fail("o PyInstaller não encontrou módulos essenciais:")
        for line in missing:
            print(f"      {line}")
        return False
    ok(f"executável gerado: {EXE} ({EXE.stat().st_size / 1_048_576:.1f} MB)")
    return True


def prepare_dist() -> None:
    config = DIST / "config.json"
    if not config.exists() and (ROOT / "config.json").exists():
        shutil.copy2(ROOT / "config.json", config)
        ok("config.json copiado para dist/ (edite lá; não precisa recompilar)")
    state = DIST / "golwall.state.json"
    if state.exists():
        try:
            data = json.loads(state.read_text("utf-8"))
            if "presenter" in data:                  # chave da versão 1, que forçava a camada errada
                data.pop("presenter", None)
                data.pop("build", None)
                state.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
                ok("estado antigo da versão 1 limpo")
        except (OSError, ValueError):
            state.unlink(missing_ok=True)


def test_exe() -> bool:
    info("testando o executável numa janela por 5 segundos...")
    log = DIST / "golwall.install-test.log"
    log.unlink(missing_ok=True)
    try:
        result = run([EXE, "--windowed", "--seconds", "5", "--allow-multiple", "--no-tray",
                      "--log-file", log], timeout=90)
    except subprocess.TimeoutExpired:
        fail("o executável não terminou sozinho")
        return False
    text = log.read_text("utf-8", errors="replace") if log.exists() else ""
    errors = [line for line in text.splitlines() if " ERROR " in line or " CRITICAL " in line]
    if result.returncode != 0 or errors or "stopped" not in text:
        fail(f"o executável terminou com problemas (código {result.returncode})")
        for line in errors[:10] or text.splitlines()[-10:]:
            print(f"      {line}")
        return False
    log.unlink(missing_ok=True)
    ok("o executável abriu, renderizou e fechou sem erros")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="responder sim a todas as perguntas")
    parser.add_argument("--skip-tests", action="store_true", help="não rodar os testes antes de compilar")
    args = parser.parse_args()
    _enable_ansi()
    os.chdir(ROOT)
    header("Game of Life Wallpaper - instalação")
    total = 6
    step(1, total, "Verificando o Python")
    if not check_python():
        return 1
    step(2, total, "Ambiente virtual")
    if not make_venv():
        return 1
    step(3, total, "Dependências (numpy, PyInstaller)")
    if not install():
        return 1
    step(4, total, "Testes")
    if args.skip_tests:
        warn("testes pulados")
    elif not run_tests():
        return 1
    step(5, total, "Compilando o executável")
    if not build():
        return 1
    prepare_dist()
    step(6, total, "Testando o executável")
    if not test_exe():
        return 1

    header("Pronto!")
    info(f"Executável: {EXE}")
    info("Ao rodar, ele fica atrás dos ícones da área de trabalho, com um ícone na bandeja.")
    info("Ctrl+Alt+Shift+G (ou clique no ícone da bandeja) abre o editor para desenhar.")
    print()
    if ask("Iniciar junto com o Windows?", args.yes):
        result = run([EXE, "--install-startup"])
        (ok if result.returncode == 0 else warn)("inicialização automática " +
                                                 ("ativada" if result.returncode == 0 else "não pôde ser ativada"))
    if ask("Abrir o wallpaper agora?", args.yes):
        subprocess.Popen([str(EXE)], cwd=str(DIST), close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        ok("wallpaper iniciado")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Instalação cancelada.{C.END}")
        sys.exit(1)
