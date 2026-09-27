# 📂 File Location — My UniStation

Onde ficam os ficheiros gerados pela aplicação e como movê-los para outro local.

[← Voltar ao README principal](docs/README.md)

---

## 📂 Onde ficam os ficheiros

Por defeito, tudo vive na **mesma pasta do `app.py`**:

| Ficheiro | Criado quando | Descrição |
|---|---|---|
| `config.json` | 1º arranque | Guarda o caminho para o `data.json` |
| `data.json` | 1ª alteração de estado | Todo o teu estado académico |
| `data.json.backup-*` | Antes de substituições | Backups automáticos (últimos 5) |

**Todos estes ficheiros são gerados localmente** e nunca devem ser versionados. O `.gitignore` já os exclui.

### Queres os dados noutro sítio?

Na app: **⚙️ Definições → 🗄️ Armazenamento**. Aí podes apontar o `data.json` para:

- Uma pasta local diferente
- **Google Drive** / **OneDrive** / **Dropbox** (sincronização entre dispositivos)
- **NAS** (via NFS ou SMB montado localmente)
- Qualquer caminho que o teu sistema operativo trate como um ficheiro

A app **nunca sobrescreve** sem confirmação e faz backup automático antes de qualquer alteração.

---

## 🐳 Em modo Docker

Quando corres o My UniStation num container, os ficheiros vivem numa pasta **diferente** da do código:

| Localização | O que contém |
|---|---|
| `/app/` (dentro do container) | Código (`app.py`) — recriado a cada `docker compose up` |
| `/data/` (volume montado) | `config.json`, `data.json`, backups — persistem entre reinícios |

**O `config.json` gerado em Docker aponta para `/data/data.json`** (caminho absoluto), e não para `data.json` (relativo). Isto garante que os dados ficam sempre no volume, nunca dentro da imagem do container — mesmo que a imagem seja reconstruída ou o container recriado.

Se vires `/app/data.json` em **⚙️ Definições → 🗄️ Armazenamento** num ambiente Docker, é sinal de que o `config.json` tem um valor antigo. 
Correção rápida:

```bash
docker compose down
rm data/config.json        # ou o caminho do teu bind mount
docker compose up -d
```

O config.json é regenerado automaticamente com o valor correto.

📖 Detalhes completos do modo Docker: vê o [`docs/DOCKER.md`](docs/DOCKER.md).

---

## 🎯 Próximos passos

Arranque e instalação: [`GETTING_STARTED.md`](GETTING_STARTED.md)

Modo Docker: [`docs/DOCKER.md`](docs/DOCKER.md)

Problemas comuns: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)

Arquitetura técnica: [`ARCHITECTURE.md`](ARCHITECTURE.md)

Website do projeto: https://jmarques239.github.io/my-unistation/

Reportar bugs: [issues no GitHub](https://github.com/jmarques239/my-unistation/issues)
