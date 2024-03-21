from neurons.miners.agent_artificial import AgentArtificial
from random import randint


artificial = AgentArtificial()


async def test(prompt):
    result = await call_openai(
        messages =[
            {
                "role": "system",
                "content": "You are a helpful assistant.",
            },
            {
                "role": "assistant",
                "content": "Hi there! How can I help you today?"
            },
            {
                "role": "user",
                "content": f"{prompt}",
            }
        ],
        temperature = 0.2,
        model = artificial.model,
        seed = randint(1, 1000000),
        response_format={"type": "json_object"},
    )
    full_response = ""
    for key in result:
        print(key, result[key])
        full_response += result[key]

    return full_response


if __name__ == "__main__":
    prompt = input("prompt: ")
    print(test(prompt))