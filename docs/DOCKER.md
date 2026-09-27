
# 🐳 Docker — My UniStation

Guia de execução do My UniStation em Docker.

[← Voltar ao README principal](../README.md)

---

## Porquê Docker?

O My UniStation foi concebido para correr diretamente com Python, sem dependências, sem instalação complexa. 
Docker acrescenta três vantagens para quem as valoriza:

- **Isolamento total** — o container não interfere com o sistema anfitrião
- **Consistência** — funciona igual em Windows, Linux, macOS e NAS
- **Arranque automático** — ideal para NAS, mini-PC ou Raspberry Pi
  que ficam sempre ligados

**O que não muda:** os dados continuam num ficheiro `data.json` que é teu. O container é efémero; os dados vivem no volume montado. A filosofia local-first mantém-se intacta, nada sai da tua máquina.

---

## 🚀 Arranque rápido

### Com Docker Compose (recomendado)

```bash
git clone https://github.com/jmarques239/my-unistation.git
cd my-unistation
docker compose up -d
```

Abre `http://localhost:8080` no browser.

Para parar:

```bash
docker compose down
```

Os dados persistem num volume Docker chamado `myunistation-data`. Sobrevivem a `down` e `up`. 
Só são removidos com:

```bash
docker compose down -v
```

### Com Docker puro (sem Compose)

```bash
docker build -t my-unistation .

docker run -d \
  --name my-unistation \
  -p 8080:8080 \
  -v myunistation-data:/data \
  --restart unless-stopped \
  my-unistation
```

---

## 📂 Onde ficam os dados

### Volume nomeado (padrão)

Os dados vivem dentro de um volume Docker gerido pelo próprio Docker. Esta é a opção mais segura, evita problemas de permissões e mantém a pasta do projeto limpa.

Para inspecionar o conteúdo:

```bash
# Ver o volume
docker volume inspect myunistation-data

# Listar ficheiros dentro do volume
docker run --rm -v myunistation-data:/data alpine ls -la /data

# Ver o conteúdo do data.json
docker run --rm -v myunistation-data:/data alpine cat /data/data.json
```

### Bind mount (alternativa)

Se preferires os ficheiros diretamente no teu filesystem, para backups automáticos com Google Drive, Syncthing, ou simples inspeção com o VSCode, usa um bind mount.

Edita o `docker-compose.yml` e substitui:

```yaml
volumes:
  - myunistation-data:/data
```

Por:

```yaml
volumes:
  - ./data:/data
```

Depois cria a pasta e arranca:

```bash
mkdir -p data
docker compose up -d
```

Os ficheiros `config.json` e `data.json` passam a aparecer em `./data/` na pasta do projeto.

**Nota sobre permissões:** em Linux, se o container não conseguir escrever no bind mount, ajusta as permissões da pasta:

```bash
sudo chown -R 1000:1000 ./data
```

O UID `1000` corresponde ao utilizador `myunistation` definido no Dockerfile.

---

## ⚙️ Configuração avançada

### Mudar a porta

Edita `docker-compose.yml`:

```yaml
ports:
  - "9090:8080"      # acede em localhost:9090
```

### Definir fuso horário

Adiciona ao serviço:

```yaml
environment:
  TZ: "Europe/Lisbon"
```

Útil se usares alertas de prazos e quiseres que as horas coincidam com o teu relógio local.

### Limitar recursos

Útil em NAS modestos, mini-PCs ou Raspberry Pi. Descomenta no `docker-compose.yml`:

```yaml
mem_limit: 256m
cpus: 1.0
```

O My UniStation usa tipicamente < 50 MB de RAM. Estes limites são generosos e evitam que uma máquina modesta fique sobrecarregada.

### Correr em NAS (Synology / QNAP / TrueNAS)

1. **Synology**: instala o pacote **Container Manager** no Package Center
2. **QNAP**: instala o **Container Station** na App Center
3. **TrueNAS**: usa o **Apps** → **Custom App** ou o Portainer embutido

Em qualquer um deles:

- Faz upload da pasta do projeto para um share do NAS (ex: `/volume1/docker/my-unistation`)
- Abre um terminal SSH no NAS
- Entra na pasta e corre:
  ```bash
  cd /volume1/docker/my-unistation
  docker compose up -d
  ```
- Acede via `http://<ip-do-nas>:8080`

### Correr em Raspberry Pi

O Dockerfile funciona diretamente em ARM (64-bit). Se usares um modelo antigo (Pi 3 ou anterior, 32-bit), ajusta a imagem base do Dockerfile:

```dockerfile
FROM python:3.12-slim
```

Para:

```dockerfile
FROM arm32v7/python:3.12-slim
```

Os modelos Pi 4 e Pi 5 (64-bit) não precisam de alteração.

---

## 🔄 Atualizar para nova versão

Quando quiseres puxar as últimas alterações do repositório:

```bash
git pull
docker compose up -d --build
```

Os dados permanecem intactos no volume. A atualização substitui apenas o código dentro do container.

Para um rebuild totalmente limpo (sem cache de layers):

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

---

## 🐛 Troubleshooting

### ** 1 - "Porta 8080 já em uso"

Outro serviço está a usar a porta. Duas opções:

- Para o serviço concorrente
- Ou muda a porta no `docker-compose.yml` (ex: `9090:8080`)


### ** 2 - O container arranca mas o browser não abre

Verifica os logs:

```bash
docker compose logs -f
```

Deves ver a linha `✅ My UniStation ativo`. Se não vires, algo está mal na configuração, verifica se as variáveis de ambiente estão corretas.

### ** 3 - Container aparece como "unhealthy"

O healthcheck bate no endpoint `/api/data`. Se falhar, verifica:

```bash
docker inspect my-unistation | grep -A 10 Health
```

As causas mais comuns:

- Porta interna diferente de 8080 (raro, não mexer sem razão)
- Firewall a bloquear a porta dentro da rede Docker
- App bloqueado por uma exceção não tratada (ver logs)

### ** 4 - Erro de permissões no volume

Se o container não consegue escrever:

```bash
docker compose down
docker volume rm myunistation-data
docker compose up -d
```

Recria o volume com as permissões corretas.

Se usaste bind mount e continua a falhar:

```bash
sudo chown -R 1000:1000 ./data
```

### ** 5 - "Cannot connect to the Docker daemon"

O Docker Desktop (Windows/macOS) ou o serviço `docker` (Linux) não está a correr. Inicia-o e tenta novamente.

### ** 6 - Como fazer backup dos dados

**Se usas volume nomeado:**

```bash
docker run --rm \
  -v myunistation-data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/myunistation-backup.tar.gz -C /data .
```

Cria `myunistation-backup.tar.gz` na pasta atual.

**Se usas bind mount:** os ficheiros estão em `./data/`. Basta copiar essa pasta.

### ** 7 - O caminho do data.json aparece como `/app/data.json` (errado)

Vês isto em **⚙️ Definições → 🗄️ Armazenamento** e o caminho está diferente do esperado (`/data/data.json`).

**Causa:** o `config.json` dentro do volume tem um valor antigo provavelmente de um arranque anterior à configuração do env var, ou copiado do modo nativo.

**Correção:**

```bash
docker compose down
rm data/config.json        # ou o caminho equivalente ao teu bind mount
docker compose up -d
```

---

## 🗑️ Remover tudo

Para apagar o container, a imagem e o volume (irreversível):

```bash
docker compose down -v
docker rmi my-unistation:latest
```

Sem o `-v`, os dados ficam no volume e podem ser recuperados num arranque futuro.

---

## ⚠️ Nota sobre local-first

Docker não altera a filosofia do projeto. O container é **stateless**, o código corre isolado, mas os dados vivem fora dele. Isto significa:

- Podes apagar o container sem perder dados
- Podes reconstruir a imagem sem perder dados
- Podes mover o volume entre máquinas
- Podes correr Docker **e** o modo nativo (Python direto) em paralelo,
  apontando ambos para o mesmo `data.json`

O `data.json` continua a ser **teu**. O Docker é apenas uma forma conveniente de o servir.

---

## 📚 Ver também

- [`GETTING_STARTED.md`](GETTING_STARTED.md) — instalação nativa (Python direto, launchers)
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — problemas comuns em modo nativo
- [`FILE_LOCATION.md`](FILE_LOCATION.md) — onde ficam os ficheiros gerados
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — visão técnica do servidor

[← Voltar ao README principal](../README.md)


