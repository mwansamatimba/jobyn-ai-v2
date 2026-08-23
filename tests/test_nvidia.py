import asyncio
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI


load_dotenv()


async def main():
    api_key = os.getenv("NVIDIA_API_KEY")
    base_url = os.getenv(
        "NVIDIA_BASE_URL",
        "https://integrate.api.nvidia.com/v1",
    )
    model = os.getenv(
        "NVIDIA_MODEL",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
    )

    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY is not configured.")

    print(f"Base URL: {base_url}")
    print(f"Model: {model}")
    print("Sending test request...")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
    )

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a resume parsing assistant. "
                    "Respond concisely."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Confirm that the Jobyn AI resume parser "
                    "is connected. Reply with exactly: "
                    "JOBYN_RESUME_PARSER_OK"
                ),
            },
        ],
        temperature=0.1,
        max_tokens=50,
        stream=False,
    )

    print("\nMODEL RESPONSE:")
    print(response.choices[0].message.content)


if __name__ == "__main__":
    asyncio.run(main())