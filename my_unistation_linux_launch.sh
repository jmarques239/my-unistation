#!/usr/bin/env bash
# =============================================================================
# My UniStation — Launcher para Linux
#
# Deteta Python 3.8+, instala automaticamente se necessário (via apt/dnf/
# pacman/zypper) e arranca o servidor. O URL para acesso é apresentado em
# destaque no terminal — o utilizador abre-o no browser que preferir.
#
# Uso:
#   ./my_unistation_linux_launch.sh
#   bash my_unistation_linux_launch.sh
# =============================================================================

set -u

# -----------------------------------------------------------------------------
# Mover para o diretório do script
# -----------------------------------------------------------------------------
cd "$(dirname "$0")" || {
    echo "[ERRO] Nao foi possivel entrar no diretorio do script."
    exit 1
}

echo "========================================"
echo "  My UniStation - Arranque"
echo "========================================"
echo

# -----------------------------------------------------------------------------
# Utilitário: verifica se um comando é Python 3.8+
# -----------------------------------------------------------------------------
is_valid_python() {
    local cmd="$1"
    command -v "$cmd" >/dev/null 2>&1 || return 1
    "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null
}

# -----------------------------------------------------------------------------
# STEP 1: Deteção inicial de Python válido
# -----------------------------------------------------------------------------
PYTHON_CMD=""
for cmd in python3 python; do
    if is_valid_python "$cmd"; then
        PYTHON_CMD="$cmd"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "[NAO ENCONTRADO] Python 3.8+ nao esta instalado."
    echo
    read -r -p "Instalar Python 3 automaticamente? [S/N]: " resp

    case "$resp" in
        [SsYy]*)
            echo

            if command -v apt-get >/dev/null 2>&1; then
                echo "A instalar via apt (Debian / Ubuntu / Mint)..."
                sudo apt-get update && sudo apt-get install -y python3

            elif command -v dnf >/dev/null 2>&1; then
                echo "A instalar via dnf (Fedora / RHEL / Rocky / AlmaLinux)..."
                sudo dnf install -y python3

            elif command -v pacman >/dev/null 2>&1; then
                echo "A instalar via pacman (Arch / Manjaro / EndeavourOS)..."
                sudo pacman -S --noconfirm python

            elif command -v zypper >/dev/null 2>&1; then
                echo "A instalar via zypper (openSUSE)..."
                sudo zypper install -y python3

            else
                echo "[ERRO] Gestor de pacotes nao reconhecido."
                echo "Instale manualmente Python 3.8+ e execute novamente."
                read -r -p "Prima Enter para sair..." _
                exit 1
            fi

            echo

            PYTHON_CMD=""
            for cmd in python3 python; do
                if is_valid_python "$cmd"; then
                    PYTHON_CMD="$cmd"
                    break
                fi
            done

            if [ -z "$PYTHON_CMD" ]; then
                echo "[ERRO] Instalacao concluida mas Python 3.8+ continua indisponivel."
                echo "Feche este terminal, abra um novo e execute novamente o script."
                read -r -p "Prima Enter para sair..." _
                exit 1
            fi
            ;;

        *)
            echo "[CANCELADO] Instalacao cancelada pelo utilizador."
            read -r -p "Prima Enter para sair..." _
            exit 1
            ;;
    esac
fi

echo "[OK] Python encontrado: $($PYTHON_CMD --version 2>&1)"
echo

# -----------------------------------------------------------------------------
# STEP 2: Arrancar servidor My UniStation
# -----------------------------------------------------------------------------
echo "A arrancar My UniStation..."
echo
echo "  ┌────────────────────────────────────────────────────┐"
echo "  │                                                    │"
echo "  │   Abre no teu browser:                             │"
echo "  │                                                    │"
echo "  │       http://localhost:8080                        │"
echo "  │                                                    │"
echo "  │   Para parar a aplicacao: Ctrl+C nesta janela      │"
echo "  │                                                    │"
echo "  └────────────────────────────────────────────────────┘"
echo
echo "Se estiveres em WSL, abre o URL acima no browser do Windows."
echo

"$PYTHON_CMD" app.py

echo
echo "My UniStation terminou."
read -r -p "Prima Enter para fechar..." _