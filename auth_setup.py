"""Run ONCE on your own computer. Opens a browser, you log in as the OUTREACH Gmail account, and it prints the
three values to save as GitHub secrets. Needs client_secret.json (OAuth client, type 'Desktop app')."""
from google_auth_oauthlib.flow import InstalledAppFlow

from google_io import SCOPES

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
print("\nSave these as GitHub secrets (never commit them):")
print("GOOGLE_CLIENT_ID     =", creds.client_id)
print("GOOGLE_CLIENT_SECRET =", creds.client_secret)
print("GOOGLE_REFRESH_TOKEN =", creds.refresh_token)
