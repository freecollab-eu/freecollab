# FreeCollab

Open source collaboration SDK. Python backend, React frontend.

## Principles

1. Clean code over clever code
2. Security by default
3. Self-hostable with one command
4. No vendor lock-in

## Stack

- FastAPI + SQLModel + PostgreSQL
- python-socketio for real-time
- PyNaCl for E2E encryption
- React + TypeScript frontend
- Docker for deployment

## Python Style

Use modern Python (3.11+). Keep it simple.

### Do:
- Type hints everywhere
- Pydantic for validation
- Early returns
- Context managers for resources
- Async where it matters

### Don't:
- No bare except
- No mutable defaults
- No global state
- No nested ifs

## Security Rules

- Hash passwords with bcrypt
- JWT for stateless auth
- Validate all input with Pydantic
- Encrypt messages with PyNaCl
- Check channel membership on every request
- No secrets in code or logs
- Use parameterized queries (SQLModel handles this)

## Run Locally

```bash
# Start services
docker-compose up -d

# Or without Docker:
cd backend
pip install -r requirements.txt
uvicorn app.main:socket_app --reload
```

## API Docs

FastAPI auto-generates docs at:
- Swagger: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
