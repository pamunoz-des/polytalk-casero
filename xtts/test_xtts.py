import torch
from TTS.api import TTS

print("CUDA disponible:", torch.cuda.is_available())

if not torch.cuda.is_available():
    raise RuntimeError("No se detectó la GPU NVIDIA")

tts = TTS(
    "tts_models/multilingual/multi-dataset/xtts_v2"
).to("cuda")

tts.tts_to_file(
    text=(
        "Hello, my name is Patricio. "
        "Today, I'd like to share something interesting with you. "
        "Let's take a closer look."
    ),
    speaker_wav="/app/voces/patricio.wav",
    language="en",
    file_path="/app/salida/prueba_natural.wav",
    temperature=0.75,
    top_p=0.85,
    top_k=50,
    repetition_penalty=2.0,
    speed=0.97
)

print("Audio generado correctamente")