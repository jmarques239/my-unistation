# 🚀 Getting Started — My UniStation

Guia de instalação e arranque. 
Cobre desde o método mais simples (Python direto) até aos launchers automáticos ( windows e linux ) para utilizadores sem conhecimentos técnicos.

[← Voltar ao README principal](../README.md)

---

## 📦 Requisitos

- **Python 3.8+** — o único requisito. Tudo o resto é biblioteca padrão.
- Um browser moderno (Chrome, Firefox, Edge, Safari).

Não precisas de:
- Base de dados
- `pip install`
- Ambiente virtual (`venv`)
- Docker
- Node.js
- Internet (depois de instalado)

---

## 🐍 Método 1 — Python direto

O caminho mais simples. Funciona em **Windows, Linux, macOS e WSL**.

### 1. Clonar o repositório

```bash
git clone https://github.com/jmarques239/my-unistation.git
cd my-unistation
```

Se não tens `git` instalado, podes simplesmente descarregar o ZIP do repositório e extrair.

### 2. Arrancar o servidor

```bash
python3 app.py      # Linux / macOS / WSL
python app.py       # Windows
```

### 3. Verificar o output

```text
✅ My UniStation ativo
🌐 Aceda a: http://0.0.0.0:8080
⚙️  Config bootstrap: /caminho/para/config.json
💾 Ficheiro de dados: /caminho/para/data.json
🛡️  Backups automáticos com retenção de 5 ficheiros
```

### 4. Abrir no browser

Abre o URL `http://localhost:8080` no browser que preferires.

### 5. Parar o servidor

Prime `Ctrl+C` na janela onde está a correr.

---

## 🖱️ Método 2 — Launchers automáticos

Para utilizadores que preferem **não lidar com a linha de comandos**. Os launchers fazem tudo: detetam o Python, instalam-no se necessário, e arrancam o servidor.

### 🪟 Windows — `my_unistation_win_launch.bat`

**Duplo clique** no ficheiro.

O script:

1. Verifica se o **Python está instalado** (procura o comando `py`)
2. Se não estiver, pergunta `Instalar Python 3.12 automaticamente? [S/N]`
3. Se aceitares, **instala via `winget`** (incluído no Windows 10/11)
4. Aguarda 3 segundos para o Python ficar disponível na sessão
5. Re-verifica se o `py` já responde — se sim, **continua automaticamente**
6. **Abre o browser** após 2 segundos, em `http://localhost:8080`
7. Arranca o servidor em foreground (a janela fica ocupada até `Ctrl+C`)

**Resultado esperado:** do duplo clique ao dashboard em menos de 10 segundos.

> 💡 **Python já instalado?** Os passos 2–3 são saltados. O arranque é praticamente instantâneo.

> ⚠️ **Fallback:** no caso raro do `py` não ficar disponível na sessão atual após a instalação, o script pede para fechares a janela e clicares novamente no `.bat`.

### 🐧 Linux / WSL — `my_unistation_linux_launch.sh`

**Primeira utilização:** marca o script como executável.

```bash
chmod +x my_unistation_linux_launch.sh
```

**Depois, corre-o:**

```bash
./my_unistation_linux_launch.sh
```

O script:

1. Deteta **Python 3.8+** (procura primeiro `python3`, depois `python`)
2. Se não encontrar, pergunta se queres instalar e usa o **gestor de pacotes da tua distro**:
   - `apt` — Debian / Ubuntu / Mint
   - `dnf` — Fedora / RHEL / Rocky / AlmaLinux
   - `pacman` — Arch / Manjaro / EndeavourOS
   - `zypper` — openSUSE
3. **Re-verifica a versão** após instalação (evita aceitar um Python inválido)
4. Mostra o URL **em destaque** no terminal
5. Arranca o servidor em foreground — `Ctrl+C` para parar

> **Porque não abre o browser automaticamente?** O Linux pode correr em ambientes sem interface gráfica (WSL, servidores headless, VPS). Tentar abrir o browser nesses casos adicionaria ruído desnecessário. O URL é apresentado de forma clara — copia e cola onde quiseres.

### 🍎 macOS

Um launcher `.command` está planeado. Por agora, usa o **Método 1** (Python direto).

> **Nota:** o macOS moderno (12.3+) **não traz Python por omissão**. Se `python3 --version` falhar, instala via Homebrew:
> ```bash
> brew install python3
> ```

---

## 🎯 Próximos passos

- **Documentação técnica:** [`ARCHITECTURE.md`](ARCHITECTURE.md) [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) [`FILE_LOCATION.md`](FILE_LOCATION.md)
- **Website do projeto:** [https://jmarques239.github.io/my-unistation/](https://jmarques239.github.io/my-unistation/)
- **Reportar bugs:** [issues no GitHub](https://github.com/jmarques239/my-unistation/issues)