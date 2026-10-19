import io
import os
import time
import threading

import torch
import soundfile as sf

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from TTS.api import TTS

app = FastAPI()
lock = threading.Lock()

VOICE_FILE = "/app/voces/patricio.wav"

if not torch.cuda.is_available():
    raise RuntimeError("XTTS requiere una GPU CUDA")

if not os.path.isfile(VOICE_FILE):
    raise RuntimeError("No existe la grabación de referencia")

print("Cargando XTTS...", flush=True)

tts = TTS(
    "tts_models/multilingual/multi-dataset/xtts_v2"
).to("cuda")

model = tts.synthesizer.tts_model
model.eval()

print("Procesando la voz de Patricio...", flush=True)

with torch.inference_mode():
    gpt_cond_latent, speaker_embedding = (
        model.get_conditioning_latents(
            audio_path=[VOICE_FILE]
        )
    )

print("XTTS preparado. Voz almacenada en memoria.", flush=True)


class SpeechRequest(BaseModel):
    text: str
    language: str = "en"
    temperature: float = 0.75
    speed: float = 0.97


@app.get("/health")
def health():
    return {
        "status": "ok",
        "engine": "xtts",
        "voice_cached": True
    }


@app.post("/synthesize")
def synthesize(request: SpeechRequest):
    if not request.text.strip():
        raise HTTPException(
            status_code=400,
            detail="El texto está vacío"
        )

    start = time.perf_counter()

    try:
        with lock:
            generation_start = time.perf_counter()

            with torch.inference_mode():
                result = model.inference(
                    text=request.text,
                    language=request.language,
                    gpt_cond_latent=gpt_cond_latent,
                    speaker_embedding=speaker_embedding,
                    temperature=request.temperature,
                    top_p=0.85,
                    top_k=50,
                    repetition_penalty=2.0,
                    speed=request.speed,
                )

            # Sincronizar CUDA para medir correctamente.
            torch.cuda.synchronize()

            generation_time = (
                time.perf_counter() - generation_start
            )

            buffer = io.BytesIO()

            sf.write(
                buffer,
                result["wav"],
                tts.synthesizer.output_sample_rate,
                format="WAV"
            )

            audio_data = buffer.getvalue()

        total_time = time.perf_counter() - start

        print(
            f"XTTS | Generación: {generation_time:.2f}s "
            f"| Total: {total_time:.2f}s",
            flush=True
        )

        return Response(
            content=audio_data,
            media_type="audio/wav",
            headers={
                "X-Generation-Time": f"{generation_time:.3f}",
                "X-Total-Time": f"{total_time:.3f}"
            }
        )

    except Exception as exc:
        print(f"Error XTTS: {exc}", flush=True)
        raise HTTPException(
            status_code=500,
            detail="Error durante la generación de audio"
        )