@echo off
setlocal EnableDelayedExpansion

echo ========================================
echo   My UniStation - Arranque
echo ========================================
echo:

REM ========================================
REM STEP 1: Verificar Python
REM ========================================
py --version >nul 2>&1
if not errorlevel 1 goto python_ok

echo [NAO ENCONTRADO] Python nao esta instalado.
echo:
set /p RESP=Instalar Python 3.12 automaticamente? [S/N]: 
if /i not "%RESP%"=="S" (
    echo [CANCELADO] Instalacao cancelada.
    timeout /t 5 >nul
    exit /b 1
)

echo:
echo A instalar Python 3.12 via winget...
echo:
winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements

if errorlevel 1 (
    echo:
    echo [ERRO] Falha na instalacao. Instale manualmente em:
    echo https://www.python.org/downloads/
    echo IMPORTANTE: Marque "Add Python to PATH" durante a instalacao.
    echo:
    echo Esta janela fecha em 15 segundos...
    timeout /t 15 >nul
    exit /b 1
)

echo:
echo [OK] Python instalado.

REM Espera 3 segundos para o registo do py launcher estabilizar
timeout /t 3 /nobreak >nul

REM Re-verifica se o py ja esta disponivel nesta sessao
py --version >nul 2>&1
if not errorlevel 1 (
    echo [OK] Python disponivel. A continuar...
    echo:
    goto python_ok
)

REM Caso raro: py ainda nao visivel nesta sessao
echo:
echo [AVISO] O Python foi instalado mas ainda nao esta ativo nesta sessao.
echo Feche esta janela e clique novamente em my_unistation_win_launch.bat.
echo:
echo Esta janela fecha em 15 segundos...
timeout /t 15 >nul
exit /b 0

:python_ok
for /f "tokens=2" %%i in ('py --version 2^>nul') do set PYTHON_VER=%%i
echo [OK] Python %PYTHON_VER% encontrado.
echo:

REM ========================================
REM STEP 2: Arrancar My UniStation
REM ========================================
echo A arrancar My UniStation...
echo:
echo O browser vai abrir automaticamente em http://localhost:8080
echo Para parar a aplicacao, feche esta janela ou prima Ctrl+C.
echo:

REM Lanca o browser em paralelo com 2s de delay, para dar tempo ao servidor
start "" cmd /c "timeout /t 2 /nobreak >nul & start "" http://localhost:8080"

REM Arranca o servidor em foreground (esta janela fica bloqueada ate Ctrl+C)
py app.py

echo:
echo My UniStation terminou.
echo Esta janela fecha em 5 segundos...
timeout /t 5 >nul