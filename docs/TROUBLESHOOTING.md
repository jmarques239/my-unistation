## 🔧 Troubleshooting

### "Python não é reconhecido como comando"

**Windows:** provavelmente o Python não foi adicionado ao `PATH` durante a instalação. Opções:

- Reinstalar o Python marcando **"Add Python to PATH"** no instalador oficial
- Usar o **`py`** em vez de `python` — o launcher oficial costuma funcionar mesmo nestes casos
- Usar o `my_unistation_win_launch.bat` que deteta e resolve isto automaticamente

**Linux:** instala o Python com o gestor de pacotes da tua distro.

```bash
sudo apt install python3       # Debian / Ubuntu / Mint
sudo dnf install python3       # Fedora / RHEL
sudo pacman -S python          # Arch / Manjaro
sudo zypper install python3    # openSUSE
```

### "Address already in use" na porta 8080

Já tens outro processo a usar a porta 8080 (talvez uma instância anterior do My UniStation). Soluções:

- Fecha a outra instância
- Ou muda a porta com uma variável de ambiente:

```bash
MYUNISTATION_PORT=8081 python3 app.py
```

### O browser abre mas mostra "não foi possível ligar"

O servidor ainda não arrancou quando o browser abriu. Espera 2–3 segundos e faz **F5**. Se persistir:

- Confirma que a janela do terminal mostra o output `✅ My UniStation ativo`
- Verifica se não há um firewall a bloquear a porta 8080

### "Permission denied" ao correr o `.sh` no Linux

Falta o bit de executável:

```bash
chmod +x my_unistation_linux_launch.sh
```

### Ficheiros com `\r` no Linux (erro `$'\r': command not found`)

O ficheiro foi editado no Windows e ficou com line endings CRLF. Corrige com:

```bash
sed -i 's/\r$//' my_unistation_linux_launch.sh
```

**Prevenção futura:** cria um `.gitattributes` no repositório com:

```
*.sh text eol=lf
*.bat text eol=crlf
```

---


## 🎯 Próximos passos

- **Documentação técnica:** [`ARCHITECTURE.md`](ARCHITECTURE.md) [`GETTING_STARTED.md`](GETTING_STARTED.md) [`FILE_LOCATION.md`](FILE_LOCATION.md)
- **Website do projeto:** [https://jmarques239.github.io/my-unistation/](https://jmarques239.github.io/my-unistation/)
- **Reportar bugs:** [issues no GitHub](https://github.com/jmarques239/my-unistation/issues)