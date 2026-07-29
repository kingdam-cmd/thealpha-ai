"""Model providers for TheAlpha AI. Each returns a stream of text chunks."""

import base64
import os

PROVIDERS = {
    "Claude (Anthropic)": {
        "id": "anthropic",
        "model": "claude-sonnet-4-5",
        "fallbacks": [
            "claude-sonnet-4-20250514",
            "claude-3-5-sonnet-latest",
            "claude-haiku-4-5",
        ],
        "env": "ANTHROPIC_API_KEY",
        "vision": True,
    },
    "Gemini (Google)": {
        "id": "gemini",
        "model": "gemini-2.5-flash",
        "fallbacks": ["gemini-2.5-flash-lite", "gemini-flash-latest", "gemini-2.0-flash"],
        "env": "GEMINI_API_KEY",
        "vision": True,
    },
    "GPT (OpenAI)": {
        "id": "openai",
        "model": "gpt-4o",
        "fallbacks": ["gpt-4o-mini"],
        "env": "OPENAI_API_KEY",
        "vision": True,
    },
}


# ---------- Helpers ----------

def split_system(messages):
    """Separate system instructions from the conversation."""
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    turns = [m for m in messages if m["role"] != "system"]
    return "\n\n".join(system_parts), turns


def guess_mime(filename):
    """Work out an image's mime type from its name."""
    name = (filename or "").lower()
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def has_key(provider_key):
    """Is this provider usable right now?"""
    env = PROVIDERS[provider_key]["env"]
    if env is None:
        return True
    return bool(os.getenv(env))


# ---------- Anthropic ----------

def stream_anthropic(messages, model, image=None, image_name=None):
    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    system, turns = split_system(messages)
    payload = [{"role": m["role"], "content": m["content"]} for m in turns]

    if image and payload:
        payload[-1]["content"] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": guess_mime(image_name),
                    "data": base64.b64encode(image).decode(),
                },
            },
            {"type": "text", "text": payload[-1]["content"]},
        ]

    with client.messages.stream(
        model=model,
        max_tokens=2000,
        system=system,
        messages=payload,
    ) as stream:
        for text in stream.text_stream:
            yield text


# ---------- OpenAI ----------

def stream_openai(messages, model, image=None, image_name=None):
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    payload = [{"role": m["role"], "content": m["content"]} for m in messages]

    if image and payload:
        b64 = base64.b64encode(image).decode()
        mime = guess_mime(image_name)
        payload[-1]["content"] = [
            {"type": "text", "text": payload[-1]["content"]},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]

    stream = client.chat.completions.create(
        model=model,
        messages=payload,
        stream=True,
    )
    for chunk in stream:
        piece = chunk.choices[0].delta.content
        if piece:
            yield piece


# ---------- Gemini ----------

def stream_gemini(messages, model, image=None, image_name=None):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    system, turns = split_system(messages)

    contents = []
    for m in turns:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    if image and contents:
        contents[-1]["parts"].append({
            "inline_data": {
                "mime_type": guess_mime(image_name),
                "data": base64.b64encode(image).decode(),
            }
        })

    stream = client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=system or None),
    )
    for chunk in stream:
        if chunk.text:
            yield chunk.text


# ---------- Dispatcher ----------

STREAMERS = {
    "anthropic": stream_anthropic,
    "openai": stream_openai,
    "gemini": stream_gemini,
}


def stream_reply(provider_key, messages, image=None, image_name=None):
    """Send a conversation to the selected provider.

    Tries the primary model, then each fallback, so a stale or
    unavailable model name doesn't break the app.
    """
    config = PROVIDERS[provider_key]
    streamer = STREAMERS[config["id"]]
    candidates = [config["model"]] + config.get("fallbacks", [])

    last_error = None
    for name in candidates:
        try:
            generator = streamer(messages, name, image, image_name)
            first = next(generator)   # force the API call to happen now
            yield first
            for piece in generator:
                yield piece
            return
        except StopIteration:
            return
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"All models failed. Last error: {last_error}")