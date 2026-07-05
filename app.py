"""Start the OpenAI-compatible Copilot server:

    python app.py            # serves http://127.0.0.1:8000 (set HOST / PORT to override)

To bind a custom host/port from the command line, point uvicorn at the ASGI app:

    uvicorn server.api:app --host 0.0.0.0 --port 8080
"""

from server import app

if __name__ == "__main__":
    app()  # blocks while uvicorn runs
