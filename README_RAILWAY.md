# Despliegue en Railway (GitHub) — checklist de producción

Este repositorio queda preparado para desplegarse en Railway desde GitHub con **3 servicios separados**:

1. **backend** (FastAPI + Socket.IO)
2. **frontend** (React + Vite servido como estático)
3. **postgres** (plugin PostgreSQL de Railway)

---

## 0) Diagnóstico rápido de la app (qué debes saber antes de desplegar)

### Backend (FastAPI)
- Expone API REST bajo `/api/*` y health checks en `/health`, `/health/live`, `/health/ready`.
- Usa `PORT` dinámico de Railway y corre con Uvicorn en `0.0.0.0`.
- Soporta PostgreSQL por `DATABASE_URL` (convierte automáticamente `postgres://` a `postgresql://`).
- Uvicorn abre el puerto **de inmediato**; las migraciones Alembic corren en segundo plano al iniciar (evita estado `Crashed` en Railway por healthcheck sin puerto).
- CORS:
  - Si defines `ALLOWED_ORIGINS`, usa esos orígenes exactos.
  - Si no defines y estás en producción, permite dominios `*.up.railway.app` por regex.

### Frontend (React)
- Usa `VITE_API_URL` para apuntar al backend en producción.
- Se sirve estático desde `dist` con `serve`.
- Requiere build correcto (Railway/Nixpacks lo hace por `npm run build`).

### Base de datos
- Recomendado usar PostgreSQL administrado por Railway (no SQLite en producción).

---

## 1) Crear el proyecto en Railway desde GitHub

1. Entra a [https://railway.app](https://railway.app).
2. `New Project` → `Deploy from GitHub repo`.
3. Selecciona este repositorio.

---

## 2) Crear servicio PostgreSQL

1. Dentro del proyecto Railway: `+ New` → `Database` → `Add PostgreSQL`.
2. Railway creará la variable de conexión (`DATABASE_URL`).

---

## 3) Crear servicio backend

1. `+ New` → `GitHub Repo` → selecciona este repo.
2. En `Settings` del servicio backend:
   - **Root Directory**: `.` (raíz, usa `Procfile` de raíz)
   - **Start Command**: vacío (usa Procfile)
   - **Build Command**: vacío (auto por Nixpacks)
3. En `Networking`, genera dominio público.
4. En `Variables`, define:

| Variable | Obligatoria | Valor recomendado |
|---|---|---|
| `DATABASE_URL` | Sí | URL privada/internal del PostgreSQL (`*.railway.internal`); evita `*.proxy.rlwy.net` para backend dentro de Railway |
| `DATABASE_PRIVATE_URL` | Opcional | Solo si `PREFER_DATABASE_PRIVATE_URL=1` y la red privada funciona |
| `PREFER_DATABASE_PRIVATE_URL` | Opcional | `0` por defecto — usa `DATABASE_URL` tal cual (recomendado en Railway) |
| `ENVIRONMENT` | Sí | `production` |
| `JWT_SECRET` | Sí | Secreto largo/único (no default) |
| `ALLOWED_ORIGINS` | Sí | URL exacta del frontend (separadas por coma si varias) |
| `SOCKETIO_SAFE_MODE` | Recomendado | `1` |
| `GEMINI_API_KEY` | Opcional | si usas funciones Gemini |
| `MIGRATION_MAX_ATTEMPTS` | Opcional | `12` por intento y URL; súbelo si Postgres tarda en arrancar |
| `MIGRATION_INITIAL_DELAY_SECONDS` | Opcional | `15` por defecto; espera antes del primer intento (warm-up de Postgres) |
| `MIGRATION_RETRY_BASE_SECONDS` | Opcional | `3` por defecto; espera base entre reintentos |
| `MIGRATION_FALLBACK_PUBLIC` | Opcional | `1` por defecto; si la URL privada hace timeout, prueba el proxy TCP público |
| `DB_CONNECT_TIMEOUT` | Opcional | `15` segundos para conectar a Postgres |
| `DB_SSLMODE` | Opcional | `require` en proxy público; `disable` en red interna (auto si vacío) |

> Importante: **no** dejes `JWT_SECRET` por defecto en producción.

---

## 4) Crear servicio frontend

1. `+ New` → `GitHub Repo` → mismo repo.
2. En `Settings` del frontend:
   - **Root Directory**: `frontend`
   - **Start Command**: vacío (usa `frontend/Procfile`)
   - **Build Command**: vacío (Nixpacks ejecuta build)
3. Genera dominio público en `Networking`.
4. Variables:

| Variable | Obligatoria | Ejemplo |
|---|---|---|
| `VITE_API_URL` | Sí | `https://<tu-backend>.up.railway.app` |

> Sin `VITE_API_URL`, el frontend intentará consumir API del mismo dominio y fallará en producción separada.

---

## 5) Orden recomendado de configuración

1. Despliega backend y frontend para obtener dominios.
2. Coloca `VITE_API_URL` en frontend con el dominio backend.
3. Coloca `ALLOWED_ORIGINS` en backend con el dominio frontend.
4. Redeploy de ambos servicios.

---

## 6) Validación post-despliegue

### Backend
- `GET https://<backend>/health` → `ok: true`
- `GET https://<backend>/health/ready` → listo

### Frontend
- Abre `https://<frontend>` y valida:
  - login carga,
  - listado de tarjetas,
  - creación/edición,
  - tiempo real (Socket.IO).

### CORS
Si ves errores de CORS, revisa que:
- `ALLOWED_ORIGINS` tenga la URL exacta de frontend,
- sin slash final,
- redeploy aplicado.

---

## 7) Archivos relevantes para Railway en este repo

- `Procfile` (raíz): `web` ejecuta `backend/scripts/run_migrations.py` y luego Uvicorn.
- `backend/Procfile`: alternativa si despliegas backend con root `backend`.
- `frontend/Procfile`: sirve build estático desde `dist`.
- `backend/runtime.txt`: fija Python 3.11 para build compatible.
- `railway.json`: política de despliegue (replicas/restart).

---

## 8) Troubleshooting rápido

### Error de conexión DB
- Verifica que backend esté conectado al servicio PostgreSQL correcto.
- Confirma `DATABASE_URL` en variables del backend. Para servicios dentro del mismo proyecto Railway, usa la URL privada/internal (`*.railway.internal`), no el TCP proxy público (`*.proxy.rlwy.net`).
- Si `DATABASE_URL` quedó apuntando a `*.proxy.rlwy.net`, agrega `DATABASE_PRIVATE_URL` con la URL privada del Postgres; el backend la preferirá automáticamente.
- En Railway, **enlaza** el servicio Postgres al backend con variable referenciada (`${{Postgres.DATABASE_URL}}`), no copies URLs a mano. Si existe `PGHOST`, el backend lo usará para la red interna.
- Si ves `timeout expired` contra `*.railway.internal`: Postgres puede estar apagado, en otro proyecto, o el hostname privado es incorrecto. Verifica que el servicio Postgres esté **Running** en el mismo proyecto.
- Si la privada falla pero el proxy público conecta, deja `MIGRATION_FALLBACK_PUBLIC=1` (default) y mantén `DATABASE_URL` con el proxy; el runner probará ambas.
- Aumenta `MIGRATION_INITIAL_DELAY_SECONDS=30` si Postgres tarda en despertar tras un deploy.

### Error 401/403 inesperado
- Revisa `JWT_SECRET` entre despliegues (si cambia, invalida tokens).

### Socket.IO no conecta
- Revisar `ALLOWED_ORIGINS` y dominio final del frontend.
- Confirmar que backend usa HTTPS público de Railway.

### Frontend muestra pantalla pero no datos
- Confirmar `VITE_API_URL` correcto y sin slash final.

