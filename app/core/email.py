import os
import resend

resend.api_key = os.getenv("RESEND_API_KEY", "")

FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "noreply@theblockgym.ro")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
EMAIL_LOGO_URL = os.getenv("EMAIL_LOGO_URL", "")


async def send_password_reset_email(to_email: str, first_name: str, token: str) -> None:
    reset_url = f"{FRONTEND_URL}/reset-password?token={token}"
    logo_html = f'<img src="{EMAIL_LOGO_URL}" alt="The Block Gym" style="height:40px;margin-bottom:24px;" />' if EMAIL_LOGO_URL else '<p style="font-size:18px;font-weight:bold;margin:0 0 24px;">The Block Gym</p>'
    resend.Emails.send({
        "from": FROM_EMAIL,
        "to": to_email,
        "subject": "Resetare parolă – The Block Gym",
        "html": f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;color:#fff;background:#0a0a0a;padding:32px;border-radius:12px;">
          {logo_html}
          <h2 style="margin:0 0 8px;">Bună, {first_name}!</h2>
          <p style="color:#aaa;margin:0 0 24px;">Am primit o cerere de resetare a parolei pentru contul tău.</p>
          <a href="{reset_url}"
             style="display:inline-block;background:#dc2626;color:#fff;text-decoration:none;padding:12px 28px;border-radius:8px;font-weight:600;font-size:14px;">
            Resetează parola
          </a>
          <p style="color:#666;font-size:12px;margin-top:24px;">
            Link-ul expiră în 1 oră. Dacă nu ai solicitat resetarea parolei, poți ignora acest email.
          </p>
        </div>
        """,
    })
