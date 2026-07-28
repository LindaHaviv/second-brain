"""Instagram (Instagram-Login) access-token helper.

Instagram gives you a SHORT-lived token (~1 hour) in the app dashboard. Exchange it for a
LONG-lived token (~60 days), and refresh that before it expires.

  # first time, most secure — exchange the short-lived token from your Meta app and store
  # the result straight in the OS keychain (nothing printed, nothing on disk):
  IG_APP_SECRET=... ../.venv/bin/python scripts/instagram_token.py <SHORT_LIVED_TOKEN> --store
  # then add the POINTER (not the token) to oracle/.env:
  #   IG_ACCESS_TOKEN=keychain:ig-access-token

  # without --store the token is printed for you to paste into oracle/.env as a plain
  # value (works, but plaintext on disk — the keychain route is preferred).

  # refresh the long-lived token (~every 60 days):
  ../.venv/bin/python scripts/instagram_token.py --refresh
  # keychain-backed tokens are refreshed IN PLACE, silently. Plain-env tokens are
  # printed for you to re-paste (agents are blocked from editing .env; humans aren't).

  # --auto (what sync.py runs): refresh only when the token is keychain-backed, and only
  # on Mondays — a weekly in-place refresh keeps the 60-day clock permanently reset,
  # so the loader never goes quietly dark from an expired token again.
"""
import datetime
import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "oracle" / ".env")
except ImportError:
    pass   # shell env still works

sys.path.insert(0, str(ROOT / "oracle" / "agent"))
import keychain_secrets  # noqa: E402

BASE = "https://graph.instagram.com"
DEFAULT_ITEM = "ig-access-token"


def _get(path, **params):
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def _store(item, token):
    import keyring
    keyring.set_password(keychain_secrets.SERVICE, item, token)


def main():
    args = sys.argv[1:]
    auto = "--auto" in args
    store = "--store" in args
    args = [a for a in args if a not in ("--auto", "--store")]

    raw = os.environ.get("IG_ACCESS_TOKEN", "")
    kc_item = raw[len("keychain:"):] if raw.startswith("keychain:") else None

    if args and args[0] == "--refresh":
        if auto:
            if not kc_item:
                print("(--auto: IG token not keychain-backed; nothing to refresh in place)")
                return
            if datetime.date.today().weekday() != 0:
                print("(--auto: weekly refresh runs on Mondays; token still well within 60d)")
                return
        tok = keychain_secrets.resolve(raw) if raw else None
        if kc_item and tok == raw:
            sys.exit(f"keychain item '{kc_item}' not found — store the token first "
                     f"(see --store in this file's docstring)")
        if not tok:
            sys.exit("set IG_ACCESS_TOKEN (the long-lived token to refresh)")
        d = _get("refresh_access_token", grant_type="ig_refresh_token", access_token=tok)
    elif args:
        secret = keychain_secrets.resolve(os.environ.get("IG_APP_SECRET", "")) or None
        if not secret:
            sys.exit("set IG_APP_SECRET (from your Meta app > App settings > Basic)")
        d = _get("access_token", grant_type="ig_exchange_token",
                 client_secret=secret, access_token=args[0])
    else:
        sys.exit(__doc__)

    days = int(d.get("expires_in", 0)) // 86400
    token = d.get("access_token") or d.get("token")
    if not token:
        sys.exit(f"unexpected response (no access_token): {d}")

    if kc_item or store:
        item = kc_item or DEFAULT_ITEM
        _store(item, token)
        print(f"token stored in keychain item '{item}' (valid ~{days} days)")
        if not kc_item:
            print(f"now add to oracle/.env:  IG_ACCESS_TOKEN=keychain:{item}")
    else:
        print(f"\nIG_ACCESS_TOKEN={token}\n\n(valid ~{days} days — paste into oracle/.env, "
              f"then refresh with --refresh before it expires)")


if __name__ == "__main__":
    main()
