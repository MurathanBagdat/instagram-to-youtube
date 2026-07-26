#!/usr/bin/env python3
"""One-time helper: obtain a YouTube OAuth refresh token.

Run this once on your own machine:

    python3 get_youtube_refresh_token.py

It asks for your Google OAuth client ID and secret (Desktop app type),
opens a browser window for you to sign in with the Google account that
owns the chiathefrenchton YouTube channel, and prints the refresh token
to store as the YT_REFRESH_TOKEN GitHub secret.

Uses only the Python standard library — nothing to install.
"""

import http.server
import json
import urllib.parse
import urllib.request
import webbrowser

PORT = 8765
REDIRECT_URI = f"http://localhost:{PORT}"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"

auth_code = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            auth_code["code"] = params["code"][0]
            body = b"<h2>Done! You can close this tab and return to the terminal.</h2>"
        else:
            body = b"<h2>No authorization code received. Try again.</h2>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    client_id = input("Google OAuth Client ID: ").strip()
    client_secret = input("Google OAuth Client Secret: ").strip()

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })

    print("\nOpening browser. Sign in with the Google account that owns your")
    print("YouTube channel. If your app is unverified, click 'Advanced' ->")
    print("'Go to <app name> (unsafe)' — that's expected for a personal app.\n")
    webbrowser.open(auth_url)

    server = http.server.HTTPServer(("localhost", PORT), Handler)
    while "code" not in auth_code:
        server.handle_request()

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": auth_code["code"],
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req) as resp:
        tokens = json.load(resp)

    refresh = tokens.get("refresh_token")
    if not refresh:
        print("No refresh token returned. Remove the app's access at "
              "https://myaccount.google.com/permissions and run this again.")
        return

    print("\n=== SUCCESS ===")
    print(f"YT_REFRESH_TOKEN:\n{refresh}\n")
    print("Store it with:")
    print("  gh secret set YT_REFRESH_TOKEN")


if __name__ == "__main__":
    main()
