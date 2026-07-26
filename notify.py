"""Send notification emails via Gmail SMTP.

Requires GMAIL_ADDRESS and GMAIL_APP_PASSWORD env vars (Gmail app password,
not the normal account password). Recipient defaults to the address in
REPORT_EMAIL.
"""

import os
import smtplib
from email.mime.text import MIMEText

DEFAULT_RECIPIENT = "murathanbagdat@hotmail.com"


def send_email(subject, body):
    address = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = os.environ.get("REPORT_EMAIL", DEFAULT_RECIPIENT)
    if not address or not password:
        print("Email not configured (GMAIL_ADDRESS/GMAIL_APP_PASSWORD missing); "
              "skipping notification.")
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = recipient

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(address, password)
        smtp.sendmail(address, [recipient], msg.as_string())
    print(f"Email sent to {recipient}: {subject}")
    return True
