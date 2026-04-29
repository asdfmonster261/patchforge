"""Interactive Steam login wrapping the steam[client] WebAuth flow.

QR-code path is the primary login UX; falls back to username + password +
Steam Guard 2FA if the user presses Enter to skip the QR code.

This module imports steam[client] lazily so that simply importing the
archive package does not blow up when the optional 'archive' extras are
not installed.  Callers should run _extras.require_extras() before invoking
fresh_login() / get_session().
"""

from __future__ import annotations

import base64
import io
import json
import select
import sys
import time
from getpass import getpass


def _import_webauth():
    """Lazy import of steam.webauth so missing extras are reported cleanly."""
    from ._extras import require_extras
    require_extras()
    import steam.webauth as wa
    from steam.enums.proto import (
        EAuthSessionGuardType,
        EAuthTokenPlatformType,
        ESessionPersistence,
    )
    return wa, EAuthSessionGuardType, EAuthTokenPlatformType, ESessionPersistence


# ---------------------------------------------------------------------------
# Polling helpers
# ---------------------------------------------------------------------------

def _poll_once(auth) -> bool:
    """Poll a single auth session.  True if complete, False if still pending."""
    resp = auth.send_api_request(
        {
            "client_id":  str(auth.client_id),
            "request_id": str(auth.request_id),
        },
        "IAuthentication", "PollAuthSessionStatus", 1,
    )
    response = resp.get("response", {})
    if "new_client_id" in response:
        auth.client_id = response["new_client_id"]
    if response.get("refresh_token") and response.get("access_token"):
        auth.refresh_token = response["refresh_token"]
        auth.access_token  = response["access_token"]
        return True
    return False


# ---------------------------------------------------------------------------
# 2FA
# ---------------------------------------------------------------------------

def _run_2fa(auth) -> None:
    wa, EAuthSessionGuardType, _, _ = _import_webauth()

    allowed = set(auth.allowed_confirmations)
    if not allowed.intersection(wa.SUPPORTED_AUTH_TYPES):
        raise wa.AuthTypeNotSupported("No supported 2FA type found for this account.")

    using_email = (
        EAuthSessionGuardType.EmailCode in allowed
        and EAuthSessionGuardType.DeviceCode not in allowed
    )
    can_confirm_with_app = EAuthSessionGuardType.DeviceConfirmation in allowed
    has_device_code      = EAuthSessionGuardType.DeviceCode        in allowed

    if using_email:
        while True:
            code = input("Steam Guard email code: ").strip()
            auth._update_login_token(code, EAuthSessionGuardType.EmailCode)
            try:
                auth._pollLoginStatus()
                return
            except wa.TwoFactorCodeRequired:
                print("error: invalid code, try again.", file=sys.stderr)

    while True:
        if has_device_code:
            suffix = " (or press Enter to approve via the Steam app)" if can_confirm_with_app else ""
            code = input(f"Steam Guard code{suffix}: ").strip()
        else:
            input("Approve the login in the Steam app, then press Enter: ")
            code = ""

        if code:
            auth._update_login_token(code, EAuthSessionGuardType.DeviceCode)
            if _poll_once(auth):
                return
            print("error: invalid code, try again.", file=sys.stderr)
        else:
            print("Waiting for Steam app confirmation", end="", flush=True)
            for _ in range(30):  # ~60s budget
                time.sleep(2)
                print(".", end="", flush=True)
                if _poll_once(auth):
                    print()
                    return
            print()
            print("warning: timed out waiting for app approval — try again.",
                  file=sys.stderr)


# ---------------------------------------------------------------------------
# QR login
# ---------------------------------------------------------------------------

def _steamid_from_jwt(token: str) -> int:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    data = json.loads(base64.urlsafe_b64decode(payload))
    return int(data["sub"])


def _start_qr_session(auth) -> str:
    _, _, EAuthTokenPlatformType, ESessionPersistence = _import_webauth()
    resp = auth.send_api_request(
        {
            "device_friendly_name": auth.user_agent,
            "platform_type":        EAuthTokenPlatformType.SteamClient,
            "persistence":          ESessionPersistence.Persistent,
        },
        "IAuthentication", "BeginAuthSessionViaQR", 1,
    )
    r = resp["response"]
    auth.client_id  = r["client_id"]
    auth.request_id = r["request_id"]
    return r["challenge_url"]


def _poll_qr_once(auth) -> dict | None:
    resp = auth.send_api_request(
        {
            "client_id":  str(auth.client_id),
            "request_id": str(auth.request_id),
        },
        "IAuthentication", "PollAuthSessionStatus", 1,
    )
    r = resp.get("response", {})
    if "new_client_id" in r:
        auth.client_id = r["new_client_id"]
    if r.get("refresh_token") and r.get("access_token"):
        return {
            "username":             r.get("account_name", ""),
            "steam_id":             _steamid_from_jwt(r["refresh_token"]),
            "client_refresh_token": r["refresh_token"],
        }
    return None


def _print_qr(challenge_url: str) -> None:
    import qrcode
    qr = qrcode.QRCode(border=1)
    qr.add_data(challenge_url)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    print(buf.getvalue())


def qr_login() -> dict | None:
    """Interactive QR-code login.  Returns None if the user pressed Enter
    to switch to password login, raises TimeoutError on the 2-minute deadline,
    or returns a token dict on success."""
    wa, *_ = _import_webauth()
    auth = wa.WebAuth()
    challenge_url = _start_qr_session(auth)

    _print_qr(challenge_url)
    print("Scan the QR code above with the Steam mobile app to log in.")
    print("(Press Enter to log in with a password instead)", flush=True)
    print("Waiting for approval", end="", flush=True)

    deadline      = time.monotonic() + 120
    qr_refresh_at = time.monotonic() + 25  # QR codes expire ~30s
    while time.monotonic() < deadline:
        if select.select([sys.stdin], [], [], 2)[0]:
            sys.stdin.readline()
            print()
            return None

        print(".", end="", flush=True)
        result = _poll_qr_once(auth)
        if result:
            print()
            return result

        if time.monotonic() >= qr_refresh_at:
            try:
                challenge_url = _start_qr_session(auth)
                print()
                _print_qr(challenge_url)
                print("(Press Enter to log in with a password instead)", flush=True)
                print("Waiting for approval", end="", flush=True)
                qr_refresh_at = time.monotonic() + 25
            except Exception as e:
                print()
                print(f"warning: QR refresh failed: {e}", file=sys.stderr)
                qr_refresh_at = time.monotonic() + 10

    print()
    raise TimeoutError("Timed out waiting for QR approval.")


# ---------------------------------------------------------------------------
# Password fallback
# ---------------------------------------------------------------------------

def password_login() -> dict:
    wa, *_ = _import_webauth()
    username = input("Steam username: ").strip()
    auth = wa.WebAuth(username)

    password = ""
    while True:
        if not password:
            password = getpass(f"Password for {username!r}: ")
        auth.password = password
        try:
            auth._startLoginSession()
        except wa.LoginIncorrect:
            print("error: invalid password, try again.", file=sys.stderr)
            password = ""
            continue
        break

    if not _poll_once(auth):
        _run_2fa(auth)

    return {
        "username":             username,
        "steam_id":             auth.steam_id.as_64,
        "client_refresh_token": auth.refresh_token,
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def fresh_login() -> dict:
    """Run the full interactive login (QR with password fallback).

    Returns a token dict: {username, steam_id, client_refresh_token}.
    """
    tokens = qr_login()
    if tokens is not None:
        return tokens
    return password_login()


def get_session() -> dict:
    """Return a token dict, restoring from disk or prompting for a fresh login.

    The returned dict is also persisted to archive_credentials.json on
    successful fresh login.
    """
    from . import credentials as _creds
    saved = _creds.load()
    if saved.has_login_tokens():
        print(f"Found saved tokens for {saved.username!r}.")
        return {
            "username":             saved.username,
            "steam_id":             saved.steam_id,
            "client_refresh_token": saved.client_refresh_token,
        }

    print("Performing fresh login...")
    tokens = fresh_login()

    creds = _creds.load()
    creds.username             = tokens["username"]
    creds.steam_id             = int(tokens["steam_id"])
    creds.client_refresh_token = tokens["client_refresh_token"]
    _creds.save(creds)
    print(f"Tokens saved to {_creds.credentials_path()}")
    return tokens
