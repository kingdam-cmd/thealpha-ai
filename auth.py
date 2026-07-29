"""Simple shared-password gate."""

import os
import hmac
import streamlit as st


def _expected_password():
    """Read the password from Streamlit secrets or the environment."""
    try:
        return st.secrets["APP_PASSWORD"]
    except Exception:
        return os.getenv("APP_PASSWORD")


def check_password():
    """Return True if the user is allowed in. Otherwise draw the login form."""
    expected = _expected_password()

    # No password configured — don't lock people out locally
    if not expected:
        return True

    if st.session_state.get("authenticated"):
        return True

    st.title("TheAlpha AI")
    st.caption("Enter the access password to continue.")

    entered = st.text_input("Password", type="password")

    if entered:
        if hmac.compare_digest(entered, expected):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    return False