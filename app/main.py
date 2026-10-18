"""
BreakMe — una API diseñada para romperse a propósito.

Cada endpoint provoca un tipo de fallo distinto para poder observar cómo
reaccionan Docker (Fase 4) y Kubernetes (Fases 9-11). Todos los parámetros
peligrosos tienen un límite para que la propia app no pueda tumbar la máquina.
"""

import asyncio
import os
import socket
import threading
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

# --- Metadatos de la app -----------------------------------------------------
# APP_VERSION se podrá cambiar por variable de entorno (útil en las fases de
# rolling update y ConfigMap, sin reconstruir la imagen).
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
HOSTNAME = socket.gethostname()  # nos dirá QUÉ réplica responde (balanceo)

# --- Límites de seguridad ----------------------------------------------------
MAX_LEAK_MB = 500      # memoria máxima que /leak puede reservar en total
MAX_SLOW_SECONDS = 60  # retardo máximo de /slow
MAX_CPU_SECONDS = 60   # tiempo máximo que /cpu puede quemar

app = FastAPI(
    title="BreakMe",
    description="API que se rompe a propósito para aprender Docker y Kubernetes.",
    version=APP_VERSION,
)

# --- Estado interno ----------------------------------------------------------
# is_ready: controla la readiness. Empezamos listos.
is_ready = True
# _leaked: aquí guardamos la memoria reservada para que el recolector de
# basura NO la libere (así el consumo se mantiene y podemos provocar el OOM).
_leaked: list[bytearray] = []


@app.get("/")
def root():
    """Versión + hostname: nos dice qué réplica responde a cada petición."""
    return {
        "app": "BreakMe",
        "version": APP_VERSION,
        "hostname": HOSTNAME,
        "message": "Estoy vivo. Prueba a romperme en /docs",
    }


@app.get("/health")
def health():
    """LIVENESS: ¿el proceso está vivo y responde? Siempre 200 si corre."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """READINESS: ¿puede recibir tráfico? 200 si sí, 503 si no."""
    if is_ready:
        return {"status": "ready"}
    # 503 = Service Unavailable. Kubernetes lo sacará del balanceo.
    return JSONResponse(status_code=503, content={"status": "not ready"})


@app.post("/toggle-ready")
def toggle_ready():
    """Cambia el estado de /ready SIN matar el proceso.

    Sirve para sacar una réplica del balanceo (readiness falla) y ver que
    NO se reinicia, a diferencia de un fallo de liveness.
    """
    global is_ready
    is_ready = not is_ready
    return {"is_ready": is_ready}


@app.get("/crash")
def crash():
    """Mata el proceso entero (código de salida 1).

    Respondemos primero y matamos 0.3 s después, así el cliente recibe la
    respuesta antes de que el proceso muera. Demuestra el reinicio automático.
    """
    def _kill():
        time.sleep(0.3)
        os._exit(1)  # salida inmediata y brusca: adiós proceso

    threading.Thread(target=_kill, daemon=True).start()
    return {"message": "Me muero en 0.3s... adiós 💀"}


@app.get("/leak")
def leak(mb: int = Query(50, ge=1, description="MB a reservar sin liberar")):
    """Reserva memoria y NO la libera. Útil para forzar un OOMKilled."""
    already = sum(len(b) for b in _leaked) // (1024 * 1024)
    if already + mb > MAX_LEAK_MB:
        raise HTTPException(
            status_code=400,
            detail=f"Límite de seguridad: máximo {MAX_LEAK_MB} MB "
                   f"(ya reservados {already} MB).",
        )
    # Rellenamos con datos reales para que la memoria se asigne de verdad.
    _leaked.append(bytearray(mb * 1024 * 1024))
    total = sum(len(b) for b in _leaked) // (1024 * 1024)
    return {"leaked_mb_now": mb, "leaked_mb_total": total}


@app.get("/slow")
async def slow(seconds: float = Query(1.0, ge=0, description="Segundos de retardo")):
    """Tarda N segundos en responder. Demuestra timeouts y latencia."""
    seconds = min(seconds, MAX_SLOW_SECONDS)
    await asyncio.sleep(seconds)
    return {"slept_seconds": seconds}


@app.get("/cpu")
def cpu(seconds: float = Query(5.0, ge=0, description="Segundos quemando CPU")):
    """Quema CPU en un bucle ocupado. Demuestra el autoescalado por CPU."""
    seconds = min(seconds, MAX_CPU_SECONDS)
    deadline = time.time() + seconds
    # Bucle que no hace nada útil, solo consumir ciclos de CPU.
    while time.time() < deadline:
        _ = sum(i * i for i in range(10_000))
    return {"burned_cpu_seconds": seconds}
