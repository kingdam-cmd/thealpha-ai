"""Real authentication via Supabase — email/password and Google."""

import os
import streamlit as st
from supabase import create_client


def _secret(name, default=None):
    """Read from Streamlit secrets, falling back to environment variables."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name, default)


@st.cache_resource
def get_client():
    """One Supabase client, reused across reruns."""
    url = _secret("SUPABASE_URL")
    key = _secret("SUPABASE_ANON_KEY")
    if not url or not key:
        st.error("Supabase is not configured. Add SUPABASE_URL and SUPABASE_ANON_KEY.")
        st.stop()
    return create_client(url, key)


def _site_url():
    """Where Google should send people back to after signing in."""
    return _secret("SITE_URL", "http://localhost:8501")


def current_user():
    """The signed-in user, or None."""
    return st.session_state.get("user")


def sign_out():
    try:
        get_client().auth.sign_out()
    except Exception:
        pass
    for key in ["user", "messages", "chat_id", "doc_text", "doc_name",
                "image_bytes", "image_name"]:
        st.session_state.pop(key, None)
    st.rerun()


def _restore_session_from_url():
    """Pick up the access token Supabase puts in the URL after Google login."""
    params = st.query_params
    token = params.get("access_token")
    if not token:
        return False
    try:
        result = get_client().auth.get_user(token)
        if result and result.user:
            st.session_state.user = result.user
            st.query_params.clear()
            return True
    except Exception:
        pass
    return False


def _login_form():
    head1, head2 = st.columns([1, 5])
    with head1:
        try:
            st.image("logo.png", width=60)
        except Exception:
            st.markdown("### α")
    with head2:
        st.markdown("## TheAlpha AI")
        st.caption("Sign in to continue.")

    client = get_client()

    tab_in, tab_up = st.tabs(["Sign in", "Create account"])

    with tab_in:
        email = st.text_input("Email", key="in_email")
        password = st.text_input("Password", type="password", key="in_pw")

        if st.button("Sign in", use_container_width=True, type="primary"):
            try:
                result = client.auth.sign_in_with_password(
                    {"email": email, "password": password}
                )
                if result.user:
                    st.session_state.user = result.user
                    st.rerun()
            except Exception as e:
                st.error(f"Couldn't sign in: {e}")

        st.markdown(
            "<p style='text-align:center; color:#888; margin:0.75rem 0 0.5rem;'>or</p>",
            unsafe_allow_html=True,
        )

        try:
            oauth = client.auth.sign_in_with_oauth({
                "provider": "google",
                "options": {"redirect_to": _site_url()},
            })
            st.link_button(
                "Continue with Google",
                oauth.url,
                use_container_width=True,
            )
        except Exception as e:
            st.caption(f"Google sign-in unavailable: {e}")

    with tab_up:
        email = st.text_input("Email", key="up_email")
        password = st.text_input(
            "Password", type="password", key="up_pw",
            help="At least 6 characters.",
        )

        if st.button("Create account", use_container_width=True, type="primary"):
            if len(password) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                try:
                    client.auth.sign_up({"email": email, "password": password})
                    st.success(
                        "Account created. Check your email to confirm, then sign in."
                    )
                except Exception as e:
                    st.error(f"Couldn't create account: {e}")


def require_login():
    """Return the signed-in user, or draw the login screen and stop."""
    if current_user():
        return current_user()

    if _restore_session_from_url():
        return current_user()

    _login_form()
    st.stop()