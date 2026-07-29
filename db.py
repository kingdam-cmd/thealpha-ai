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