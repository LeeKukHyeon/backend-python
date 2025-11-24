import os
import openai

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

def get_deployment_commands(natural_text: str) -> str:
    """
    GPT API 호출 -> 자연어를 CI/CD 및 배포 명령으로 변환
    """
    prompt = f"Convert the following natural language into deployment commands:\n{natural_text}"

    response = openai.Completion.create(
        model="text-davinci-003",
        prompt=prompt,
        max_tokens=200
    )

    return response.choices[0].text.strip()
