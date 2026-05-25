# Repository Guidelines

## Project Structure & Module Organization
`broncaserp/` contains the Django project configuration (`settings.py`, `urls.py`, `wsgi.py`, `asgi.py`). The main app logic lives in `erp/`, including `models.py`, `forms.py`, `views.py`, `services.py`, `middleware.py`, `context_processors.py`, `signals.py`, `admin.py`, `urls.py`, and custom template tags under `erp/templatetags/`.

Server-rendered templates live in `templates/`, with feature groupings such as `templates/erp/`, `templates/provider/`, and `templates/registration/`. Shared layout pieces live in `templates/base.html`. Frontend assets are under `static/`, including CSS in `static/css/`, JavaScript in `static/js/`, icons in `static/icons/`, and PWA assets like `static/manifest.webmanifest` and `static/service-worker.js`. Deployment samples are in `deploy/`.

## Build, Test, and Development Commands
Create a local environment and install dependencies:
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run the app locally:
```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

Run tests:
```powershell
.\.venv\Scripts\python.exe manage.py test
```

Collect static files before production deploys:
```powershell
.\.venv\Scripts\python.exe manage.py collectstatic
```

## Coding Style & Naming Conventions
Follow existing Django and PEP 8 conventions: 4-space indentation, `snake_case` for functions and variables, `PascalCase` for models/forms/classes, and uppercase for settings constants. Keep views thin and place business rules in `erp/services.py` or model methods. Match template names to the feature being rendered, for example `templates/erp/inventory.html` or `templates/provider/dashboard.html`. Keep JavaScript modules small and feature-focused, following the existing `static/js/` structure.

## Testing Guidelines
This repo uses Django’s built-in test runner and `TestCase`. Current tests live in `erp/tests.py`; add new tests near the related domain behavior and name methods `test_<expected_behavior>()`. Cover sales, inventory, membership scoping, credits, and cash flows when changing business logic. Prefer deterministic fixtures created in `setUp()` over shared external state.

## Commit & Pull Request Guidelines
Recent history uses short, imperative commit subjects such as `Implement initial Broncas ERP MVP`. Keep commit messages concise, focused, and behavior-oriented. For pull requests, include a summary of user-visible changes, note any migrations or environment variable changes, link the issue if there is one, and attach screenshots for template or CSS updates.

## Security & Configuration Tips
Do not commit real secrets or `.env` contents. Configure `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL`, and `CSRF_TRUSTED_ORIGINS` through environment variables. SQLite is the local fallback; production is expected to use PostgreSQL behind Gunicorn and Nginx.
