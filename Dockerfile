# Playwright needs Chromium + system libs. The official Playwright Python image
# ships them preinstalled and matches our playwright>=1.60 pin.
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

# Install VNC, Xvfb, fluxbox, and novnc for headless browser login
RUN apt-get update && apt-get install -y \
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    net-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY . .

# Set up VNC and Display
ENV DISPLAY=:99
ENV RESOLUTION=1280x800x24
ENV HOST=0.0.0.0
ENV PORT=8000

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Expose FastAPI port
EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
