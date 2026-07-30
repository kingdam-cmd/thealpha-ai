from pypdf import PdfReader
import streamlit as st
import os
import docx
from dotenv import load_dotenv

from prompts import SYSTEM_PROMPT
from providers import PROVIDERS, has_key, stream_reply
from auth import require_login, current_user, sign_out
import db

load_dotenv()

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
MAX_DOC_CHARS = 150_000


# ---------- Documents ----------

def extract_pdf_text(f):
    reader = PdfReader(f)
    pages = [p.extract_text() for p in reader.pages]
    return "\n\n".join(p for p in pages if p)


def extract_docx_text(f):
    document = docx.Document(f)
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n\n".join(parts)


def extract_text(f):
    name = f.name.lower()
    if name.endswith(".pdf"):
        return extract_pdf_text(f)
    if name.endswith(".docx"):
        return extract_docx_text(f)
    if name.endswith((".txt", ".md", ".csv")):
        return f.read().decode("utf-8", errors="ignore")
    raise ValueError(f"Unsupported file type: {f.name}")


def clear_document():
    st.session_state.doc_text = None
    st.session_state.doc_name = None


def clear_image():
    st.session_state.image_bytes = None
    st.session_state.image_name = None


def start_new_chat():
    st.session_state.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    st.session_state.chat_id = None
    clear_document()
    clear_image()


def derive_title(messages):
    for m in messages:
        if m["role"] == "user":
            return m["content"][:60]
    return "New chat"


# ---------- Page setup ----------

st.set_page_config(page_title="TheAlpha AI", page_icon="logo.png", layout="centered")

user = require_login()

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
if "chat_id" not in st.session_state:
    st.session_state.chat_id = None
if "provider" not in st.session_state:
    available = [k for k in PROVIDERS if has_key(k)]
    st.session_state.provider = available[0] if available else list(PROVIDERS)[0]


# ---------- Sidebar ----------

with st.sidebar:
    st.image("logo.png", width=80)
    st.markdown("### TheAlpha AI")
    st.caption(user.email)

    if st.button("New chat", use_container_width=True):
        start_new_chat()
        st.rerun()

    st.divider()
    st.caption("History")

    for chat in db.list_chats(user.id):
        row1, row2 = st.columns([5, 1])
        label = chat.get("title") or "Untitled"
        if row1.button(label, key=f"open_{chat['id']}", use_container_width=True):
            messages = db.load_chat(chat["id"])
            if messages:
                st.session_state.messages = messages
                st.session_state.chat_id = chat["id"]
                clear_document()
                clear_image()
                st.rerun()
        if row2.button("🗑", key=f"del_{chat['id']}"):
            db.delete_chat(chat["id"])
            if st.session_state.chat_id == chat["id"]:
                start_new_chat()
            st.rerun()

    st.divider()
    if st.button("Sign out", use_container_width=True):
        sign_out()


# ---------- Main chat ----------

st.title("TheAlpha AI")

for msg in st.session_state.messages[1:]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# ---------- Bottom bar ----------

st.markdown("""
<style>
div[data-testid="stBottomBlockContainer"] { padding-bottom: 0.5rem; }
div[data-testid="stBottom"] div[data-testid="stVerticalBlock"] { gap: 0.25rem; }
</style>
""", unsafe_allow_html=True)

with st.bottom:
    attached = st.session_state.get("doc_name") or st.session_state.get("image_name")
    if attached:
        acol1, acol2 = st.columns([6, 1])
        icon = "🖼️" if st.session_state.get("image_name") else "📄"
        acol1.caption(f"{icon} {attached}")
        if acol2.button("Remove", use_container_width=True):
            clear_document()
            clear_image()
            st.rerun()

    user_input = st.chat_input(
        "Message TheAlpha AI...",
        accept_file=True,
        file_type=["pdf", "docx", "txt", "md", "csv", "png", "jpg", "jpeg", "webp", "gif"],
    )

    options = [k for k in PROVIDERS if has_key(k)] or list(PROVIDERS.keys())
    if st.session_state.provider not in options:
        st.session_state.provider = options[0]

    pcol, scol = st.columns([1, 2])
    with pcol:
        st.session_state.provider = st.selectbox(
            "Model", options,
            index=options.index(st.session_state.provider),
            label_visibility="collapsed",
        )
    with scol:
        st.caption(PROVIDERS[st.session_state.provider]["model"])


# ---------- Handle input ----------

if user_input:
    if user_input.files:
        f = user_input.files[0]
        if f.name.lower().endswith(IMAGE_EXTS):
            clear_document()
            st.session_state.image_bytes = f.read()
            st.session_state.image_name = f.name
        else:
            clear_image()
            try:
                with st.spinner("Reading document..."):
                    text = extract_text(f)
                if not text.strip():
                    st.warning(
                        f"No readable text in {f.name}. "
                        "If it's a scan, the text isn't extractable."
                    )
                else:
                    st.session_state.doc_text = text[:MAX_DOC_CHARS]
                    st.session_state.doc_name = f.name
            except Exception as e:
                st.error(f"Couldn't read {f.name}: {e}")

    prompt = user_input.text

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        payload = list(st.session_state.messages)

        doc = st.session_state.get("doc_text")
        if doc:
            payload.insert(1, {
                "role": "system",
                "content": (
                    "The user has attached a document. Answer from it where relevant, "
                    "and say clearly when something isn't covered by it.\n\n"
                    f"--- DOCUMENT: {st.session_state.doc_name} ---\n{doc}"
                ),
            })

        with st.chat_message("assistant"):
            try:
                reply = st.write_stream(
                    stream_reply(
                        st.session_state.provider,
                        payload,
                        st.session_state.get("image_bytes"),
                        st.session_state.get("image_name"),
                    )
                )
            except Exception as e:
                reply = f"Error: {e}"
                st.error(reply)

        st.session_state.messages.append({"role": "assistant", "content": reply})

        st.session_state.chat_id = db.save_chat(
            st.session_state.chat_id,
            user.id,
            derive_title(st.session_state.messages),
            st.session_state.messages,
        )

    st.rerun()