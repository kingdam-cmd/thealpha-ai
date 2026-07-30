"""Real authentication via Supabase — email/password and Google.

Sessions persist across visits: when someone signs in, their Supabase
refresh token is stored in a browser cookie. On their next visit that
token is exchanged for a fresh session, so they land straight in the app.
Signing out deletes the cookie.
"""

import os
import datetime

import streamlit as st
from supabase import create_client
import extra_streamlit_components as stx

COOKIE_NAME = "alpha_refresh"
COOKIE_DAYS = 30


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


def cookies():
    """One cookie manager per session. Deliberately not cached — the
    manager renders a hidden widget, and Streamlit forbids widgets
    inside cached functions. Session state keeps it stable per user."""
    if "cookie_manager" not in st.session_state:
        st.session_state.cookie_manager = stx.CookieManager(key="alpha_cookies")
    return st.session_state.cookie_manager


def _remember(session):
    """Store the refresh token so the next visit skips the login screen."""
    if not session or not session.refresh_token:
        return
    try:
        cookies().set(
            COOKIE_NAME,
            session.refresh_token,
            expires_at=datetime.datetime.now() + datetime.timedelta(days=COOKIE_DAYS),
            key="set_alpha_cookie",
        )
    except Exception:
        pass


def _forget():
    try:
        cookies().delete(COOKIE_NAME, key="del_alpha_cookie")
    except Exception:
        pass


def _site_url():
    """Where Google should send people back to after signing in."""
    return _secret("SITE_URL", "http://localhost:8501")


def current_user():
    """The signed-in user, or None."""
    return st.session_state.get("user")


def display_name(user):
    """The best name we have for this user: their chosen name if set,
    Google's name if they signed in that way, otherwise a fallback
    derived from their email."""
    meta = getattr(user, "user_metadata", None) or {}
    name = meta.get("full_name") or meta.get("name")
    if name:
        return name.strip()

    local = user.email.split("@")[0]
    for sep in (".", "_", "-"):
        local = local.replace(sep, " ")
    return local.strip().title() or "there"


def update_display_name(new_name):
    """Save a new display name to the signed-in user's Supabase profile."""
    result = get_client().auth.update_user({"data": {"full_name": new_name.strip()}})
    if result and result.user:
        st.session_state.user = result.user
        return True
    return False


def sign_out():
    _forget()
    try:
        get_client().auth.sign_out()
    except Exception:
        pass
    for key in ["user", "messages", "chat_id", "doc_text", "doc_name",
                "image_bytes", "image_name"]:
        st.session_state.pop(key, None)
    st.rerun()


def _restore_from_cookie():
    """Turn a stored refresh token back into a live session."""
    if st.session_state.get("cookie_checked"):
        return False
    st.session_state.cookie_checked = True

    token = cookies().get(COOKIE_NAME)
    if not token:
        return False

    try:
        result = get_client().auth.refresh_session(token)
        if result and result.user:
            st.session_state.user = result.user
            _remember(result.session)
            return True
    except Exception:
        # Token expired or was revoked — clear it and show the login screen.
        _forget()
    return False


def _restore_session_from_url():
    """Finish the OAuth handshake using whatever Supabase put in the URL."""
    params = st.query_params

    # Implicit flow — token arrives directly
    token = params.get("access_token")
    if token:
        try:
            result = get_client().auth.get_user(token)
            if result and result.user:
                st.session_state.user = result.user
                refresh = params.get("refresh_token")
                if refresh:
                    try:
                        refreshed = get_client().auth.refresh_session(refresh)
                        _remember(refreshed.session)
                    except Exception:
                        pass
                st.query_params.clear()
                return True
        except Exception as e:
            st.error(f"Couldn't complete sign-in: {e}")
            st.query_params.clear()
        return False

    # PKCE flow — exchange the code for a session
    code = params.get("code")
    if code:
        try:
            result = get_client().auth.exchange_code_for_session({"auth_code": code})
            if result and result.user:
                st.session_state.user = result.user
                _remember(result.session)
                st.query_params.clear()
                return True
        except Exception as e:
            st.error(f"Couldn't complete sign-in: {e}")
            st.query_params.clear()
        return False

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
                    _remember(result.session)
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
        name = st.text_input(
            "Name",
            key="up_name",
            placeholder="How should we greet you?",
        )
        email = st.text_input("Email", key="up_email")
        password = st.text_input(
            "Password", type="password", key="up_pw",
            help="At least 6 characters.",
        )

        if st.button("Create account", use_container_width=True, type="primary"):
            if not name.strip():
                st.error("Enter a name so we know what to call you.")
            elif len(password) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                try:
                    client.auth.sign_up({
                        "email": email,
                        "password": password,
                        "options": {"data": {"full_name": name.strip()}},
                    })
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

    if _restore_from_cookie():
        return current_user()

    _login_form()
    st.stop()