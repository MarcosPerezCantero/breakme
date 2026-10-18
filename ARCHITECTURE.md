# Arquitectura de BreakMe

Este documento explica **todas las piezas** del proyecto y cómo encajan: desde el
código hasta la app corriendo en Kubernetes, autoescalándose y curándose sola.

BreakMe es una API (FastAPI) hecha para **romperse a propósito**. No importa la app
en sí: es la excusa para ver cómo **Docker** y **Kubernetes** reaccionan ante fallos.

---

## 1. Visión de conjunto: el viaje del código

```
  Tú escribes código
        │
        ▼
  ┌───────────┐   docker build   ┌───────────┐   push    ┌──────────────┐
  │  Código   │ ───────────────► │  Imagen   │ ────────► │    GHCR      │
  │ (app/)    │   (Dockerfile)   │ breakme   │           │ (registry)   │
  └───────────┘                  └───────────┘           └──────┬───────┘
        │                                                        │
        │ git push                                               │ pull
        ▼                                                        ▼
  ┌───────────┐   GitHub Actions (CI)                     ┌──────────────┐
  │  GitHub   │ ─► build + Trivy (escaneo) + push ───────►│  Kubernetes  │
  │  (repo)   │                                           │  (clúster)   │
  └───────────┘                                           └──────────────┘
```

Dos mundos que resuelven problemas distintos:

- **Docker** responde a *"¿cómo empaqueto y ejecuto mi app de forma reproducible?"*
- **Kubernetes** responde a *"¿cómo la mantengo viva, escalada y actualizada sin intervención humana?"*

---

## 2. La aplicación (`app/main.py`)

Una API con endpoints de dos tipos:

**Palancas de avería** (para provocar fallos a mano):
| Endpoint | Rompe |
|---|---|
| `/crash` | mata el proceso (exit 1) |
| `/leak?mb=N` | reserva memoria y no la libera (fuerza OOM) |
| `/cpu?seconds=N` | quema CPU (dispara el autoescalado) |
| `/slow?seconds=N` | tarda a propósito (latencia) |

**Sondas de salud** (las consulta el orquestador, no el usuario):
| Endpoint | Pregunta que responde |
|---|---|
| `/health` | ¿el proceso está vivo? (**liveness**) |
| `/ready` | ¿puede recibir tráfico ahora? (**readiness**) |
| `/toggle-ready` | cambia el estado de `/ready` sin matar el proceso |
| `/` | versión + hostname (sirve para ver qué réplica responde) |

Todos los parámetros peligrosos tienen un **límite** para que la app no pueda tumbar
la máquina anfitriona.

---

## 3. Capa Docker

### Dockerfile (imagen pequeña y segura)

```
ETAPA 1 (builder)            ETAPA 2 (final)
├─ python:3.12-slim          ├─ python:3.12-slim
├─ crea venv                 ├─ copia SOLO el venv de la etapa 1
└─ pip install deps   ─────► ├─ copia el código
   (con compiladores)        ├─ USER 10001 (no root)
                             ├─ HEALTHCHECK /health
                             └─ arranca uvicorn
```

- **Multi-stage**: la imagen final no lleva `pip` ni compiladores → más pequeña y
  con menos superficie de ataque (~60 MB).
- **Orden = caché**: `requirements.txt` se copia e instala ANTES que el código, así
  cambiar el código no reinstala las dependencias.
- **No root** (`USER 10001`): si comprometen la app, no es root dentro del contenedor.

### Docker Compose (`compose.yaml`)

Para desarrollo local: levanta **3 réplicas** de BreakMe + un **nginx** que balancea
delante. Demuestra, en una sola máquina, redes internas por DNS y balanceo. Su límite:
una sola máquina, sin self-healing real ni autoescalado → por eso existe Kubernetes.

---

## 4. CI — Integración Continua (`.github/workflows/ci.yml`)

En cada `git push` a `main`, GitHub arranca una máquina y, solo:

```
checkout → build imagen → Trivy (escaneo de vulnerabilidades) → push a GHCR
```

- **Trivy** corta el pipeline si encuentra vulnerabilidades CRÍTICAS con parche.
- Publica la imagen con tags: el SHA del commit (inmutable) y `latest`.
- Usa el `GITHUB_TOKEN` automático (no hace falta un token manual).

El **CD** (desplegar) es manual en el clúster local: `kubectl apply -f k8s/`.

---

## 5. Capa Kubernetes — el concepto central

**Bucle de reconciliación:** tú declaras el **estado deseado** (en los YAML de `k8s/`),
y Kubernetes compara sin parar con el **estado real** y corrige las diferencias.

```
   cada pocos segundos, para siempre:
      estado deseado (YAML)   vs.   estado real (clúster)
      ¿coinciden? → no hace nada
      ¿NO? → actúa hasta que coincidan     ← de aquí sale el self-healing
```

### Las piezas y cómo se conectan

```
                         INTERNET / tu navegador
                                   │
                                   ▼
                        ┌────────────────────┐
                        │     INGRESS        │  breakme.localhost
                        │ (ingress-nginx)    │  enruta por nombre/ruta
                        └─────────┬──────────┘
                                  ▼
                        ┌────────────────────┐
                        │     SERVICE        │  IP y nombre estables
                        │  (ClusterIP)       │  balancea entre pods
                        └─────────┬──────────┘
                     selecciona por etiqueta app: breakme
            ┌─────────────────────┼─────────────────────┐
            ▼                     ▼                     ▼
       ┌─────────┐           ┌─────────┐           ┌─────────┐
       │  POD    │           │  POD    │           │  POD    │   (efímeros)
       │ breakme │           │ breakme │           │ breakme │
       └─────────┘           └─────────┘           └─────────┘
            ▲                                             ▲
            │  los crea y mantiene (replicas: 3)          │
       ┌────┴─────────────────────────────────────────────┴───┐
       │                   DEPLOYMENT                          │
       │  (vía un ReplicaSet que mantiene el número de pods)   │
       └───────────────────────────────────────────────────────┘
            ▲                                             │
            │ escala el número de réplicas                │ inyecta config
       ┌────┴─────┐                                 ┌─────┴───────────────┐
       │   HPA    │  CPU media > 50% → +réplicas    │ ConfigMap + Secret  │
       │ (3→10)   │  (usa metrics-server)           │ (variables de env)  │
       └──────────┘                                 └─────────────────────┘
```

### Qué hace cada objeto

| Objeto | Rol | Fichero |
|---|---|---|
| **Namespace** `breakme` | Cajón lógico que aísla nuestros recursos | (creado a mano) |
| **Deployment** | Declara *"quiero 3 réplicas de esta imagen"*; las mantiene | `k8s/deployment.yaml` |
| **ReplicaSet** | Lo crea el Deployment; es quien vigila el número de pods | (automático) |
| **Pod** | Unidad mínima: envuelve el contenedor. Efímero y desechable | (automático) |
| **Service** | IP/nombre estables delante de pods que cambian; balancea | `k8s/service.yaml` |
| **Ingress** | Puerta HTTP de entrada por nombre (`breakme.localhost`) | `k8s/ingress.yaml` |
| **ConfigMap** | Configuración no sensible, fuera de la imagen | `k8s/configmap.yaml` |
| **Secret** | Datos sensibles (base64, no cifrado) | `k8s/secret.yaml` |
| **HPA** | Autoescala réplicas según la CPU | `k8s/hpa.yaml` |

### Probes (sondas) — definidas en el Deployment

| Probe | Mira | Si falla |
|---|---|---|
| **liveness** | `/health` | **reinicia** el contenedor (app colgada pero viva) |
| **readiness** | `/ready` | lo **saca del tráfico** (no reinicia) |
| **startup** | `/health` | da margen al arranque antes de aplicar liveness |

### Seguridad — `securityContext` en el Deployment

- `runAsNonRoot` + `runAsUser: 10001` → nunca root.
- `readOnlyRootFilesystem: true` → disco raíz de solo lectura (un `/tmp` aparte para escribir).
- `allowPrivilegeEscalation: false` y `capabilities: drop ALL` → mínimos privilegios.

### Recursos — `requests` y `limits`

- **requests** (cpu 100m, mem 64Mi): lo que el pod reserva; el scheduler lo usa para colocarlo.
- **limits** (cpu 500m, mem 256Mi): el tope. Pasarse de memoria → **OOMKilled**; de CPU → *throttling*.

---

## 6. Qué demuestra cada fallo (Docker vs. Kubernetes)

| Acción | En Docker (una máquina) | En Kubernetes |
|---|---|---|
| `/crash` | muere; revive solo con `--restart` | se reinicia (RESTARTS sube); hay otras réplicas sirviendo |
| `/leak` | OOMKilled (exit 137), queda parado | OOMKilled + reinicio automático |
| colgada pero viva | Docker la marca `unhealthy` pero **no actúa** | la **liveness** la reinicia |
| `/toggle-ready` | — | sale del tráfico **sin** reiniciarse (readiness) |
| `/cpu` (carga) | — | el **HPA** añade réplicas solo |

En una frase: **Docker empaqueta y ejecuta; Kubernetes mantiene vivo, escala y actualiza sin intervención humana.**

---

## 7. Estructura del repositorio

```
breakme/
├── app/main.py              # la API y sus endpoints
├── requirements.txt         # FastAPI + uvicorn
├── Dockerfile               # imagen multi-stage, no root
├── .dockerignore
├── compose.yaml             # 3 réplicas + nginx (uso local)
├── nginx.conf               # balanceo en Compose
├── .github/workflows/ci.yml # pipeline: build + Trivy + push a GHCR
├── k8s/
│   ├── deployment.yaml      # réplicas, imagen, probes, recursos, seguridad
│   ├── service.yaml         # puerta estable + balanceo
│   ├── ingress.yaml         # acceso por breakme.localhost
│   ├── configmap.yaml       # configuración no sensible
│   ├── secret.yaml          # dato sensible (demo)
│   └── hpa.yaml             # autoescalado por CPU
├── README.md                # qué es y qué pruebas permite
└── ARCHITECTURE.md          # este documento
```

---

## 8. Cómo levantarlo en Kubernetes (resumen)

```bash
kubectl create namespace breakme
kubectl apply -f k8s/ -n breakme
kubectl get pods,svc,ingress,hpa -n breakme
# acceso:  http://breakme.localhost/docs   (requiere ingress-nginx instalado)
```

Componentes de clúster que hay que tener instalados una vez:
- **ingress-nginx** (para el Ingress).
- **metrics-server** (para que el HPA tenga métricas de CPU).
