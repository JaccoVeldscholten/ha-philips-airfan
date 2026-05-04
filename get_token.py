#!/usr/bin/env python3
"""
Philips Air Fan - Token Helper

This script authenticates with the Philips Air+ account via OAuth2 PKCE
and retrieves a JWT token that can be used with the Home Assistant integration.

Requirements:
    pip install playwright aiohttp
    playwright install chromium

Usage:
    python3 get_token.py

The script will:
1. Open a browser window for Philips login
2. Intercept the OAuth redirect
3. Exchange the code for a Philips access token
4. Authenticate with the Philips Air cloud API
5. Print the JWT token (valid for ~7 days)
"""

import asyncio
import json
import hashlib
import base64
import secrets
from urllib.parse import urlencode

from playwright.async_api import async_playwright
import aiohttp

# Philips OAuth configuration
CLIENT_ID = "-XsK7O6iEkLml77yDGDUi0ku"
CLIENT_SECRET = "V34BlAhuilIdOx0Imo16rGQ2"
TOKEN_URL = "https://cdc.accounts.home.id/oidc/op/v1.0/4_JGZWlP8eQHpEqkvQElolbA/oauth/token"
REDIRECT_URI = "com.philips.air://loginredirect"
AUTH_URL = "https://cdc.accounts.home.id/oidc/op/v1.0/4_JGZWlP8eQHpEqkvQElolbA/authorize"
SCOPE = (
    "openid email profile address DI.Account.read DI.AccountProfile.read "
    "DI.AccountProfile.write DI.AccountGeneralConsent.read "
    "DI.AccountGeneralConsent.write DI.GeneralConsent.read subscriptions "
    "profile_extended consents DI.AccountSubscription.read DI.AccountSubscription.write"
)

# Philips Air cloud API
API_HOST = "https://www.api.air.philips.com"
APP_ID = "9fd505fa9c7111e9a1e3061302926720"


def generate_pkce():
    """Generate PKCE verifier and challenge."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


async def get_auth_code(auth_url: str) -> str | None:
    """Open browser, let user login, intercept the OAuth redirect code."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        redirect_url = None

        client = await context.new_cdp_session(page)
        await client.send("Fetch.enable", {"patterns": [{"requestStage": "Response"}]})

        async def handle_paused(params):
            nonlocal redirect_url
            req_id = params["requestId"]
            status = params.get("responseStatusCode", 0)
            if 300 <= status < 400:
                for h in params.get("responseHeaders", []):
                    if h["name"].lower() == "location" and h["value"].startswith(
                        "com.philips.air://"
                    ):
                        redirect_url = h["value"]
                        body = base64.b64encode(
                            b"<h1>Authentication successful!</h1>"
                            b"<p>You can close this window.</p>"
                        ).decode()
                        await client.send(
                            "Fetch.fulfillRequest",
                            {
                                "requestId": req_id,
                                "responseCode": 200,
                                "responseHeaders": [
                                    {"name": "content-type", "value": "text/html"}
                                ],
                                "body": body,
                            },
                        )
                        return
            await client.send("Fetch.continueRequest", {"requestId": req_id})

        client.on(
            "Fetch.requestPaused", lambda p: asyncio.ensure_future(handle_paused(p))
        )

        try:
            await page.goto(auth_url, timeout=15000)
        except Exception:
            pass

        print("\n🔐 Please log in with your Philips account in the browser window...")
        print("   (waiting up to 5 minutes)\n")

        for _ in range(300):
            if redirect_url:
                break
            await asyncio.sleep(1)

        await browser.close()

    if not redirect_url:
        return None

    return redirect_url.split("code=")[1].split("&")[0]


async def exchange_code(code: str, verifier: str) -> str:
    """Exchange OAuth code for Philips access token, then get cloud JWT."""
    async with aiohttp.ClientSession() as session:
        # Step 1: Exchange code for Philips access token
        data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        }
        async with session.post(TOKEN_URL, data=data) as resp:
            tokens = await resp.json()
            if resp.status != 200:
                raise Exception(f"Token exchange failed: {resp.status}\n{json.dumps(tokens, indent=2)}")
            access_token = tokens["access_token"]
            print("✅ Philips OAuth token obtained")

        # Step 2: Get user info to find username
        userinfo_url = "https://cdc.accounts.home.id/oidc/op/v1.0/4_JGZWlP8eQHpEqkvQElolbA/userinfo"
        headers = {"Authorization": f"Bearer {access_token}"}
        async with session.get(userinfo_url, headers=headers) as resp:
            userinfo = await resp.json()
            sub = userinfo.get("sub", "")
            print(f"✅ User: {sub[:20]}...")

        # Step 3: Get server time
        async with session.get(f"{API_HOST}/device/serverTime/") as resp:
            time_data = await resp.json()
            timestamp = time_data["data"]["timestamp2"]

        # Step 4: Get Fogcloud token
        # The username format for Philips accounts
        username = f"PHILIPS:{sub}"
        fog_data = {
            "username": username,
            "timestamp": timestamp,
            "app_id": APP_ID,
        }

        # Generate signature (HMAC-SHA256)
        import hmac as hmac_mod
        from urllib.parse import quote

        secret = f"a_{APP_ID}"
        fmt = f"app_id={APP_ID}&timestamp={timestamp}&username={quote(username)}"
        hmac1 = hmac_mod.new(secret.encode(), fmt.encode(), hashlib.sha256).hexdigest()
        signature = hmac_mod.new(username.encode(), hmac1.encode(), hashlib.sha256).hexdigest()

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Signature": signature,
        }
        async with session.post(
            f"{API_HOST}/enduser/v2/getToken/", json=fog_data, headers=headers
        ) as resp:
            result = await resp.json()
            if result.get("meta", {}).get("code") != 0:
                # Signature might not match (iOS vs Android algorithm differs)
                # Fall back to trying without signature or with different method
                print(f"⚠️  getToken with computed signature failed: {result.get('meta', {}).get('message', '')}")
                print("   Trying alternative authentication method...")

                # Try the login endpoint as fallback
                login_data = {"username": username, "app_id": APP_ID}
                async with session.put(
                    f"{API_HOST}/enduser/login/", json=login_data
                ) as resp2:
                    result2 = await resp2.json()
                    if result2.get("meta", {}).get("code") == 0:
                        token = result2["data"]["token"]
                        print("✅ Got JWT token via login endpoint")
                        return token
                    else:
                        raise Exception(
                            f"Could not obtain JWT token.\n"
                            f"getToken response: {result}\n"
                            f"login response: {result2}\n\n"
                            f"You may need to capture the token via mitmproxy. "
                            f"See README for instructions."
                        )
            else:
                token = result["data"]["token"]
                print("✅ Got JWT token via getToken endpoint")
                return token


async def main():
    print("=" * 60)
    print("  Philips Air Fan - Token Helper")
    print("=" * 60)

    verifier, challenge = generate_pkce()
    params = {
        "client_id": CLIENT_ID,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "ui_locales": "en-US",
        "scope": SCOPE,
    }
    auth_url = f"{AUTH_URL}?{urlencode(params)}"

    print("\n📋 Opening browser for Philips login...")
    code = await get_auth_code(auth_url)

    if not code:
        print("\n❌ Timeout - no login detected within 5 minutes")
        return

    print(f"✅ OAuth code received")

    try:
        token = await exchange_code(code, verifier)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return

    print("\n" + "=" * 60)
    print("  YOUR JWT TOKEN (copy this into Home Assistant):")
    print("=" * 60)
    print(f"\n{token}\n")
    print("=" * 60)
    print("  Token is valid for approximately 7 days.")
    print("  Add the Philips Air Fan integration in HA and paste this token.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
