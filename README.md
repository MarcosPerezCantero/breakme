# BreakMe

[![CI](https://github.com/MarcosPerezCantero/breakme/actions/workflows/ci.yml/badge.svg)](https://github.com/MarcosPerezCantero/breakme/actions/workflows/ci.yml)

Una API pequeña (Python + FastAPI) **diseñada para romperse a propósito**: se cuelga,
consume memoria, quema CPU o deja de estar lista cuando tú se lo pides. No pretende ser
útil como aplicación — es un banco de pruebas para **ver con tus propios ojos cómo
reaccionan Docker y Kubernetes ante distintos tipos de fallo**.

Cada endpoint provoca una avería controlada. Todos los parámetros peligrosos tienen un
límite para que la propia app no pueda tumbar la máquina anfitriona.

> 📐 ¿Quieres entender cómo encaja todo (Docker, CI y Kubernetes)? Lee **[ARCHITECTURE.md](ARCHITECTURE.md)**.

---

## Endpoints

| Endpoint | Qué hace | Qué permite probar |
|---|---|---|
| `GET /` | Devuelve versión y `hostname` | Ver **qué réplica** responde (balanceo de carga) |
| `GET /health` | Responde `200 {"status":"ok"}` | **Liveness**: ¿el proceso está vivo? |
| `GET /ready` | `200` si está lista, `503` si no | **Readiness**: ¿puede recibir tráfico? |
| `POST /toggle-ready` | Cambia el estado de `/ready` | Sacar una instancia del tráfico **sin matarla** |
| `GET /crash` | Mata el proceso (exit 1) | Reinicio automático ante una caída |
| `GET /leak?mb=N` | Reserva `N` MB y no los libera | Límite de memoria y **OOMKilled** |
| `GET /slow?seconds=N` | Tarda `N` segundos en responder | Latencia y timeouts |
| `GET /cpu?seconds=N` | Quema CPU durante `N` segundos | Consumo de CPU y autoescalado |

**Límites de seguridad:** `/leak` ≤ 500 MB en total · `/slow` ≤ 60 s · `/cpu` ≤ 60 s.

Documentación interactiva (Swagger) en **`/docs`** una vez arrancada.

---

## Arrancar

### Imagen publicada (GHCR)

```bash
docker run -d --name breakme -p 8000:8000 ghcr.io/marcosperezcantero/breakme:1.0.0
curl http://localhost:8000/
```

### Construir la imagen en local

```bash
docker build -t breakme:1.0.0 .
docker run -d --name breakme -p 8000:8000 breakme:1.0.0
```

La imagen usa un *build* multi-etapa, corre como **usuario no root** (`appuser`, uid 10001)
e incluye un `HEALTHCHECK` que consulta `/health`.

### Varias réplicas con balanceo (Docker Compose)

Levanta 3 instancias de BreakMe detrás de un nginx que reparte el tráfico:

```bash
docker compose up -d --scale breakme=3
curl http://localhost:8080/        # repite: el "hostname" cambia en cada petición
docker compose down
```

### En Kubernetes (3 réplicas, self-healing y autoescalado)

Los manifiestos están en `k8s/`. Requiere un clúster (p. ej. Docker Desktop con Kubernetes
activado) y, una sola vez, instalar un Ingress Controller (`ingress-nginx`) y `metrics-server`
(este último para el autoescalado).

```bash
kubectl create namespace breakme
kubectl apply -f k8s/ -n breakme
kubectl get pods,svc,ingress,hpa -n breakme
# acceso por el Ingress:  http://breakme.localhost/docs
```

Incluye Deployment (3 réplicas), Service, probes (liveness/readiness), ConfigMap, Secret,
`securityContext` (no root, disco de solo lectura), Ingress y HPA (autoescalado por CPU).
Todo explicado en **[ARCHITECTURE.md](ARCHITECTURE.md)**.

---

## Pruebas que puedes hacer

Ejemplos contra la instancia local (`:8000`) o contra el balanceador de Compose (`:8080`).

### 1. Caída y reinicio

```bash
docker run -d --name breakme -p 8000:8000 --restart=always breakme:1.0.0
curl http://localhost:8000/crash     # el proceso muere
docker ps                            # con --restart, el contenedor vuelve a estar "Up"
```

Sin política de reinicio, el contenedor queda `Exited (1)` y no se recupera solo.

### 2. Agotar la memoria (OOMKilled)

```bash
docker run -d --name breakme -p 8000:8000 --memory=256m breakme:1.0.0
curl "http://localhost:8000/leak?mb=100"   # repite hasta superar los 256 MB
docker inspect breakme --format '{{.State.OOMKilled}}'   # -> true
docker ps -a                                # STATUS: Exited (137)
```

El kernel mata el proceso al pasar del límite. El código de salida **137** (`128 + 9`,
SIGKILL) es la firma de un OOM.

### 3. Latencia

```bash
curl "http://localhost:8000/slow?seconds=5"   # tarda 5 s en responder
```

Útil para observar timeouts y cómo se comporta un balanceador ante respuestas lentas.

### 4. Carga de CPU

```bash
curl "http://localhost:8000/cpu?seconds=20"   # quema CPU 20 s
docker stats                                  # observa el consumo en vivo
```

### 5. Sacar una instancia del tráfico sin matarla (readiness)

```bash
curl http://localhost:8000/ready          # 200 ready
curl -X POST http://localhost:8000/toggle-ready
curl http://localhost:8000/ready          # 503 not ready
```

El proceso sigue vivo (`/health` responde 200), pero un orquestador que vigile `/ready`
dejaría de enviarle tráfico hasta que vuelva a estar lista.

### 6. Balanceo entre réplicas

```bash
docker compose up -d --scale breakme=3
curl http://localhost:8080/   # repite varias veces: el "hostname" va rotando
curl http://localhost:8080/crash
curl http://localhost:8080/   # las réplicas restantes siguen respondiendo
```

---

## Estructura

```
breakme/
├── app/
│   └── main.py              # la API y todos los endpoints de fallo
├── requirements.txt         # FastAPI + uvicorn
├── Dockerfile               # build multi-etapa, usuario no root, healthcheck
├── .dockerignore
├── compose.yaml             # 3 réplicas de breakme + nginx como balanceador
├── nginx.conf               # configuración del balanceo (round-robin vía DNS de Docker)
├── .github/workflows/ci.yml # CI: build + escaneo Trivy + push a GHCR
├── k8s/                      # manifiestos de Kubernetes
│   ├── deployment.yaml      #   réplicas, imagen, probes, recursos, seguridad
│   ├── service.yaml         #   puerta estable + balanceo
│   ├── ingress.yaml         #   acceso por breakme.localhost
│   ├── configmap.yaml       #   configuración no sensible
│   ├── secret.yaml          #   dato sensible (demo)
│   └── hpa.yaml             #   autoescalado por CPU
├── README.md
└── ARCHITECTURE.md          # todas las piezas y cómo encajan
```

---

## Qué demuestra cada tipo de fallo

| Fallo | Lo que se observa |
|---|---|
| `/crash` | Diferencia entre `Exited` y `Up`; políticas de reinicio |
| `/leak` | Límites de memoria y `OOMKilled` (exit 137) |
| `/slow` | Latencia, timeouts y el efecto sobre un balanceador |
| `/cpu` | Consumo de CPU sostenido (base del autoescalado) |
| `/toggle-ready` | Readiness: retirar tráfico sin reiniciar el proceso |
| `/health` | Liveness: detectar un proceso vivo pero que ya no responde |
