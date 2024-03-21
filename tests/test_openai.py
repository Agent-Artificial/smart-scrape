import asyncio
import os
import traceback
from openai import OpenAI
from openai import AsyncOpenAI
from neurons.miners.agent_artificial import AgentArtificial

artificial = AgentArtificial()


client = AsyncOpenAI(timeout=30, api_key=artificial.api_key, base_url=artificial.base_url)

async def send_openai_request(prompt, engine=artificial.model):
    try:
        stream = await client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            model=engine,
            seed=1234,
            temperature=0.0001,
        )
        collected_messages = []

        async for part in stream:
            print(part.choices[0].delta.content or "")
            collected_messages.append(part.choices[0].delta.content or "")

        all_messages = ''.join(collected_messages)
        return all_messages

    except Exception as e:
        print(f"Got exception when calling openai {e}")
        traceback.print_exc()
        return "Error calling model"

async def main():
    prompts = ["count to 10", "tell me a joke"]
    tasks = [send_openai_request(prompt) for prompt in prompts]

    responses = await asyncio.gather(*tasks)
    for response in responses:
        print(response)

asyncio.run(main())
