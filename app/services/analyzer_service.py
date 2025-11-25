from app.services.llm_service import ask_gpt_for_classification
from app.models.analyze_models import AnalyzeResponse

async def analyze_text(text: str, github_url: str = None) -> AnalyzeResponse:
    raw = ask_gpt_for_classification(text)

    return AnalyzeResponse(
        action_type=raw["action_type"],
        summary=raw["summary"],
        confidence=raw["confidence"],
        details={
            "github_url": github_url,
            **raw.get("details", {})
        }
    )
