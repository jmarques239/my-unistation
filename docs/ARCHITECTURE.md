# 🏗️ Architecture — My UniStation

Documentação técnica sobre a arquitetura, API HTTP, garantias de robustez e estrutura do projeto.

[← Voltar ao README principal](../README.md)

---

## 🎯 Princípios de design

1. **Zero dependências externas** — apenas biblioteca padrão do Python
2. **Local-first** — os dados nunca saem da máquina do utilizador
3. **Escrita atómica** — nunca corrompe dados, mesmo em falhas abruptas
4. **Single-writer** — um único servidor escreve no `data.json`
5. **Config bootstrap separado** — o `config.json` (pequeno) aponta para o `data.json` (grande)
6. **Sem lock-in** — formato JSON universal, portável entre máquinas

---

## 📊 Diagrama de arquitetura

```text
┌──────────────────────┐   HTTP    ┌──────────────────────┐
│  Browser (cliente)   │ ────────► │  app.py              │
│  SPA — Dark / Light  │           │  ThreadingHTTPServer │
└──────────────────────┘           └──────────┬───────────┘
                                              │
                                              ▼
                                   ┌──────────────────────┐
                                   │  data.json           │
                                   │  Disco / NAS / Drive │
                                   └──────────────────────┘
```

**Componentes:**

- **Cliente (browser)** — SPA single-page com HTML/CSS/JS embutidos no Python. Zero build step, zero framework.
- **Servidor (`app.py`)** — `ThreadingHTTPServer` da stdlib. Serve a SPA, expõe a API, gere a persistência.
- **Persistência (`data.json`)** — Ficheiro único, caminho configurável. Escrita atómica com backups automáticos.

---
## ⚙️ Variáveis de Ambiente Suportadas

| Variável | Valor Padrão | Descrição |
|---|---|---|
| `MYUNISTATION_PORT` | `8080` | Porta TCP do servidor HTTP |
| `MYUNISTATION_HOST` | `0.0.0.0` | IP de escuta (permite acesso em LAN) |
| `MYUNISTATION_CONFIG` | `config.json` | Ficheiro com o caminho do data.json |
| `MYUNISTATION_DATA` | `data.json` | Caminho predefinido da base de dados |
---

## 🔌 API HTTP

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/` | Aplicação single-page (HTML/CSS/JS) |
| `GET` | `/api/data` | Estado completo do utilizador |
| `POST` | `/api/data` | Guarda o estado (validação estrutural server-side) |
| `GET` | `/api/config` | Configuração atual (caminho, metadados, se é acessível) |
| `POST` | `/api/config/preview` | Pré-visualiza uma alteração de armazenamento |
| `POST` | `/api/config` | Aplica uma alteração de armazenamento |

### Exemplos

**Ler o estado atual:**

```bash
curl http://localhost:8080/api/data
```

**Guardar novo estado:**

```bash
curl -X POST http://localhost:8080/api/data \
  -H "Content-Type: application/json" \
  -d @data.json
```

**Pré-visualizar alteração de armazenamento:**

```bash
curl -X POST http://localhost:8080/api/config/preview \
  -H "Content-Type: application/json" \
  -d '{"new_path": "/mnt/nas/MyUni/data.json"}'
```

**Aplicar alteração:**

```bash
curl -X POST http://localhost:8080/api/config \
  -H "Content-Type: application/json" \
  -d '{"new_path": "/mnt/nas/MyUni/data.json", "action": "use_local"}'
```

**Ações disponíveis:** `use_local`, `use_remote`, `migrate_to_empty`, `start_fresh`.

---

## 🛡️ Garantias técnicas

### Escrita atómica

Sempre que o estado é gravado:

1. Cria um ficheiro temporário **no mesmo diretório do destino** (mesmo filesystem)
2. Escreve o conteúdo completo
3. Força `fsync` para o disco
4. Substitui o original com `os.replace()` — atómico no mesmo filesystem

**Resultado:** o `data.json` está sempre numa versão íntegra. Um `kill -9` a meio da escrita não o corrompe.

```python
def _write_json_atomic(path, data):
    fd, tmp = tempfile.mkstemp(dir=parent)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)   # atómico
```

### Backups com retenção

Antes de qualquer operação que substitua um ficheiro existente:

1. O ficheiro é renomeado para `.backup-<timestamp>`
2. Se houver colisão no mesmo segundo, adiciona sufixo `-1`, `-2`, ...
3. Mantém os últimos **5** backups; apaga os mais antigos automaticamente

**Resultado:** mesmo que estragues algo, recuperas sempre uma versão recente.

### Schema versionado

O `data.json` inclui um campo `schema_version`. Quando a estrutura evoluir, a app faz **migração automática** sem perder dados.

```python
def _migrate_state(data):
    migrated = dict(data)
    v = migrated.get("schema_version", 0)
    if v < 1:
        migrated["schema_version"] = 1
    # futuras migrações:
    # if v < 2: ...
    return migrated
```

### Thread-safety

`ThreadingHTTPServer` (não `HTTPServer`) serve cada pedido numa thread separada. O estado partilhado está protegido por lock:

- `_config_cache` — protegido por `_config_lock`
- Escrita em ficheiro — atómica por natureza
- Leitura — sempre consistente graças ao `os.replace`

**Resultado:** múltiplos dispositivos na LAN podem aceder em simultâneo sem corrupção.

### Validação server-side

Antes de aceitar um `POST /api/data`, o servidor valida a estrutura:

```python
def _validate_state(data):
    if not isinstance(data, dict): return False
    if "profile" in data and not isinstance(data["profile"], dict): return False
    if "courses" in data and not isinstance(data["courses"], list): return False
    # ...
```

**Resultado:** mesmo com um cliente bugado, o servidor recusa gravar lixo.

---

## 📂 Estrutura do projeto

```text
my-unistation/
├── app.py                            # Servidor + SPA embutida
├── my_unistation_win_launch.bat      # Launcher Windows
├── my_unistation_linux_launch.sh     # Launcher Linux / WSL
├── LICENSE                           # MIT
├── README.md
├── .gitignore
│
├── assets/
│   └── my-unistation-logo.svg        # Logo oficial (vetorial)
│
├── prompt/
│   └── Master_prompt.md              # Briefing original do projeto              
│
└── docs/
    ├── .nojekyll
    ├── index.html                    # Website (GitHub Pages)
    ├── GETTING-STARTED.md            # Guia de arranque
    ├── TROUBLESHOOTING.md            # Problemas comuns
    ├── FILE_LOCATION.md              # Onde ficam os ficheiros
    ├── ARCHITECTURE.md               # Arquitetura técnica
    └── screenshots/
        ├── my_unistation_resumo.png
        ├── my_unistation_roteiro_1.png
        ├── my_unistation_roteiro_2.png
        ├── my_unistation_roteiro_3.png
        ├── my_unistation_atividades.png
        ├── my_unistation_puc.png
        └── my_unistation_calendario.png   
```

**Ficheiros gerados em runtime** (no `.gitignore`):

| Ficheiro | Papel |
|---|---|
| `config.json` | Bootstrap: guarda o caminho para o `data.json` |
| `data.json` | Estado completo do utilizador |
| `data.json.backup-*` | Backups automáticos (últimos 5) |

---

## 🎨 Fronteira técnica

### O que o servidor faz

- Servir a SPA (HTML/CSS/JS embutidos como string Python)
- Persistir o estado em `data.json` (com validação e escrita atómica)
- Gerir a configuração de armazenamento (`config.json`)
- Fazer backups com retenção

### O que o servidor **não faz**

- Autenticação (uso single-user doméstico)
- Base de dados SQL (JSON é suficiente para o volume)
- Multi-utilizador real (não é o objetivo)
- Telemetria ou analytics (nunca)
- Chamadas a serviços externos (nunca)

### Porque JSON e não SQLite/MySQL?

O volume de dados é de **dezenas de KB**, single-user, single-writer. Um ficheiro JSON:

- É portável (copiar = migrar)
- É legível (abrir no VSCode)
- É compatível com qualquer sistema de sincronização (Drive, Dropbox, Syncthing)
- Não requer servidor de BD

Se um dia o projeto evoluir para multi-utilizador com sessões, o passo natural é SQLite — mantém a filosofia "single file" e adiciona queries.

---

## 🤝 Contribuir

Contribuições são bem-vindas. Antes de abrires uma PR:

1. **Abre uma issue** a descrever a alteração pretendida
2. Aguarda feedback antes de investires tempo significativo
3. Mantém o projeto **sem dependências externas** — é um princípio central
4. Segue o estilo existente: nomes claros, funções curtas, comentários quando ajudam

### Princípios a preservar

- **Zero dependências** — apenas stdlib
- **Um ficheiro** — o `app.py` contém tudo
- **Local-first** — nada de chamadas externas
- **Portabilidade** — funciona em qualquer SO com Python 3.8+

---

## 📄 Licença

MIT — vê [`LICENSE`](../LICENSE) na raiz do repositório.