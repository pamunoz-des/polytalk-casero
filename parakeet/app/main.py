import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app.engine import get_model, transcribe_pcm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("polytalk_parakeet")

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2

WINDOW_SECONDS = float(os.getenv("PARAKEET_WINDOW_SECONDS", "3.0"))
PAUSE_SECONDS = float(os.getenv("PARAKEET_PAUSE_SECONDS", "1.0"))
IDLE_SECONDS = float(os.getenv("PARAKEET_IDLE_SECONDS", "3.0"))
MAX_CONTEXT_SECONDS = float(os.getenv("PARAKEET_MAX_CONTEXT_SECONDS", "15.0"))

MIN_AUDIO_BYTES = int(0.5 * BYTES_PER_SECOND)
WINDOW_BYTES = int(WINDOW_SECONDS * BYTES_PER_SECOND)
MAX_CONTEXT_BYTES = int(MAX_CONTEXT_SECONDS * BYTES_PER_SECOND)

FRAME_BYTES = int(0.02 * BYTES_PER_SECOND)
RMS_THRESHOLD = 0.003

MODEL_NAME = os.getenv("PARAKEET_MODEL", "nvidia/parakeet-tdt-0.6b-v3")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Inicializando Parakeet")
    await asyncio.to_thread(get_model)
    logger.info("Parakeet listo para recibir audio")
    yield


app = FastAPI(
    title="PolyTalk NVIDIA Parakeet STT v3",
    lifespan=lifespan
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "engine": "parakeet",
        "version": "3",
        "model": MODEL_NAME,
        "time": int(time.time())
    }


@app.get("/gpu")
def gpu_status():
    import torch
    return {
        "cuda_available": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0)
        if torch.cuda.is_available() else "cpu"
    }


def audio_rms(audio_bytes):
    if len(audio_bytes) < 2:
        return 0.0

    samples = np.frombuffer(
        audio_bytes[:len(audio_bytes) - len(audio_bytes) % 2],
        dtype="<i2"
    ).astype(np.float32)

    if samples.size == 0:
        return 0.0

    return float(np.sqrt(np.mean((samples / 32768.0) ** 2)))


@app.websocket("/v1/stream/transcriptions")
async def stream_transcription(websocket: WebSocket):
    await websocket.accept()

    utterance = bytearray()
    frame_buffer = bytearray()

    committed_text = ""
    current_text = ""
    language = None
    emit_policy = "live"

    sequence = 0
    silence_bytes = 0
    speech_detected = False
    last_live_bytes = 0

    logger.info("Parakeet v3 WebSocket conectado")

    async def emit_transcription(force_emit=False):
        nonlocal sequence, current_text

        if len(utterance) < MIN_AUDIO_BYTES:
            return

        if not speech_detected:
            return

        started = time.monotonic()
        # Excluir silencio final detectado, conservando 200 ms
        # para no cortar consonantes o finales de palabras.
        trim_bytes = max(
            0,
            silence_bytes - int(0.2 * BYTES_PER_SECOND)
        )
        audio_end = len(utterance) - trim_bytes
        audio_to_transcribe = bytes(utterance[:audio_end])

        if len(audio_to_transcribe) < MIN_AUDIO_BYTES:
            return

        recognized = await asyncio.to_thread(
            transcribe_pcm,
            audio_to_transcribe
        )

        if not recognized:
            return

        current_text = recognized.strip()

        full_text = " ".join(
            part for part in (committed_text, current_text) if part
        ).strip()

        sequence += 1

        await websocket.send_json({
            "text": full_text,
            "is_final": False,
            "language": language,
            "has_speech": True,
            "metrics": {
                "sequence": sequence,
                "audio_duration_seconds": round(
                    len(utterance) / BYTES_PER_SECOND, 3
                ),
                "processing_seconds": round(
                    time.monotonic() - started, 3
                ),
                "force_emit": force_emit
            }
        })

    async def finalize_utterance():
        nonlocal committed_text, current_text
        nonlocal silence_bytes, speech_detected, last_live_bytes

        if speech_detected and len(utterance) >= MIN_AUDIO_BYTES:
            await emit_transcription(force_emit=True)

            if current_text:
                committed_text = " ".join(
                    part for part in (committed_text, current_text) if part
                ).strip()

        utterance.clear()
        current_text = ""
        silence_bytes = 0
        speech_detected = False
        last_live_bytes = 0

    async def process_frame(frame):
        nonlocal silence_bytes, speech_detected, last_live_bytes

        utterance.extend(frame)

        if audio_rms(frame) >= RMS_THRESHOLD:
            speech_detected = True
            silence_bytes = 0
        else:
            silence_bytes += len(frame)

        if (
            speech_detected
            and silence_bytes >= int(PAUSE_SECONDS * BYTES_PER_SECOND)
        ):
            await finalize_utterance()
            return

        if (
            emit_policy == "live"
            and speech_detected
            and len(utterance) - last_live_bytes >= WINDOW_BYTES
        ):
            await emit_transcription()
            last_live_bytes = len(utterance)

        if len(utterance) >= MAX_CONTEXT_BYTES:
            await finalize_utterance()

    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=IDLE_SECONDS
                )
            except asyncio.TimeoutError:
                if frame_buffer:
                    frame_buffer.extend(
                        b"\x00" * (
                            FRAME_BYTES - len(frame_buffer)
                        )
                    )
                    await process_frame(bytes(frame_buffer))
                    frame_buffer.clear()

                if utterance:
                    await finalize_utterance()
                continue

            if message.get("type") == "websocket.disconnect":
                break

            if message.get("text"):
                try:
                    control = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                if "language" in control:
                    language = control["language"]

                if control.get("emit_policy") in ("live", "pause"):
                    emit_policy = control["emit_policy"]
                    await websocket.send_json({
                        "type": "emit_policy_ack",
                        "emit_policy": emit_policy
                    })

                if "candidate_languages" in control:
                    await websocket.send_json({
                        "type": "candidate_languages_ack",
                        "candidate_languages": control["candidate_languages"]
                    })

                if control.get("type") == "flush":
                    if frame_buffer:
                        frame_buffer.extend(
                            b"\x00" * (
                                FRAME_BYTES - len(frame_buffer)
                            )
                        )
                        await process_frame(bytes(frame_buffer))
                        frame_buffer.clear()

                    if utterance:
                        await finalize_utterance()

                    await websocket.send_json({"type": "flush_ack"})
                continue

            chunk = message.get("bytes")
            if not chunk:
                continue

            frame_buffer.extend(chunk)

            while len(frame_buffer) >= FRAME_BYTES:
                frame = bytes(frame_buffer[:FRAME_BYTES])
                del frame_buffer[:FRAME_BYTES]
                await process_frame(frame)

    except WebSocketDisconnect:
        logger.info("Parakeet v3 WebSocket desconectado")
    except Exception as exc:
        logger.exception("Error en WebSocket")
        try:
            await websocket.send_json({"error": str(exc)})
        except Exception:
            pass
    finally:
        logger.info("Parakeet v3 WebSocket finalizado")
