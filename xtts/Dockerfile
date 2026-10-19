FROM pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 espeak-ng git && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir coqui-tts && \
    pip install --no-cache-dir "transformers==4.57.6"

COPY test_xtts.py /app/test_xtts.py

CMD ["python", "test_xtts.py"]

RUN pip install --no-cache-dir fastapi "uvicorn[standard]" soundfile

COPY xtts_api.py /app/xtts_api.py

CMD ["uvicorn", "xtts_api:app", "--host", "0.0.0.0", "--port", "8020"]