import json
import re

from openai import OpenAI

from core.config import settings
from schema.citation_analysis_schema import CitationAnalysisRequest
from services.ai_provider_services import PROVIDER_MODELS, fetch_answers_from_all_providers
from services.AI_visibility_scervices import (
    MAX_CITATION_SCORE,
    _all_answer_records,
    _business_name_mentioned,
)
from services.logger_services import logger

_VALIDATOR_MODEL = "gpt-4o-mini"
_MIN_QUERY_LENGTH = 8
_MAX_QUERY_LENGTH = 300

_GREETING_OR_FILLER = {
    "hi",
    "hello",
    "hey",
    "yo",
    "sup",
    "hiya",
    "howdy",
    "thanks",
    "thank you",
    "thankyou",
    "ok",
    "okay",
    "k",
    "test",
    "testing",
    "asdf",
    "qwerty",
    "how are you",
    "how r you",
    "whats up",
    "what's up",
    "good morning",
    "good evening",
    "good night",
    "bye",
    "goodbye",
    "lol",
    "haha",
    "please",
    "help",
    "who are you",
    "what can you do",
}

_VALIDATOR_SYSTEM_PROMPT = (
    "You are a query quality checker for local business citation analysis. "
    "Decide whether each string is a real business-discovery / search query "
    "for the given business type and location. "
    "Reject greetings, chit-chat, spam, nonsense, jokes, and queries unrelated "
    "to finding or comparing businesses. "
    "Return only valid JSON."
)


def _normalize_for_rules(text: str) -> str:
    cleaned = re.sub(r"[^\w\s']", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _rule_based_query_check(query: str, index: int) -> dict | None:
    """
    Fast local rejection. Returns an invalid result dict, or None if rules pass.
    """
    text = query.strip()
    normalized = _normalize_for_rules(text)
    words = normalized.split() if normalized else []

    if len(text) < _MIN_QUERY_LENGTH:
        return {
            "index": index,
            "query": text,
            "valid": False,
            "reason": f"Too short to be a useful search query (min {_MIN_QUERY_LENGTH} characters)",
            "checked_by": "rules",
        }

    if len(text) > _MAX_QUERY_LENGTH:
        return {
            "index": index,
            "query": text,
            "valid": False,
            "reason": f"Too long (max {_MAX_QUERY_LENGTH} characters)",
            "checked_by": "rules",
        }

    if not re.search(r"[a-zA-Z]", text):
        return {
            "index": index,
            "query": text,
            "valid": False,
            "reason": "Must contain letters",
            "checked_by": "rules",
        }

    if normalized in _GREETING_OR_FILLER:
        return {
            "index": index,
            "query": text,
            "valid": False,
            "reason": "Greeting or filler text, not a business search query",
            "checked_by": "rules",
        }

    if words and all(word in _GREETING_OR_FILLER for word in words) and len(words) <= 4:
        return {
            "index": index,
            "query": text,
            "valid": False,
            "reason": "Greeting or filler text, not a business search query",
            "checked_by": "rules",
        }

    return None


def _build_validator_prompt(
    questions: list[str],
    business_type: str,
    business_loc: str,
) -> str:
    numbered = "\n".join(
        f"{index}. {question}"
        for index, question in enumerate(questions, start=1)
    )
    return f"""Validate these custom search queries for citation analysis.

Business type: {business_type}
Business location: {business_loc}

A query is VALID only if a real person might use it to discover, compare, or ask for recommendations of {business_type} businesses in or near {business_loc} (or a clearly related local search).

INVALID examples: "hi", "hello", "thanks", "how are you", jokes, spam, gibberish, off-topic chat, empty meaning.

VALID examples: "Best {business_type} in {business_loc}?", "Where should I stay near the city centre?", "Recommend a good {business_type} for a weekend trip".

Return JSON in this exact shape:
{{
  "all_valid": true,
  "results": [
    {{
      "index": 1,
      "query": "original query text",
      "valid": true,
      "reason": "short reason"
    }}
  ]
}}

Include one result for every query from 1 to {len(questions)}. Set all_valid to true only if every query is valid.

Queries:
{numbered}"""


def _parse_validator_response(raw_text: str, questions: list[str]) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Validator did not return a JSON object")

    payload = json.loads(cleaned[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Validator JSON root must be an object")

    raw_results = payload.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise ValueError("Validator response missing results list")

    by_index: dict[int, dict] = {}
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(questions):
            by_index[index] = item

    if len(by_index) != len(questions):
        raise ValueError(
            f"Validator returned {len(by_index)} results, expected {len(questions)}"
        )

    results = []
    for index, question in enumerate(questions, start=1):
        item = by_index[index]
        valid = bool(item.get("valid"))
        reason = str(item.get("reason") or "").strip() or (
            "Valid business discovery query" if valid else "Not a valid business search query"
        )
        results.append({
            "index": index,
            "query": question,
            "valid": valid,
            "reason": reason,
            "checked_by": "ai",
        })

    all_valid = all(item["valid"] for item in results)
    return {
        "all_valid": all_valid,
        "results": results,
        "validator_model": _VALIDATOR_MODEL,
    }


def _validate_queries_with_ai(
    questions: list[str],
    business_type: str,
    business_loc: str,
) -> dict:
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    prompt = _build_validator_prompt(questions, business_type, business_loc)

    logger.info(
        "citation_analysis validator: calling "
        f"model='{_VALIDATOR_MODEL}' for {len(questions)} query(ies)"
    )

    response = client.chat.completions.create(
        model=_VALIDATOR_MODEL,
        messages=[
            {"role": "system", "content": _VALIDATOR_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    raw = (response.choices[0].message.content or "").strip()
    if not raw:
        raise ValueError("Validator returned empty content")

    return _parse_validator_response(raw, questions)


def validate_custom_questions(
    questions: list[str],
    business_type: str,
    business_loc: str,
) -> dict:
    """
    Hybrid validation: cheap local rules first, then one gpt-4o-mini call
    for remaining queries. Fail closed if the AI validator errors.
    """
    cleaned_questions = [q.strip() for q in questions if q and str(q).strip()]
    if not cleaned_questions:
        return {
            "all_valid": False,
            "results": [{
                "index": 1,
                "query": "",
                "valid": False,
                "reason": "custom_questions must contain at least one non-empty question",
                "checked_by": "rules",
            }],
            "validator_model": None,
        }

    rule_results: dict[int, dict] = {}
    pending: list[tuple[int, str]] = []

    for index, question in enumerate(cleaned_questions, start=1):
        rejected = _rule_based_query_check(question, index)
        if rejected is not None:
            rule_results[index] = rejected
        else:
            pending.append((index, question))

    ai_by_index: dict[int, dict] = {}
    validator_model = None

    if pending:
        pending_texts = [text for _, text in pending]
        try:
            ai_payload = _validate_queries_with_ai(
                pending_texts,
                business_type.strip(),
                business_loc.strip(),
            )
            validator_model = ai_payload.get("validator_model")
            for local_index, (original_index, original_text) in enumerate(pending, start=1):
                item = next(
                    (
                        result
                        for result in ai_payload["results"]
                        if result["index"] == local_index
                    ),
                    None,
                )
                if item is None:
                    raise ValueError("AI validator result missing for a pending query")
                ai_by_index[original_index] = {
                    "index": original_index,
                    "query": original_text,
                    "valid": bool(item["valid"]),
                    "reason": item["reason"],
                    "checked_by": "ai",
                }
        except Exception as exc:
            logger.exception(
                f"citation_analysis validator: AI validation failed — {exc}"
            )
            # Fail closed: do not start expensive multi-provider analysis.
            failed_results = [
                {
                    "index": index,
                    "query": text,
                    "valid": False,
                    "reason": (
                        "Query validation service unavailable; "
                        "analysis was not started"
                    ),
                    "checked_by": "ai_error",
                }
                for index, text in pending
            ] + list(rule_results.values())
            failed_results.sort(key=lambda item: item["index"])
            return {
                "all_valid": False,
                "results": failed_results,
                "validator_model": None,
                "validation_error": str(exc),
            }

    merged = []
    for index, question in enumerate(cleaned_questions, start=1):
        if index in rule_results:
            merged.append(rule_results[index])
        else:
            merged.append(ai_by_index[index])

    merged.sort(key=lambda item: item["index"])
    all_valid = all(item["valid"] for item in merged)

    logger.info(
        "citation_analysis validator: completed — "
        f"all_valid={all_valid}, total={len(merged)}, "
        f"rules_rejected={len(rule_results)}, ai_checked={len(ai_by_index)}"
    )

    return {
        "all_valid": all_valid,
        "results": merged,
        "validator_model": validator_model,
    }


def _calculate_citation_score(
    business_name: str,
    answer_records: list[dict],
) -> dict:
    total_answers = len(answer_records)
    mentioned_in = []
    provider_mentions = {provider: 0 for provider in PROVIDER_MODELS}

    for record in answer_records:
        mentioned = any(
            _business_name_mentioned(business_name, name)
            for name in record["businesses"]
        )
        if not mentioned:
            continue

        provider = record["provider"]
        provider_mentions[provider] += 1
        mentioned_in.append({
            "provider": provider,
            "question_number": record["question_number"],
            "question": record["question"],
            "Answer": ", ".join(record["businesses"]),
        })

    mention_count = len(mentioned_in)
    if total_answers == 0:
        score = 0.0
        percentage = 0.0
    else:
        score = (mention_count / total_answers) * MAX_CITATION_SCORE
        percentage = (mention_count / total_answers) * 100

    return {
        "mentions": mention_count,
        "total_answers": total_answers,
        "total_questions": total_answers,
        "max_score": MAX_CITATION_SCORE,
        "score": round(score, 2),
        "percentage": round(percentage),
        "mentions_by_provider": provider_mentions,
        "mentioned_in": mentioned_in,
    }


def run_citation_analysis(payload: CitationAnalysisRequest) -> dict:
    business_name = payload.business_name.strip()
    business_id = payload.business_id
    business_type = payload.business_type.strip()
    business_loc = payload.business_loc.strip()
    questions = list(payload.custom_questions)

    logger.info(
        "citation_analysis: started — "
        f"business='{business_name}', business_id='{business_id}', "
        f"type='{business_type}', loc='{business_loc}', "
        f"questions={len(questions)}"
    )

    provider_results = fetch_answers_from_all_providers(
        business_type,
        business_loc,
        questions,
    )
    answer_records = _all_answer_records(provider_results, questions)
    citation_score = _calculate_citation_score(business_name, answer_records)

    successful_providers = sum(
        result["status"] == "success"
        for result in provider_results.values()
    )
    analysis_status = (
        "success"
        if successful_providers == len(PROVIDER_MODELS)
        else "partial"
    )

    logger.info(
        "citation_analysis: completed — "
        f"status={analysis_status}, "
        f"mentions={citation_score['mentions']}/{citation_score['total_answers']}, "
        f"score={citation_score['score']}/{MAX_CITATION_SCORE}"
    )

    return {
        "status": analysis_status,
        "business_name": business_name,
        "business_id": business_id,
        "business_type": business_type,
        "business_location": business_loc,
        "questions": questions,
        "answers": provider_results,
        "successful_providers": successful_providers,
        "total_providers": len(PROVIDER_MODELS),
        "citation_score": citation_score,
    }
