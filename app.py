# from flask import Flask, request, jsonify
# import aiohttp
# import asyncio
# from openai import AsyncOpenAI
# from dotenv import load_dotenv

# load_dotenv()

# app = Flask(__name__)

# # Set up OpenAI API credentials
# client = AsyncOpenAI()

# async def process_prompt(prompt):
#     chat_completion = await client.chat.completions.create(
#         messages=[
#             {"role": "system", "content": "You are an assistant that provides numerical outputs."},
#             {"role": "user", "content": prompt}
#         ],
#         model="gpt-4-turbo",
#     )
#     output = chat_completion.choices[0].message.content.strip()
#     try:
#         print(output)
#         number = float(output)
#         return number
#     except ValueError:
#         return None

# @app.route("/process", methods=["POST"])
# async def process():
#     prompt = request.json["prompt"]
#     number = await process_prompt(prompt)
#     if number is not None:
#         return jsonify({"number": number})
#     else:
#         return jsonify({"error": "Invalid output format"}), 400

# if __name__ == "__main__":
#     app.run()


from flask import Flask, jsonify
from celery_utils import celery_init_app
import os

app = Flask(__name__)
app.config.update(
    CELERY=dict(
        broker_url="redis://localhost:6379/0",
        result_backend="redis://localhost:6379/0",
        task_ignore_result=False,
    )
)

celery_app = celery_init_app(app)

@app.route('/test_task', methods=['GET'])
def trigger_test_task():
    try:
        task = celery_app.send_task('tasks.test_task')
        print("Test Task ID: ", task.id)
        return jsonify({'job_id': task.id}), 202
    except Exception as e:
        print("Error: ", e)
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

