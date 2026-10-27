import re
from fastapi import Request
# SPDX-FileCopyrightText: 2026 BizzAppDev Systems Pvt. Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
API router for PolyTalk application.

Provides REST endpoints for audio translation.
"""

import asyncio
from pathlib import Path
import json
import time
from typing import Optional, AsyncGenerator

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)

from ..services.pipeline_service import TranslationPipelineService
from ..services.whisper_service import WhisperService
from ..services.visual_context_service import VisualContextService
from ..utils.config import get_custom_instruction_max_chars
from ..utils.logger import get_logger
from ..utils.sanitize import normalize_instruction
from ..version import __version__

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["api"])

pipeline_service: Optional[TranslationPipelineService] = None
visual_context_service: Optional[VisualContextService] = None


def sanitize_custom_instruction(value: str | None) -> str:
    """Normalize and bound user-provided translation guidance."""
    return normalize_instruction(value, get_custom_instruction_max_chars())


def get_pipeline_service() -> TranslationPipelineService:
    """
    Get or create the translation pipeline service singleton.

    Returns:
        TranslationPipelineService instance
    """
    global pipeline_service
    if pipeline_service is None:
        pipeline_service = TranslationPipelineService()
    return pipeline_service


def get_visual_context_service() -> VisualContextService:
    """Get or create the visual context service singleton."""
    global visual_context_service
    if visual_context_service is None:
        visual_context_service = VisualContextService()
    return visual_context_service


async def close_visual_context_service() -> None:
    """Close and reset the visual context service singleton."""
    global visual_context_service
    if visual_context_service is None:
        return

    await visual_context_service.close()
    visual_context_service = None


def should_start_visual_context_request(
    image_data_url: str, in_flight: bool, ready: bool
) -> bool:
    """Return whether a visual context request should be accepted."""
    return bool(image_data_url) and not (in_flight or ready)




@router.post("/voices/custom/create")
async def create_omnivoice_profile(
    name: str = Form(...),
    audio_file: UploadFile = File(...),
):
    """Create an authorized voice profile in OmniVoice."""
    import httpx

    nombre = name.strip()

    if not nombre or len(nombre) > 80:
        raise HTTPException(
            status_code=400,
            detail="El nombre debe tener entre 1 y 80 caracteres.",
        )

    formatos = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
        ".m4a": "audio/mp4",
    }

    extension = Path(audio_file.filename or "").suffix.lower()

    if extension not in formatos:
        raise HTTPException(
            status_code=400,
            detail="Formato no permitido. Usa WAV, MP3, OGG, WEBM o M4A.",
        )

    limite = 20 * 1024 * 1024
    contenido = await audio_file.read(limite + 1)

    if not contenido:
        raise HTTPException(
            status_code=400,
            detail="El archivo de audio esta vacio.",
        )

    if len(contenido) > limite:
        raise HTTPException(
            status_code=413,
            detail="El archivo supera el limite de 20 MB.",
        )

    url = "http://polytalk-omnivoice:7860/api/voices/custom/create"

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            respuesta = await client.post(
                url,
                data={
                    "name": nombre,
                    "add_to_my_voices": "true",
                },
                files={
                    "audio_file": (
                        audio_file.filename,
                        contenido,
                        formatos[extension],
                    )
                },
            )

        if respuesta.status_code >= 400:
            logger.warning(
                "OmniVoice rejected voice creation: HTTP %s",
                respuesta.status_code,
            )
            raise HTTPException(
                status_code=502,
                detail="OmniVoice no pudo crear el perfil de voz.",
            )

        return respuesta.json()

    except httpx.HTTPError:
        logger.exception("OmniVoice voice creation failed")
        raise HTTPException(
            status_code=502,
            detail="No se pudo conectar con OmniVoice.",
        )


@router.get("/voices/my-list")
async def list_omnivoice_voices():
    """List saved OmniVoice profiles."""
    import httpx
    from fastapi import HTTPException

    url = "http://polytalk-omnivoice:7860/api/voices/my-list"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError:
        logger.exception("Failed to retrieve OmniVoice profiles")
        raise HTTPException(
            status_code=502,
            detail="OmniVoice service unavailable",
        )


@router.get("/health")
async def health_check() -> dict:
    """
    Health check endpoint.

    Returns:
        Health status dictionary
    """
    return {
        "status": "healthy",
        "version": __version__,
        "service": "PolyTalk API",
    }


@router.websocket("/ws/translate")
async def websocket_translate(
    websocket: WebSocket,
    source_language: str = "en",
    target_language: str = "gu",
    mode: str = "live",
    stt_engine: str = "parakeet",
    tts_engine: str = "xtts",
    tts_enabled: bool = True,
    custom_instruction: str | None = None,
    voice_id: str | None = None,
):
    """
    WebSocket endpoint for real-time audio translation streaming (2-thread pipeline).

    Args:
        websocket: WebSocket connection
        source_language: Source language code
        target_language: Target language code
        mode: Translation mode, either live or conversation.
        custom_instruction: Optional user-provided translation guidance.
    """
    await websocket.accept()
    logger.info(
        f"WebSocket connection established: {source_language} -> {target_language} mode={mode}"
    )

    if mode not in {"live", "conversation"}:
        await websocket.send_json(
            {"type": "error", "error": "mode must be live or conversation"}
        )
        await websocket.close()
        return

    if stt_engine not in {"whisper", "parakeet"}:
        await websocket.send_json(
            {"type": "error", "error": "Unsupported STT engine"}
        )
        await websocket.close()
        return

    if stt_engine == "parakeet" and mode == "conversation":
        await websocket.send_json(
            {
                "type": "error",
                "error": "Parakeet STT is not available in conversation mode",
            }
        )
        await websocket.close()
        return

    if tts_engine not in {"auto", "xtts", "omnivoice", "supertonic", "piper"}:
        await websocket.send_json(
            {"type": "error", "error": "Unsupported TTS engine"}
        )
        await websocket.close()
        return

    pipeline = get_pipeline_service()

    if stt_engine == "parakeet":
        parakeet_service = WhisperService()
        parakeet_service.base_url = "http://parakeet:8000"
        parakeet_service.ws_endpoint = "/v1/stream/transcriptions"
        pipeline = TranslationPipelineService(
            whisper_service=parakeet_service,
            warm_connections=False,
            confirmed_transcripts_only=True,
        )
        logger.info("Using NVIDIA Parakeet STT for this session")
    audio_chunks = []
    client_disconnected = False
    pause_event = asyncio.Event()
    language_swap_queue = asyncio.Queue()
    visual_context_queue = asyncio.Queue(maxsize=1)
    custom_instruction_queue = asyncio.Queue(maxsize=1)
    visual_context_tasks: set[asyncio.Task] = set()
    visual_context_in_flight = False
    visual_context_ready = False

    connection_start = time.time()
    idle_timeout_seconds = 300

    async def send_pipeline_status(
        stage: str,
        status: str,
        message: str,
    ) -> None:
        """Send a compact connection/readiness update to the frontend."""
        await send_result(
            {
                "type": "pipeline_status",
                "stage": stage,
                "status": status,
                "message": message,
            }
        )

    async def audio_generator() -> AsyncGenerator[bytes, None]:
        """Generate audio chunks from WebSocket messages."""
        nonlocal client_disconnected, connection_start, visual_context_in_flight
        is_paused = False
        try:
            while True:
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=30.0)
                    connection_start = time.time()
                except asyncio.TimeoutError:
                    idle_time = time.time() - connection_start
                    if idle_time > idle_timeout_seconds:
                        logger.warning(
                            f"Connection idle for {idle_time:.1f}s, closing connection"
                        )
                        client_disconnected = True
                        return
                    logger.debug("Audio generator timeout, continuing...")
                    continue

                if "bytes" in message:
                    if not is_paused:
                        audio_chunk = message["bytes"]
                        audio_chunks.append(audio_chunk)
                        yield audio_chunk
                    else:
                        logger.debug("Audio chunk received while paused, discarding")
                elif "text" in message:
                    data = json.loads(message["text"])
                    if data.get("type") == "end":
                        client_disconnected = True
                        logger.info("Client sent 'end' signal, stopping pipeline")
                        yield b"__END_SIGNAL__"
                        return
                    elif data.get("type") == "pause":
                        is_paused = True
                        pause_event.set()
                        logger.info(
                            "Client sent 'pause' signal, stopping audio transmission"
                        )
                    elif data.get("type") == "resume":
                        is_paused = False
                        pause_event.clear()
                        logger.info(
                            "Client sent 'resume' signal, resuming audio transmission"
                        )
                    elif data.get("type") == "custom_instruction":
                        instruction = sanitize_custom_instruction(
                            data.get("custom_instruction")
                        )
                        while not custom_instruction_queue.empty():
                            try:
                                custom_instruction_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        custom_instruction_queue.put_nowait(instruction)
                        logger.info(
                            "Custom translation instruction updated: "
                            f"chars={len(instruction)}"
                        )
                    elif data.get("type") == "visual_context":
                        image_data_url = data.get("image_data_url") or ""
                        if should_start_visual_context_request(
                            image_data_url,
                            visual_context_in_flight,
                            visual_context_ready,
                        ):
                            visual_context_in_flight = True
                            task = asyncio.create_task(
                                summarize_visual_context(image_data_url)
                            )
                            visual_context_tasks.add(task)
                            task.add_done_callback(visual_context_tasks.discard)
                        elif image_data_url:
                            logger.debug("Ignoring duplicate visual context request")
                    elif data.get("type") == "swap_languages":
                        logger.info(
                            "Ignoring language swap request; runtime swap is disabled"
                        )
                        try:
                            await websocket.send_json(
                                {
                                    "type": "swap_disabled",
                                    "message": "Language swap is disabled for active sessions",
                                }
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to send swap disabled response: {e}"
                            )
        except WebSocketDisconnect:
            client_disconnected = True
            logger.info("Client disconnected")
        except GeneratorExit:
            logger.info("GeneratorExit received")
            raise
        except Exception as e:
            logger.error(f"Error receiving audio chunk: {e}")

    async def send_result(result: dict):
        """Send result to WebSocket client."""
        try:
            await websocket.send_json(result)
        except Exception as e:
            logger.debug(f"Error sending result: {e}")

    async def summarize_visual_context(image_data_url: str) -> None:
        """Summarize a shared tab/page screenshot without blocking audio receive."""
        nonlocal visual_context_in_flight, visual_context_ready
        try:
            await send_pipeline_status(
                "visual_context",
                "active",
                "Reading shared tab context",
            )
            service = get_visual_context_service()
            summary = await service.summarize_screenshot(
                image_data_url,
                source_language,
                target_language,
            )
            if not summary:
                await send_pipeline_status(
                    "visual_context",
                    "warning",
                    "Shared tab context unavailable",
                )
                return

            while not visual_context_queue.empty():
                try:
                    visual_context_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            visual_context_queue.put_nowait(summary)
            visual_context_ready = True
            logger.info(
                "Visual context summary received: "
                f"chars={len(summary)} summary={summary[:1200]!r}"
            )
            await send_pipeline_status(
                "visual_context",
                "done",
                "Shared tab context ready",
            )
        except Exception as exc:
            logger.warning(f"Visual context service call failed: {exc}")
            await send_pipeline_status(
                "visual_context",
                "warning",
                "Shared tab context unavailable",
            )
        finally:
            visual_context_in_flight = False

    audio_gen = audio_generator()

    try:
        await send_pipeline_status(
            "server_connected",
            "done",
            "Server connected",
        )
        await send_pipeline_status(
            "pipeline_warming",
            "active",
            "Preparing translation pipeline",
        )
        try:
            await pipeline.warm_connections()
            await send_pipeline_status(
                "pipeline_ready",
                "done",
                "Pipeline ready",
            )
        except Exception as warm_error:
            logger.warning(f"Pipeline warm-up failed: {warm_error}")
            await send_pipeline_status(
                "pipeline_ready",
                "warning",
                "Pipeline ready with warnings",
            )

        if voice_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", voice_id):
            await websocket.close(code=1008, reason="Invalid voice profile identifier")
            return

        if tts_engine != "omnivoice":
            voice_id = None

        async for result in pipeline.process_streaming(
            audio_gen,
            source_language,
            target_language,
            mode=mode,
            pause_event=pause_event,
            language_swap_queue=language_swap_queue,
            visual_context_queue=visual_context_queue,
            custom_instruction=sanitize_custom_instruction(custom_instruction),
            custom_instruction_queue=custom_instruction_queue,
            tts_engine=tts_engine,
            tts_enabled=tts_enabled,
            voice_id=voice_id,
        ):
            if client_disconnected:
                logger.info("Client disconnected, stopping pipeline")
                break

            try:
                await send_result(result)
            except Exception:
                if result.get("type") != "complete":
                    logger.info("Client disconnected during streaming")
                    break

            if result.get("type") == "complete":
                logger.info("Translation pipeline complete")
                break

    except Exception as e:
        logger.error(f"WebSocket translation error: {e}")
        if not client_disconnected:
            try:
                await send_result({"type": "error", "error": str(e)})
            except Exception:
                pass

    finally:
        if stt_engine == "parakeet":
            try:
                await pipeline.close()
                logger.info("Parakeet session pipeline closed")
            except Exception as close_error:
                logger.warning(f"Error closing Parakeet pipeline: {close_error}")

        connection_duration = time.time() - connection_start
        logger.info(f"WebSocket connection closed after {connection_duration:.2f}s")

        try:
            await audio_gen.aclose()
        except Exception as e:
            logger.error(f"Error closing audio generator: {e}")

        for task in visual_context_tasks:
            task.cancel()
        if visual_context_tasks:
            await asyncio.gather(*visual_context_tasks, return_exceptions=True)

        if not client_disconnected:
            try:
                await websocket.close()
            except Exception:
                pass


# POLYTALK OMNIVOICE AUDIO PROXY 619
@router.get("/audio/{filename}")
async def omnivoice_generated_audio(filename: str):
    import re
    import httpx
    from fastapi import HTTPException
    from fastapi.responses import Response

    if not re.fullmatch(r"[A-Za-z0-9_-]+\.wav", filename):
        raise HTTPException(
            status_code=400,
            detail="Nombre de archivo de audio no valido"
        )

    url = (
        "http://polytalk-omnivoice:7860/api/audio/"
        + filename
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resultado = await client.get(url)
            resultado.raise_for_status()

        return Response(
            content=resultado.content,
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"}
        )

    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=error.response.status_code,
            detail="Audio no disponible en OmniVoice"
        )

    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail="No fue posible conectar con OmniVoice"
        )



# POLYTALK OMNIVOICE SYNTHESIS PROXY 622
@router.post("/voices/synthesize")
async def polytalk_omnivoice_synthesize(request: Request):
    import httpx
    from fastapi import HTTPException

    datos = await request.json()

    texto = str(datos.get("text", "")).strip()
    voice_id = str(datos.get("voice_id", "")).strip()
    language = datos.get("language", "es")

    if not isinstance(language, str):
        raise HTTPException(
            status_code=400,
            detail="Idioma invalido"
        )

    language = language.strip()

    if not language or len(language) > 10:
        raise HTTPException(
            status_code=400,
            detail="Idioma invalido"
        )

    if not texto or len(texto) > 500:
        raise HTTPException(
            status_code=400,
            detail="El texto debe contener entre 1 y 500 caracteres"
        )

    if not voice_id:
        raise HTTPException(
            status_code=400,
            detail="Selecciona un perfil de voz"
        )

    payload = {
        "text": texto,
        "language": language,
        "model_choice": "omnivoice",
        "voice_id": voice_id,
        "speed": "1.0"
    }

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            respuesta = await client.post(
                "http://polytalk-omnivoice:7860/api/synthesize",
                data=payload
            )

        respuesta.raise_for_status()
        resultado = respuesta.json()

        if not resultado.get("success"):
            raise HTTPException(
                status_code=502,
                detail="OmniVoice no pudo generar el audio"
            )

        audio_url = resultado.get("audio_url", "")

        if not (
            isinstance(audio_url, str)
            and audio_url.startswith("/api/audio/")
            and audio_url.endswith(".wav")
        ):
            raise HTTPException(
                status_code=502,
                detail="OmniVoice devolvio una ruta de audio invalida"
            )

        return {
            "success": True,
            "audio_url": audio_url,
            "duration_seconds": resultado.get("duration_seconds"),
            "voice_id": voice_id
        }

    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=502,
            detail="Error de sintesis en OmniVoice: "
                   + str(error.response.status_code)
        )

    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail="No fue posible conectar con OmniVoice"
        )


# POLYTALK TEXT TRANSLATION V1
@router.post("/text/translate")
async def polytalk_text_translate(request: Request):
    from fastapi import HTTPException
    from app.services.translation_service import TranslationService

    try:
        datos = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON invalido")

    if not isinstance(datos, dict):
        raise HTTPException(status_code=400, detail="JSON invalido")

    texto = datos.get("text", "")
    origen = datos.get("source_language", "es")
    destino = datos.get("target_language", "en")

    if not all(isinstance(x, str) for x in (texto, origen, destino)):
        raise HTTPException(status_code=400, detail="Campos invalidos")

    texto = texto.strip()
    origen = origen.strip()
    destino = destino.strip()

    if not texto or len(texto) > 3000:
        raise HTTPException(
            status_code=400,
            detail="El texto debe tener entre 1 y 3000 caracteres"
        )

    if not origen or not destino:
        raise HTTPException(
            status_code=400,
            detail="Selecciona ambos idiomas"
        )

    if origen == destino:
        return {
            "success": True,
            "translated_text": texto,
            "source_language": origen,
            "target_language": destino
        }

    servicio = TranslationService()

    try:
        resultado = await servicio.translate(
            text=texto,
            source_language=origen,
            target_language=destino
        )

        if not resultado.success:
            raise HTTPException(
                status_code=502,
                detail=resultado.error or "Error de traduccion"
            )

        return {
            "success": True,
            "translated_text": resultado.text,
            "source_language": origen,
            "target_language": destino
        }

    finally:
        await servicio.close()
