"""A tiny OpenAI-compatible mock upstream for smoke-testing RedactGate end to end.

It echoes the last user message back as the assistant reply AND records every request
body it receives to a JSON file, so the smoke test can assert that the body RedactGate
forwarded contained placeholders (never raw PII). Supports streaming.

Run:  uvicorn scripts.mock_upstream:app --port 8077
"""

from __future__ import annotations

import json
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

RECORD = os.environ.get("MOCK_UPSTREAM_RECORD", "/tmp/redactgate_upstream_last.json")
app = FastAPI()


def _last_user(body: dict) -> str:
    users = [m.get("content") for m in body.get("messages", []) if m.get("role") == "user"]
    return users[-1] if users and isinstance(users[-1], str) else ""


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    with open(RECORD, "w", encoding="utf-8") as fh:
        json.dump(body, fh, ensure_ascii=False, indent=2)
    reply = "Acknowledged: " + _last_user(body)

    if body.get("stream"):
        def gen():
            cid = "chatcmpl-mock"
            # stream in small pieces, deliberately splitting on placeholder boundaries
            for i in range(0, len(reply), 5):
                piece = reply[i : i + 5]
                chunk = {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": body.get("model", "mock"),
                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
            done = {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": body.get("model", "mock"),
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(done)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "mock"),
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22},
    }
