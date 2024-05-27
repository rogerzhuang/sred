web: gunicorn app_batch:app
worker: celery -A app_batch.celery worker --loglevel=info -P eventlet