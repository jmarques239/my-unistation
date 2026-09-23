# 📋 Master Prompt — My UniStation

> **Documento de referência.** Este ficheiro contém o *briefing* original usado para iniciar o desenvolvimento do My UniStation com apoio de inteligência artificial.
>
> O objetivo é **duplo**:
> 1. Servir como demonstração transparente dos requisitos técnicos, funcionais e arquiteturais que orientaram a implementação.
> 2. Servir como modelo reutilizável para quem queira iniciar projetos semelhantes — mostrando o nível de detalhe necessário para o sucesso de um projeto assistido por IA.

[← Voltar ao README principal](../README.md)

---

## 🎯 Contexto

Pretende-se desenvolver uma aplicação **local-first** para estudantes da **Universidade Aberta (UAb)** que permita gerir o semestre em curso **e** acumular o histórico de UCs concluídas em semestres anteriores.

A aplicação deve correr inteiramente na máquina do utilizador, sem depender de serviços cloud, autenticação externa, ou qualquer tipo de telemetria. Os dados residem num único ficheiro que pertence ao utilizador.

O projeto é uma evolução do `uab-academic-dashboard-generator`, mas resolve as limitações arquiteturais do formato estático (uma página por semestre, persistência em `localStorage`, sem backend).

---

## 🎯 Objetivo funcional

Criar um dashboard académico que:

- Mostre o progresso da licenciatura em **ECTS** e a **média ponderada acumulada** (não apenas do semestre).
- Liste as **UCs em frequência** e o **histórico de UCs concluídas** com nota, ECTS e regime.
- Apresente um **calendário mensal** com aberturas, prazos e cotação de cada momento de avaliação.
- Apresente um **roteiro semanal** com as 17 semanas letivas decompostas, com tópicos e progresso.
- Consolide todas as **atividades sumativas** numa tabela ordenável.
- Arquive o **dossiê do PUC** de cada UC, incluindo transcrição integral do plano de atividades e do calendário de avaliação.
- Permita **importação assistida por IA** — o utilizador copia uma prompt pronta, cola no ChatGPT/Claude/Ollama com o PDF do PUC, e carrega o JSON resultante.
- Emita **alertas de prazos** configuráveis, com antecedência de 1 a 7 dias.
- Permita **configurar o caminho do ficheiro `data.json`** (PC, NAS, Google Drive, OneDrive, Dropbox).

---

## 🏗️ Requisitos técnicos

### Stack

- **Python 3.8+** — apenas biblioteca padrão. **Zero dependências externas.**
- **Servidor HTTP** — `ThreadingHTTPServer` da stdlib.
- **Frontend** — SPA single-page com HTML/CSS/JS embutidos numa string Python. Zero build step, zero framework.

### Persistência

- **Ficheiro único `data.json`** com caminho configurável.
- **`config.json` separado** como bootstrap — guarda o caminho para o `data.json`.
- **Escrita atómica** — `mkstemp` + `fsync` + `os.replace` para nunca corromper dados.
- **Backups automáticos** — antes de qualquer substituição, o ficheiro é renomeado para `.backup-<timestamp>` (retenção dos últimos 5).
- **Schema versionado** — campo `schema_version` no JSON para permitir migrações futuras.

### Segurança e robustez

- **Validação server-side** antes de aceitar qualquer `POST`.
- **Thread-safe** — servidor multi-thread com lock no cache de configuração.
- **Nunca sobrescrever sem confirmação** — qualquer alteração de armazenamento exige pré-visualização e confirmação explícita do utilizador.

### Launchers

- **Windows** (`my_unistation_win_launch.bat`) — deteta Python via `py`, instala via `winget` se necessário, abre o browser automaticamente.
- **Linux / WSL** (`my_unistation_linux_launch.sh`) — deteta Python 3.8+, instala via `apt`/`dnf`/`pacman`/`zypper`, mostra o URL em destaque no terminal.
- **macOS** — `.command` planeado (não obrigatório na primeira iteração).

---

## 🎨 Requisitos de design

- **Local-first by default.** Nada sai da máquina. Sem contas, sem telemetria, sem analytics.
- **Dark mode e Light mode** com persistência entre sessões.
- **Interface limpa** — hierarquia visual clara, sem ruído.
- **Uma cor por UC** — identifica visualmente cada disciplina em calendário, cartões e tabelas.
- **Feedback constante ao utilizador** — estado de sincronização visível (chip no topo).
- **Identidade visual** — logo próprio em SVG com gradiente de marca (esmeralda → ciano).

---

## 🔌 API HTTP pretendida

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/` | Aplicação single-page |
| `GET` | `/api/data` | Estado completo do utilizador |
| `POST` | `/api/data` | Guarda o estado (validação server-side) |
| `GET` | `/api/config` | Configuração atual + metadados |
| `POST` | `/api/config/preview` | Pré-visualiza alteração de armazenamento |
| `POST` | `/api/config` | Aplica alteração de armazenamento |

Ações de armazenamento suportadas: `use_local`, `use_remote`, `migrate_to_empty`, `start_fresh`.

---

## 📂 Estrutura de ficheiros pretendida

```
my-unistation/
├── app.py                            # Servidor + SPA embutida
├── my_unistation_win_launch.bat      # Launcher Windows
├── my_unistation_linux_launch.sh     # Launcher Linux / WSL
├── LICENSE                           # MIT
├── README.md
├── .gitignore
│
├── assets/
│   └── my-unistation-logo.svg
│
├── prompt/
│   └── Master_prompt.md              # Este ficheiro
│
└── docs/
    ├── index.html                    # Website (GitHub Pages)
    ├── GETTING-STARTED.md
    ├── TROUBLESHOOTING.md
    ├── FILE_LOCATION.md
    └── ARCHITECTURE.md
```

Ficheiros gerados em runtime (nunca versionados):

| Ficheiro | Papel |
|---|---|
| `config.json` | Bootstrap: caminho para o `data.json` |
| `data.json` | Estado completo do utilizador |
| `data.json.backup-*` | Backups automáticos (últimos 5) |

---

## ⛔ O que o projeto **não** deve fazer

- ❌ Não deve introduzir **dependências externas** — apenas stdlib.
- ❌ Não deve requerer **autenticação** — uso single-user doméstico.
- ❌ Não deve usar **base de dados SQL** — JSON é suficiente para o volume.
- ❌ Não deve enviar dados para **serviços externos** — nem telemetria, nem analytics, nem crashes.
- ❌ Não deve suportar **multi-utilizador real** — não é o objetivo.
- ❌ Não deve depender de **internet** depois de instalado.

---

## ✅ Critérios de sucesso

1. Arranca com `python3 app.py` sem qualquer configuração prévia.
2. Cria automaticamente `config.json` e `data.json` no primeiro arranque.
3. Funciona igualmente em Windows, Linux, macOS e WSL.
4. Nunca corrompe o `data.json`, mesmo com `kill -9` a meio de uma escrita.
5. Nunca perde dados — backups automáticos com retenção.
6. Nunca envia dados para fora da máquina.
7. É possível migrar de máquina copiando o `data.json`.
8. Interface funciona em desktop e mobile (layout responsivo).
9. Launcher Windows do duplo-clique ao dashboard em menos de 10 segundos.
10. Zero `pip install` obrigatório.

---

## 📝 Nota sobre o processo

Esta master prompt representa o **briefing inicial** que orientou o desenvolvimento.

A implementação foi conduzida com apoio extensivo de inteligência artificial, mas o **papel de arquiteto foi humano**. Cada decisão de design, cada requisito e cada restrição foram definidos antes do início da codificação.

O resultado demonstra que:

- **A IA amplifica** o trabalho de quem sabe o que quer.
- **A IA não substitui** a necessidade de requisitos claros e estruturados.
- **Os guard rails** — precisos, pormenorizados, verificáveis — são a diferença entre um projeto coeso e um amontoado de código funcional mas incoerente.

Esta master prompt é o exemplo prático dessa metodologia.

---

## 📄 Licença

MIT — vê [`LICENSE`](../LICENSE) na raiz do repositório.

[← Voltar ao README principal](../README.md)