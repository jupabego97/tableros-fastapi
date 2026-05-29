release: echo "Migrations run at web startup (see backend/scripts/run_migrations.py)"
web: cd backend && python scripts/run_migrations.py && uvicorn app.socket_app:socket_app --host 0.0.0.0 --port $PORT
