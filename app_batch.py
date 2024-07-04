import os
from flask import Flask, request, jsonify
from openai import OpenAI, AsyncOpenAI
import time
import json
from celery.exceptions import CeleryError
from celery.result import AsyncResult
from celery.states import PENDING, SUCCESS, FAILURE, RETRY
from dotenv import load_dotenv
from celery_config import make_celery
import numpy as np
import asyncio
from minio import Minio
from minio.error import S3Error
import io
import logging

load_dotenv()

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load OpenAI API keys from environment variables
api_keys = os.getenv('OPENAI_API_KEYS').split(',')

# Configure RabbitMQ URL
rabbitmq_username = os.getenv('RABBITMQ_ACCESS_KEY')
rabbitmq_password = os.getenv('RABBITMQ_SECRET_KEY')
rabbitmq_host = os.getenv('RABBITMQ_HOST', 'localhost')
rabbitmq_url = f'amqp://{rabbitmq_username}:{rabbitmq_password}@{rabbitmq_host}:5672/bproc'

# Configure Redis Cluster nodes
redis_cluster = os.getenv('REDIS_CLUSTER', 'localhost')
redis_settings = {'startup_nodes': [{"host": redis_cluster, "port": '6379'}]}

# Configure Celery
app.config.update(
    CELERY_BROKER_URL=rabbitmq_url,
    CELERY_REDIS_CLUSTER_SETTINGS=redis_settings,
)
celery = make_celery(app)

# Initialize MinIO client
minio_url = os.getenv('MINIO_URL', 'localhost:9000')
minio_client = Minio(
    minio_url,
    access_key=os.getenv('MINIO_ACCESS_KEY'),
    secret_key=os.getenv('MINIO_SECRET_KEY'),
    secure=False
)

BATCH_SIZE = 50000  # Number of requests to batch together
POLL_INTERVAL = 10  # Time in seconds to wait between polling
MAX_RETRY_ATTEMPTS = 1  # Maximum number of retry attempts for failed requests
MAX_API_KEY_RETRY = len(api_keys)  # Maximum retry attempts for different API keys

@celery.task(name='app_batch.process_batch', bind=True)
def process_batch(self, batch_data, endpoint, api_key_index=0, retry_count=0):
    logger.info(f"Result backend: {self.app.conf.result_backend}")
    logger.info(f"Starting process_batch with {len(batch_data)} items, endpoint: {endpoint}")
    current_api_key = api_keys[api_key_index]
    client = OpenAI(api_key=current_api_key)
    logger.info(f"Using API Key: {current_api_key[:5]}...{current_api_key[-5:]}")

    try:
        task_id = self.request.id
        batch_input_file_id = upload_batch_file(client, batch_data, task_id)
        logger.info(f"Batch input file ID: {batch_input_file_id}")
        batch_id = create_batch(client, batch_input_file_id, endpoint)
        logger.info(f"Batch ID: {batch_id}")

        while True:
            status_response = get_batch_status(client, batch_id)
            logger.info(f"Batch status: {status_response.status}")
            status = status_response.status
            if status == 'completed':
                break
            elif status in ['failed', 'expired']:
                error_code = status_response.errors.data[0].code if status_response.errors else 'unknown_error'
                logger.error(f"Batch processing failed with status: {status}, error code: {error_code}")
                if error_code == 'token_limit_exceeded' and api_key_index + 1 < MAX_API_KEY_RETRY:
                    logger.info("Rate limit reached. Retrying with next API key.")
                    return process_batch(batch_data, endpoint, api_key_index=api_key_index+1, retry_count=retry_count)
                raise CeleryError(f'Batch processing failed with status: {status}')
            time.sleep(POLL_INTERVAL)

        results, errors = get_batch_results(client, batch_id)
        logger.info(f"Batch processing completed. Results count: {len(results)}, Errors count: {len(errors)}")

        if errors and retry_count < MAX_RETRY_ATTEMPTS:
            failed_requests = [item['custom_id'] for item in errors if item.get('response', {}).get('status_code') == 400]
            if failed_requests:
                logger.info(f"Retrying {len(failed_requests)} failed requests")
                failed_data = [item for item in batch_data if item['custom_id'] in failed_requests]
                retry_results = process_batch(failed_data, endpoint, api_key_index=api_key_index, retry_count=retry_count+1)
                results.extend(retry_results.get('results', []))

        # Explicitly update task state
        self.update_state(state='SUCCESS', meta={'results': results})
        return {'results': results}
    except Exception as e:
        logger.exception(f"Error in process_batch: {str(e)}")
        # Explicitly update task state on failure
        self.update_state(state='FAILURE', meta={'error': str(e)})
        raise

def upload_batch_file(client, batch_data, task_id):
    print("Uploading batch file with data: ", batch_data)
    try:
        # Ensure the bucket exists
        bucket_name = "batch-inputs"
        if not minio_client.bucket_exists(bucket_name):
            minio_client.make_bucket(bucket_name)

        # Create a .jsonl file for the batch requests
        file_name = f'batch_input_{task_id}.jsonl'
        file_data = io.BytesIO()
        for item in batch_data:
            file_data.write((json.dumps(item) + '\n').encode('utf-8'))
        file_data.seek(0)
        
        # Upload the batch file to MinIO
        minio_client.put_object(
            "batch-inputs",
            file_name,
            file_data,
            length=file_data.getbuffer().nbytes,
            content_type="application/jsonl"
        )
        
        # Upload the batch file to OpenAI
        batch_input_file = client.files.create(
            file=file_data,
            purpose="batch"
        )
        return batch_input_file.id
    except S3Error as e:
        print("Error in upload_batch_file: ", str(e))
        raise e

def create_batch(client, batch_input_file_id, endpoint):
    print("Creating batch with input file ID: ", batch_input_file_id)
    try:
        # Create the batch using the uploaded file ID
        batch = client.batches.create(
            input_file_id=batch_input_file_id,
            endpoint=endpoint,
            completion_window="24h",
            metadata={"description": "batch processing job"}
        )
        print("Batch created with ID: ", batch.id)
        return batch.id
    except Exception as e:
        print("Error in create_batch: ", str(e))
        raise e

def get_batch_status(client, batch_id):
    print("Retrieving batch status for batch ID: ", batch_id)
    try:
        # Retrieve the status of the batch
        return client.batches.retrieve(batch_id)
    except Exception as e:
        print("Error in get_batch_status: ", str(e))
        raise e

def get_batch_results(client, batch_id):
    print("Retrieving batch results for batch ID: ", batch_id)
    try:
        # Retrieve the results of the batch
        batch = client.batches.retrieve(batch_id)
        output_file_id = batch.output_file_id
        error_file_id = batch.error_file_id

        results = []
        errors = []

        if output_file_id:
            output_response = client.files.content(output_file_id)
            output_content = output_response.read().decode('utf-8')
            results = [json.loads(line) for line in output_content.splitlines()]

        if error_file_id:
            error_response = client.files.content(error_file_id)
            error_content = error_response.read().decode('utf-8')
            errors = [json.loads(line) for line in error_content.splitlines()]

        return results, errors
    except Exception as e:
        print("Error in get_batch_results: ", str(e))
        raise e

@app.route('/bproc', methods=['POST'])
def bproc():
    logger.info("Received request to /bproc")
    data = request.get_json()
    batch = data.get('batch', [])
    endpoint = data.get('endpoint', '/v1/chat/completions')  # Default to completions endpoint
    logger.info(f"Batch size: {batch}, Endpoint: {endpoint}")
    if not batch:
        return jsonify({'error': 'Batch data is required'}), 400
    
    # Check if batch size exceeds the limit
    if len(batch) > BATCH_SIZE:
        return jsonify({'error': f'Batch size exceeds the limit of {BATCH_SIZE}'}), 400

    try:
        task = process_batch.apply_async(args=[batch, endpoint])
        print("Task ID: ", task.id)
        return jsonify({'job_id': task.id}), 202
    except Exception as e:
        print("Error: ", e)
        return jsonify({'error': str(e)}), 500

@app.route('/bstatus/<job_id>', methods=['GET'])
def job_status(job_id):
    logger.info(f"Checking status for job: {job_id}")
    try:
        task = AsyncResult(job_id, app=celery)
        logger.info(f"Raw task state: {task.state}")
        logger.info(f"Task result: {task.result}")
        
        if task.state == PENDING:
            response = {
                'state': 'PENDING',
                'status': 'Task is pending or not found'
            }
        elif task.state == SUCCESS:
            response = {
                'state': 'SUCCESS',
                'status': 'Task has completed successfully',
                'result': task.result
            }
        elif task.state == FAILURE:
            response = {
                'state': 'FAILURE',
                'status': 'Task has failed',
                'error': str(task.result) if task.result else 'Unknown error'
            }
        elif task.state == RETRY:
            response = {
                'state': 'RETRY',
                'status': 'Task is being retried'
            }
        else:
            response = {
                'state': task.state,
                'status': 'Task is in progress or in an unknown state'
            }
        
        logger.info(f"Returning response: {response}")
        return jsonify(response)
    except Exception as e:
        logger.exception(f"Error checking task status: {str(e)}")
        return jsonify({'state': 'ERROR', 'status': 'Error checking task status'}), 500

@app.route('/')
def health_check():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
