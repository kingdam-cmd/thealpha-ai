"""Chat storage in Supabase Postgres."""

import streamlit as st
from auth import get_client


def list_chats(user_id):
    """Chat summaries for one user, newest first."""
    try:
        result = (
            get_client()
            .table("chats")
            .select("id, title, updated_at")
            .eq("user_id", user_id)
            .order("updated_at", desc=True)
            .limit(50)
            .execute()
        )
        return result.data or []
    except Exception as e:
        st.error(f"Couldn't load chats: {e}")
        return []


def load_chat(chat_id):
    """Full message list for one chat."""
    try:
        result = (
            get_client()
            .table("chats")
            .select("messages")
            .eq("id", chat_id)
            .single()
            .execute()
        )
        return result.data["messages"] if result.data else None
    except Exception as e:
        st.error(f"Couldn't open chat: {e}")
        return None


def save_chat(chat_id, user_id, title, messages):
    """Create or update a chat. Returns the chat id."""
    payload = {
        "user_id": user_id,
        "title": title,
        "messages": messages,
        "updated_at": "now()",
    }
    try:
        client = get_client()
        if chat_id:
            client.table("chats").update(payload).eq("id", chat_id).execute()
            return chat_id
        result = client.table("chats").insert(payload).execute()
        return result.data[0]["id"] if result.data else None
    except Exception as e:
        st.error(f"Couldn't save chat: {e}")
        return chat_id


def delete_chat(chat_id):
    try:
        get_client().table("chats").delete().eq("id", chat_id).execute()
        return True
    except Exception as e:
        st.error(f"Couldn't delete chat: {e}")
        return False


def is_premium(user_id):
    """Whether this account has Alpha Desktop access."""
    try:
        result = (
            get_client()
            .table("profiles")
            .select("is_premium")
            .eq("id", user_id)
            .single()
            .execute()
        )
        return bool(result.data and result.data.get("is_premium"))
    except Exception:
        return False


def usage_stats(user_id):
    """Total chats and total messages for one user."""
    try:
        result = (
            get_client()
            .table("chats")
            .select("messages")
            .eq("user_id", user_id)
            .execute()
        )
        rows = result.data or []
        total_chats = len(rows)
        total_messages = 0
        for row in rows:
            msgs = row.get("messages") or []
            # skip the system prompt that sits at index 0 of every chat
            total_messages += max(0, len(msgs) - 1)
        return {"chats": total_chats, "messages": total_messages}
    except Exception:
        return {"chats": 0, "messages": 0}