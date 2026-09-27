FROM python:3.12-slim

LABEL org.opencontainers.image.title="My UniStation"
LABEL org.opencontainers.image.description="Dashboard académico local-first para estudantes Universitarios"
LABEL org.opencontainers.image.source="https://github.com/jmarques239/my-unistation"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.authors="João Marques"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random

# /data é o mount point do volume — dados persistem fora do container
ENV MYUNISTATION_HOST=0.0.0.0 \
    MYUNISTATION_PORT=8080 \
    MYUNISTATION_CONFIG=/data/config.json \
    MYUNISTATION_DATA=/data/data.json

WORKDIR /app

RUN useradd --system --create-home --uid 1000 myunistation && \
    mkdir -p /data && \
    chown -R myunistation:myunistation /app /data

COPY --chown=myunistation:myunistation app.py /app/app.py

USER myunistation

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/data', timeout=3)" || exit 1

CMD ["python3", "/app/app.py"]