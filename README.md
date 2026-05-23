# El Broncas ERP

ERP SaaS ligero para punto de venta, inventario, creditos y administracion de cuentas de clientes.

## Stack

- Django monolitico con HTML server-rendered.
- PostgreSQL en produccion via `DATABASE_URL`.
- SQLite como fallback local para desarrollo y pruebas.
- CSS/JS liviano sin SPA.
- Gunicorn + Nginx recomendado para Ubuntu.

## Arranque local

```powershell
C:\Users\malch\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

## Variables importantes

- `SECRET_KEY`: llave secreta de Django.
- `DEBUG`: `true` o `false`.
- `ALLOWED_HOSTS`: hosts separados por coma.
- `DATABASE_URL`: ejemplo `postgres://user:pass@localhost:5432/broncaserp`.
- `CSRF_TRUSTED_ORIGINS`: origenes separados por coma.

## Flujo inicial

1. Crear superusuario.
2. Entrar a `/proveedor/`.
3. Crear negocio/cliente nuevo con su usuario dueno.
4. Entrar con el usuario del negocio y operar POS, inventario y creditos.

