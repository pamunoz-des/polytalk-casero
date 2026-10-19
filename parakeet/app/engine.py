import glob
import logging
import threading

import numpy as np
import torch
import nemo.collections.asr as nemo_asr

logger = logging.getLogger("polytalk_parakeet.engine")

_model = None
_model_lock = threading.Lock()
_inference_lock = threading.Lock()


def get_model():
    global _model

    if _model is None:
        with _model_lock:
            if _model is None:
                files = glob.glob(
                    "/models/parakeet-tdt-0.6b-v3/*.nemo"
                )
                if not files:
                    raise FileNotFoundError(
                        "No se encontro el modelo Parakeet"
                    )

                logger.info("Cargando Parakeet en GPU")

                model = nemo_asr.models.ASRModel.restore_from(
                    files[0],
                    map_location="cpu"
                )

                device = "cuda" if torch.cuda.is_available() else "cpu"
                model = model.to(device)
                model.eval()

                _model = model
                logger.info("Parakeet cargado en %s", device)

    return _model


def transcribe_pcm(audio_bytes: bytes) -> str:
    if not audio_bytes or len(audio_bytes) % 2:
        return ""

    audio = np.frombuffer(audio_bytes, dtype="<i2")
    if audio.size < 1600:
        return ""

    samples = audio.astype(np.float32) / 32768.0

    model = get_model()

    with _inference_lock:
        with torch.inference_mode():
            result = model.transcribe(
                [samples.copy()],
                batch_size=1
            )

    if not result:
        return ""

    item = result[0]
    return (item.text if hasattr(item, "text") else str(item)).strip()
