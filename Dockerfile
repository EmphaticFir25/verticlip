FROM python:3.12-slim

# ffmpeg renders clips; fonts-dejavu supplies the watermark font inside the container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 33214
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "33214"]
