from celery import Celery

def make_celery(app):
    celery = Celery(
        app.import_name,
        broker=app.config['CELERY_BROKER_URL'],
        include=['app_batch']  # Ensure this matches the module name
    )
    celery.conf.update(app.config)

    # Add Redis Cluster configuration
    celery.conf.update(
        CELERY_RESULT_BACKEND="celery_redis_cluster_backend.redis_cluster.RedisClusterBackend",
        CELERY_REDIS_CLUSTER_SETTINGS={
            'startup_nodes': [
                {"host": "redis-service", "port": "6379"}
            ]
        }
    )
    
    return celery
