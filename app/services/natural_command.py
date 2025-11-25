import os
import json
from openai import OpenAI
import openai

OPENAI_API_KEY = "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"#os.environ.get("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("환경변수 OPENAI_API_KEY 가 설정되지 않았습니다. K8s secretMount 확인 필요")

print(OPENAI_API_KEY)

client = OpenAI(api_key=OPENAI_API_KEY)

async def parse_command(text: str):
    """
    GPT에게 자연어 명령을 분석시키고 'action' 값을 반환
    """
    system_prompt = """
    너는 Kubernetes, DevOps, GitOps 컨텍스트를 이해하는 분석기다.

    사용자의 자연어 명령을 다음 중 하나로 분류하라:
    - deploy_repo          : 깃허브 레포를 CI/CD로 배포하려는 의도
    - setup_test_server    : 테스트 서버를 새로 구축하려는 의도
    - unknown              : 둘 중 무엇인지 확실하지 않은 경우

    반드시 JSON만 반환하라. 예: {"action":"deploy_repo"}
    """

    user_prompt = f"사용자 입력: {text}"

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        max_tokens=200
    )

    raw_output = response.choices[0].message.content.strip()

    # JSON 형태 파싱
    try:
        return json.loads(raw_output)
    except json.JSONDecodeError:
        return {"action": "unknown", "raw": raw_output}