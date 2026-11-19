"""Run ONCE on your own computer. Opens a browser, you log in as the OUTREACH Gmail account, and it prints the
three values to save as GitHub secrets. Needs client_secret.json (OAuth client, type 'Desktop app')."""
from google_auth_oauthlib.flow import InstalledAppFlow

from google_io import SCOPES

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
if not creds.refresh_token:
    raise SystemExit("No refresh token returned. Revoke the app at myaccount.google.com/permissions and run again.")
# Write the token to a git-ignored file instead of printing it, so it never lands in terminal logs.
with open("refresh_token.txt", "w", encoding="utf-8") as f:
    f.write(creds.refresh_token)
print("\nRefresh token saved to refresh_token.txt (git-ignored; never commit it).")
print("Use it as the GOOGLE_REFRESH_TOKEN GitHub secret. GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET come from client_secret.json.")
