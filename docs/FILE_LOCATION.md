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

## 🎯 Próximos passos

- **Documentação técnica:** [`ARCHITECTURE.md`](ARCHITECTURE.md) [`GETTING_STARTED.md`](GETTING_STARTED.md) [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
- **Website do projeto:** [https://jmarques239.github.io/my-unistation/](https://jmarques239.github.io/my-unistation/)
- **Reportar bugs:** [issues no GitHub](https://github.com/jmarques239/my-unistation/issues)