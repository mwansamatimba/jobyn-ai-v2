import asyncio

from backend.ai.llm import generate_text


async def main() -> None:
    print("=" * 60)
    print("JOBYN AI - NVIDIA NIM LIVE SMOKE TEST")
    print("=" * 60)

    response = await generate_text(
        """
        You are testing the Jobyn AI backend.

        Explain in exactly three short sentences what an
        AI-powered job matching platform does.
        """,
        temperature=0.2,
        max_tokens=300,
    )

    print("\nMODEL:")
    print("z-ai/glm-5.2")

    print("\nRESPONSE:")
    print(response)

    print("\n" + "=" * 60)
    print("NVIDIA NIM SMOKE TEST PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())