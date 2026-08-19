import asyncio
import mimetypes
from urllib.parse import unquote, urlsplit

import httpx
from google import genai
from google.genai import types

from core.config import settings
from schema.video_generate_schema import VideoGenerateRequest
from services.logger_services import logger
from services.s3_service import (
    get_video_studio_result_s3_key,
    get_video_studio_video_s3_key,
    upload_video_studio_files_to_s3,
)

VEO_MODEL = "veo-3.1-fast-generate-preview"
VIDEO_RESOLUTION = "720p"
VIDEO_ASPECT_RATIO = "16:9"
CLIP_SECONDS = 8
EXTEND_FALLBACK_SECONDS = 7
MAX_REFERENCE_IMAGES = 3
MAX_EXTEND_BEATS = 20
MAX_IMAGE_BYTES = 20 * 1024 * 1024
POLL_INTERVAL_SECONDS = 15
MAX_POLL_ATTEMPTS = 40
ALLOWED_IMAGE_MIMES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}
MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _log_section(title: str) -> None:
    logger.info(f"video_generate: {'=' * 60}")
    logger.info(f"video_generate: {title}")
    logger.info(f"video_generate: {'=' * 60}")


def _guess_image_mime(url: str, content_type: str | None) -> str:
    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        if mime in ALLOWED_IMAGE_MIMES:
            return "image/jpeg" if mime == "image/jpg" else mime
    suffix = urlsplit(unquote(url)).path.lower()
    for extension, mime in MIME_BY_SUFFIX.items():
        if suffix.endswith(extension):
            return mime
    guessed, _ = mimetypes.guess_type(unquote(url))
    if guessed in ALLOWED_IMAGE_MIMES:
        return "image/jpeg" if guessed == "image/jpg" else guessed
    raise ValueError(f"Unsupported image type for {url}")


async def _download_reference_images(image_urls: list[str]) -> list[types.Image]:
    selected_urls = image_urls[:MAX_REFERENCE_IMAGES]
    if len(image_urls) > MAX_REFERENCE_IMAGES:
        logger.warning(
            "video_generate: Veo accepts up to "
            f"{MAX_REFERENCE_IMAGES} reference images; using the first "
            f"{MAX_REFERENCE_IMAGES} of {len(image_urls)}"
        )

    images: list[types.Image] = []
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for index, url in enumerate(selected_urls, start=1):
            logger.info(f"video_generate: downloading reference image {index} — {url}")
            response = await client.get(url)
            if response.status_code >= 400:
                raise ValueError(
                    f"Failed to download image {index} ({url}): HTTP {response.status_code}"
                )
            if len(response.content) > MAX_IMAGE_BYTES:
                raise ValueError(
                    f"Image {index} exceeds the 20MB Veo limit ({url})"
                )
            mime_type = _guess_image_mime(url, response.headers.get("Content-Type"))
            images.append(
                types.Image(image_bytes=response.content, mime_type=mime_type)
            )
    return images


def _uri_only_video(video: types.Video) -> types.Video:
    if not video.uri:
        raise RuntimeError("Veo returned a video without a URI, so it cannot be extended")
    return types.Video(uri=video.uri, mime_type=video.mime_type or "video/mp4")


def _extract_generated_video(operation: types.GenerateVideosOperation) -> types.Video:
    if operation.error:
        raise RuntimeError(f"Veo operation failed: {operation.error}")

    response = operation.response or operation.result
    if response is None:
        raise RuntimeError("Veo returned no response payload")

    filtered_reasons = response.rai_media_filtered_reasons or []
    if filtered_reasons and not response.generated_videos:
        raise RuntimeError(
            "Veo filtered the video for safety: " + "; ".join(filtered_reasons)
        )

    generated = response.generated_videos or []
    if not generated or generated[0].video is None:
        raise RuntimeError("Veo returned no generated video")
    return generated[0].video


async def _wait_for_operation(
    client: genai.Client,
    operation: types.GenerateVideosOperation,
    *,
    label: str,
) -> types.Video:
    attempts = 0
    while not operation.done:
        attempts += 1
        if attempts > MAX_POLL_ATTEMPTS:
            raise TimeoutError(
                f"{label} timed out after "
                f"{MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS} seconds"
            )
        logger.info(
            f"video_generate: waiting for {label} — "
            f"attempt {attempts}/{MAX_POLL_ATTEMPTS}"
        )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        operation = await client.aio.operations.get(operation)

    logger.info(f"video_generate: {label} completed")
    return _extract_generated_video(operation)


def _base_video_config(
    *,
    duration_seconds: int,
    reference_images: list[types.VideoGenerationReferenceImage] | None = None,
) -> types.GenerateVideosConfig:
    return types.GenerateVideosConfig(
        number_of_videos=1,
        duration_seconds=duration_seconds,
        aspect_ratio=VIDEO_ASPECT_RATIO,
        resolution=VIDEO_RESOLUTION,
        person_generation="allow_adult",
        reference_images=reference_images,
    )


async def _start_intro_operation(
    client: genai.Client,
    *,
    prompt: str,
    images: list[types.Image],
    use_reference_images: bool,
) -> types.GenerateVideosOperation:
    if use_reference_images and len(images) > 1:
        reference_images = [
            types.VideoGenerationReferenceImage(
                image=image,
                reference_type=types.VideoGenerationReferenceType.ASSET,
            )
            for image in images
        ]
        return await client.aio.models.generate_videos(
            model=VEO_MODEL,
            source=types.GenerateVideosSource(prompt=prompt),
            config=_base_video_config(
                duration_seconds=CLIP_SECONDS,
                reference_images=reference_images,
            ),
        )

    return await client.aio.models.generate_videos(
        model=VEO_MODEL,
        source=types.GenerateVideosSource(prompt=prompt, image=images[0]),
        config=_base_video_config(duration_seconds=CLIP_SECONDS),
    )


async def _generate_intro_clip(
    client: genai.Client,
    *,
    prompt: str,
    images: list[types.Image],
) -> types.Video:
    use_reference_images = len(images) > 1
    try:
        operation = await _start_intro_operation(
            client,
            prompt=prompt,
            images=images,
            use_reference_images=use_reference_images,
        )
        return await _wait_for_operation(client, operation, label="intro clip")
    except Exception as exc:
        if not use_reference_images:
            raise
        logger.warning(
            "video_generate: multi-image intro failed, "
            f"retrying with the first image only — {exc}"
        )
        operation = await _start_intro_operation(
            client,
            prompt=prompt,
            images=images,
            use_reference_images=False,
        )
        return await _wait_for_operation(
            client,
            operation,
            label="intro clip (first image fallback)",
        )


def _is_duration_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "duration" in message or "duration_seconds" in message


async def _extend_clip(
    client: genai.Client,
    *,
    prompt: str,
    previous_video: types.Video,
    label: str,
) -> tuple[types.Video, int]:
    video = _uri_only_video(previous_video)
    durations = [CLIP_SECONDS, EXTEND_FALLBACK_SECONDS]
    last_error: BaseException | None = None

    for duration in durations:
        try:
            logger.info(
                f"video_generate: extending {label} with duration_seconds={duration}"
            )
            operation = await client.aio.models.generate_videos(
                model=VEO_MODEL,
                source=types.GenerateVideosSource(prompt=prompt, video=video),
                config=_base_video_config(duration_seconds=duration),
            )
            generated = await _wait_for_operation(client, operation, label=label)
            return generated, duration
        except Exception as exc:
            last_error = exc
            if duration == CLIP_SECONDS and _is_duration_error(exc):
                logger.warning(
                    f"video_generate: {label} rejected {CLIP_SECONDS}s extend, "
                    f"retrying with {EXTEND_FALLBACK_SECONDS}s — {exc}"
                )
                continue
            raise

    raise last_error or RuntimeError(f"{label} failed")


async def _download_video_bytes(client: genai.Client, video: types.Video) -> bytes:
    if not video.uri:
        raise RuntimeError("Cannot download video: Veo did not return a file URI")
    video_bytes = await asyncio.to_thread(client.files.download, file=video)
    if not video_bytes:
        raise RuntimeError("Downloaded video was empty")
    return video_bytes


async def generate_business_video(payload: VideoGenerateRequest) -> dict:
    _log_section("Video Studio — starting Veo generation")
    beats = payload.beats[:MAX_EXTEND_BEATS]
    if len(payload.beats) > MAX_EXTEND_BEATS:
        logger.warning(
            "video_generate: Veo allows at most "
            f"{MAX_EXTEND_BEATS} extensions; using the first {MAX_EXTEND_BEATS} beats"
        )

    segments_requested = 1 + len(beats)
    logger.info(
        f"video_generate: business='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"model='{VEO_MODEL}', resolution={VIDEO_RESOLUTION}, "
        f"clip_seconds={CLIP_SECONDS}, segments={segments_requested}, "
        f"images={len(payload.image_urls)}"
    )

    current_video: types.Video | None = None
    video_bytes: bytes | None = None
    segments_completed = 0
    applied_beats: list[dict] = []
    errors: list[str] = []
    client: genai.Client | None = None

    try:
        images = await _download_reference_images(payload.image_urls)
        client = genai.Client(api_key=settings.Gemini_API_KEY)
        current_video = await _generate_intro_clip(
            client,
            prompt=payload.master_prompt,
            images=images,
        )
        segments_completed = 1
        logger.info("video_generate: intro clip ready, starting beat extensions")

        for beat in beats:
            label = f"beat {beat.beat_number} ({beat.purpose})"
            try:
                current_video, extend_seconds_used = await _extend_clip(
                    client,
                    prompt=beat.prompt,
                    previous_video=current_video,
                    label=label,
                )
                segments_completed += 1
                applied_beats.append(
                    {
                        "beat_number": beat.beat_number,
                        "purpose": beat.purpose,
                        "extend_seconds": extend_seconds_used,
                    }
                )
            except Exception as exc:
                logger.exception(f"video_generate: {label} failed — {exc}")
                errors.append(f"{label}: {exc}")
                break
    except Exception as exc:
        logger.exception(f"video_generate: intro generation failed — {exc}")
        errors.append(str(exc))

    if current_video is not None and client is not None:
        try:
            video_bytes = await _download_video_bytes(client, current_video)
        except Exception as exc:
            logger.exception(f"video_generate: failed to download video bytes — {exc}")
            errors.append(f"Video download failed: {exc}")

    if segments_completed == segments_requested and not errors:
        status = "success"
    elif video_bytes:
        status = "partial"
    else:
        status = "error"

    estimated_duration = 0
    if segments_completed > 0:
        estimated_duration = CLIP_SECONDS + sum(
            int(beat.get("extend_seconds") or CLIP_SECONDS)
            for beat in applied_beats
        )
    result = {
        "status": status,
        "message": (
            "Video generated successfully."
            if status == "success"
            else (
                "Video generated partially; later beats failed."
                if status == "partial"
                else "Video generation failed."
            )
        ),
        "business_name": payload.business_name,
        "business_id": payload.business_id,
        "model": VEO_MODEL,
        "resolution": VIDEO_RESOLUTION,
        "aspect_ratio": VIDEO_ASPECT_RATIO,
        "audio": True,
        "clip_seconds": CLIP_SECONDS,
        "target_seconds": payload.target_seconds,
        "segments_requested": segments_requested,
        "segments_completed": segments_completed,
        "estimated_duration_seconds": estimated_duration,
        "beats_applied": applied_beats,
        "video_s3_key": get_video_studio_video_s3_key(
            payload.business_name,
            payload.business_id,
        ),
        "result_s3_key": get_video_studio_result_s3_key(
            payload.business_name,
            payload.business_id,
        ),
        "video_url": None,
    }
    if errors:
        result["errors"] = errors
        result["message"] = errors[-1] if status == "error" else result["message"]

    uploaded = upload_video_studio_files_to_s3(
        payload.business_name,
        video_bytes=video_bytes,
        result=result,
        business_id=payload.business_id,
    )
    if video_bytes and not uploaded["video"]:
        result.setdefault("errors", []).append("Failed to upload video.mp4 to S3")
        if result["status"] == "success":
            result["status"] = "partial"
    if not uploaded["result"]:
        result.setdefault("errors", []).append("Failed to upload Result.json to S3")

    _log_section("Video Studio — finished")
    logger.info(
        f"video_generate: status={result['status']}, "
        f"segments={segments_completed}/{segments_requested}, "
        f"estimated_duration={estimated_duration}s"
    )
    return result
