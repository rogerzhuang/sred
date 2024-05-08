from flask import Flask, request, jsonify
import aiohttp
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Set up OpenAI API credentials
client = AsyncOpenAI()

async def process_prompt(prompt):
    chat_completion = await client.chat.completions.create(
        messages=[
            {"role": "system", "content": "You are an assistant that provides numerical outputs."},
            {"role": "user", "content": prompt}
        ],
        model="gpt-4-turbo",
    )
    output = chat_completion.choices[0].message.content.strip()
    try:
        print(output)
        number = float(output)
        return number
    except ValueError:
        return None

@app.route("/process", methods=["POST"])
async def process():
    prompt = request.json["prompt"]
    number = await process_prompt(prompt)
    if number is not None:
        return jsonify({"number": number})
    else:
        return jsonify({"error": "Invalid output format"}), 400

if __name__ == "__main__":
    app.run()