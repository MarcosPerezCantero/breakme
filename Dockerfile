# syntax=docker/dockerfile:1

# ============================================================================
# ETAPA 1 — builder: instala las dependencias en un entorno virtual aislado.
# Esta etapa puede traer herramientas de compilación; NO irán a la imagen final.
# ============================================================================
FROM python:3.12-slim AS builder

# Creamos un venv en /opt/venv y lo ponemos en el PATH.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copiamos SOLO requirements.txt primero. Mientras este fichero no cambie,
# Docker reutiliza (CACHED) esta capa de instalación aunque cambie el código.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ============================================================================
# ETAPA 2 — final: imagen limpia y ligera. Solo recibe el venv ya instalado
# y el código. Sin pip, sin cachés, sin herramientas de compilación.
# ============================================================================
FROM python:3.12-slim

# Usuario NO root: si comprometen la app, no será root dentro del contenedor.
# uid 10001, con un nombre legible (appuser).
RUN useradd --uid 10001 --create-home appuser

# Traemos el entorno virtual con las dependencias ya instaladas.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Que Python no escriba .pyc y que los logs salgan sin buffer (se ven al momento).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
# El código se copia al final: es lo que más cambia, así no invalida capas previas.
COPY app/ ./app/

# A partir de aquí, el proceso corre como appuser (uid 10001), no como root.
USER 10001

EXPOSE 8000

# Docker comprobará periódicamente si la app responde en /health.
# Usamos Python (no curl) para no añadir paquetes extra a la imagen.
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
