import os
from openai import OpenAI
import json

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
당신은 DevOps + Kubernetes 분석 전문가입니다.

사용자 요청을 아래 중 하나로 분류하세요:
- cicd
- k8s_api
- unknown

JSON으로만 출력:
{
  "action_type": "...",
  "summary": "...",
  "confidence": 0.0~1.0,
  "details": { ... }
}
"""

def ask_gpt_for_classification(text: str):
    result = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text}
        ],
        response_format={"type": "json_object"}
    )
    return json.loads(result.choices[0].message.content)
