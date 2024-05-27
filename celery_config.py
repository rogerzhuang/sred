from celery import Celery

def make_celery(app):
    celery = Celery(
        app.import_name,
        backend=app.config['CELERY_RESULT_BACKEND'],
        broker=app.config['CELERY_BROKER_URL'],
        include=['app_batch']  # Ensure this matches the module name
    )
    celery.conf.update(app.config)
    return celery
