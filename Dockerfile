FROM python:3.11-slim

# System deps: ffmpeg for audio conversion (OGG/Opus ↔ WAV ↔ MP3)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only PyTorch build — pinned to a version with full NumPy 2.x support.
COPY requirements.txt .
RUN pip install --no-cache-dir \
    torch==2.4.1+cpu \
    torchaudio==2.4.1+cpu \
    --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
