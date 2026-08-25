import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from anthropic import Anthropic
from google import genai
from openai import OpenAI

from core.config import settings
from services.logger_services import logger


PROVIDER_MODEL_FALLBACKS = {
    "openai": ["gpt-4o-mini", "gpt-5.4-mini", "gpt-5.4-nano"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
    "anthropic": [
        "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
    ],
    "perplexity": ["sonar"],
    "llama": [
        "meta-llama/llama-3.3-70b-instruct",
        "meta-llama/llama-3.1-70b-instruct",
    ],
}

PROVIDER_MODELS = {
    provider: models[0] for provider, models in PROVIDER_MODEL_FALLBACKS.items()
}


def _build_answer_prompt(
    business_type: str,
    business_loc: str,
    questions: list[str],
) -> str:
    numbered_questions = "\n".join(
        f"{number}. {question}"
        for number, question in enumerate(questions, start=1)
    )

    return f"""Answer these 10 questions about {business_type}s in {business_loc}.

For every question, recommend 9-10 real, specific business names in ranked order.
Do not add descriptions, links, prices, addresses, or explanations.

Return only valid JSON in this exact shape:
{{
  "answers": [
    {{
      "question_number": 1,
      "businesses": ["First business", "Second business"]
    }}
  ]
}}

Include one item for every question from 1 to 10.

Questions:
{numbered_questions}"""


def _extract_json(raw_text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("The model did not return a JSON object")

    return json.loads(cleaned[start:end + 1])


def _normalize_answers(raw_text: str, questions: list[str]) -> list[dict]:
    payload = _extract_json(raw_text)
    raw_answers = payload.get("answers")

    if not isinstance(raw_answers, list):
        raise ValueError("The model response does not contain an answers list")

    answers_by_number: dict[int, list[str]] = {}

    for item in raw_answers:
        if not isinstance(item, dict):
            continue

        try:
            question_number = int(item.get("question_number"))
        except (TypeError, ValueError):
            continue

        businesses = item.get("businesses", [])
        if not isinstance(businesses, list):
            continue

        clean_names = [
            str(name).strip()
            for name in businesses
            if str(name).strip()
        ]

        if 1 <= question_number <= len(questions):
            answers_by_number[question_number] = clean_names

    return [
        {
            "question_number": number,
            "question": question,
            "businesses": answers_by_number.get(number, []),
        }
        for number, question in enumerate(questions, start=1)
    ]


def _fetch_openai(prompt: str, model: str) -> str:
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a local business recommendation assistant.",
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def _fetch_gemini(prompt: str, model: str) -> str:
    client = genai.Client(api_key=settings.Gemini_API_KEY)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
    )
    return response.text or ""


def _fetch_anthropic(prompt: str, model: str) -> str:
    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=model,
        max_tokens=4000,
        system="You are a local business recommendation assistant.",
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text
        for block in response.content
        if getattr(block, "type", "") == "text"
    )


def _fetch_perplexity(prompt: str, model: str) -> str:
    client = OpenAI(
        api_key=settings.Perplexity_API,
        base_url="https://api.perplexity.ai",
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a local business recommendation assistant.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content or ""


def _fetch_llama(prompt: str, model: str) -> str:
    client = OpenAI(
        api_key=settings.OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://palapal.ai",
            "X-Title": "Palapalai",
        },
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a local business recommendation assistant.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content or ""


PROVIDER_FETCHERS = {
    "openai": _fetch_openai,
    "gemini": _fetch_gemini,
    "anthropic": _fetch_anthropic,
    "perplexity": _fetch_perplexity,
    "llama": _fetch_llama,
}


def _log_provider_answers(provider: str, result: dict) -> None:
    provider_name = provider.upper()
    model = result["model"]

    logger.info(f"ai_visibility: {'=' * 60}")
    logger.info(
        f"ai_visibility: ANSWERS FROM {provider_name} — model='{model}'"
    )
    logger.info(f"ai_visibility: {'=' * 60}")

    if result["status"] != "success":
        logger.error(
            f"ai_visibility: {provider_name} did not return answers — "
            f"{result.get('error', 'Unknown error')}"
        )
        return

    for answer in result["answers"]:
        question_number = answer["question_number"]
        businesses = ", ".join(answer["businesses"])
        logger.info(
            f"ai_visibility: [{provider_name}] Q{question_number}: "
            f"{answer['question']}"
        )
        logger.info(
            f"ai_visibility: [{provider_name}] A{question_number}: "
            f"{businesses}"
        )


def _fetch_provider_answers(
    provider: str,
    prompt: str,
    questions: list[str],
) -> dict:
    models = PROVIDER_MODEL_FALLBACKS[provider]
    last_error: Exception | None = None

    for model_index, model in enumerate(models):
        logger.info(f"ai_visibility: calling {provider} model='{model}'")

        for attempt in range(1, 3):
            try:
                raw_text = PROVIDER_FETCHERS[provider](prompt, model)
                answers = _normalize_answers(raw_text, questions)
                answered_count = sum(bool(item["businesses"]) for item in answers)

                if answered_count != len(questions):
                    raise ValueError(
                        f"Expected {len(questions)} answers, "
                        f"but received {answered_count}"
                    )

                logger.info(
                    f"ai_visibility: {provider} model='{model}' returned "
                    f"{answered_count}/{len(questions)} answers"
                )
                return {
                    "status": "success",
                    "model": model,
                    "answers": answers,
                }
            except Exception as exc:
                last_error = exc
                if attempt == 1:
                    logger.warning(
                        f"ai_visibility: {provider} model='{model}' attempt 1 failed — "
                        f"{exc}; retrying once"
                    )
                    continue
                break

        if model_index < len(models) - 1:
            next_model = models[model_index + 1]
            logger.warning(
                f"ai_visibility: {provider} model='{model}' failed — {last_error}; "
                f"trying fallback model '{next_model}'"
            )

    logger.exception(
        f"ai_visibility: {provider} answer fetch failed after all model fallbacks — "
        f"{last_error}"
    )
    return {
        "status": "error",
        "model": models[-1],
        "answers": [],
        "error": str(last_error),
    }


def fetch_answers_from_all_providers(
    business_type: str,
    business_loc: str,
    questions: list[str],
) -> dict[str, dict]:
    prompt = _build_answer_prompt(business_type, business_loc, questions)
    results: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=len(PROVIDER_FETCHERS)) as executor:
        future_to_provider = {
            executor.submit(
                _fetch_provider_answers,
                provider,
                prompt,
                questions,
            ): provider
            for provider in PROVIDER_FETCHERS
        }

        for future in as_completed(future_to_provider):
            provider = future_to_provider[future]
            results[provider] = future.result()

    for provider in PROVIDER_FETCHERS:
        _log_provider_answers(provider, results[provider])

    return {
        provider: results[provider]
        for provider in PROVIDER_FETCHERS
    }
