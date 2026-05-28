release: cd backend && python scripts/run_migrations.py
web: cd backend && uvicorn app.socket_app:socket_app --host 0.0.0.0 --port $PORT
