import os
import uvicorn

port = int(os.environ.get("EMBED_PORT", "8003"))
uvicorn.run("embedding_service:app", host="127.0.0.1", port=port, log_level="info")
