import os
import resend

resend.api_key = os.getenv("RESEND_API_KEY", "")

FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "noreply@theblockgym.ro")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
EMAIL_LOGO_URL = os.getenv("EMAIL_LOGO_URL", "")


_EMAIL_CONTENT = {
    "ro": {
        "subject": "Resetare parolă – The Block Gym",
        "greeting": "Bună, {first_name}!",
        "body": "Am primit o cerere de resetare a parolei pentru contul tău.",
        "button": "Resetează parola",
        "footer": "Link-ul expiră în 1 oră. Dacă nu ai solicitat resetarea parolei, poți ignora acest email.",
    },
    "en": {
        "subject": "Password Reset – The Block Gym",
        "greeting": "Hi, {first_name}!",
        "body": "We received a request to reset the password for your account.",
        "button": "Reset password",
        "footer": "This link expires in 1 hour. If you didn't request a password reset, you can safely ignore this email.",
    },
}

_WELCOME_EMAIL_CONTENT = {
    "ro": {
        "subject": "Bun venit la The Block Gym!",
        "greeting": "Bun venit, {first_name}!",
        "body": "Contul tău a fost creat cu succes. Suntem bucuroși să te avem alături de noi.",
        "button": "Mergi la site",
        "terms_notice": 'Prin crearea acestui cont, ai acceptat <a href="{terms_url}" style="color:#dc2626;">Termenii și Condițiile</a> și <a href="{privacy_url}" style="color:#dc2626;">Politica de Confidențialitate</a>.',
        "footer": "Dacă nu ai creat acest cont, te rugăm să ne contactezi imediat.",
    },
    "en": {
        "subject": "Welcome to The Block Gym!",
        "greeting": "Welcome, {first_name}!",
        "body": "Your account has been successfully created. We're happy to have you with us.",
        "button": "Go to website",
        "terms_notice": 'By creating this account, you agreed to our <a href="{terms_url}" style="color:#dc2626;">Terms and Conditions</a> and <a href="{privacy_url}" style="color:#dc2626;">Privacy Policy</a>.',
        "footer": "If you did not create this account, please contact us immediately.",
    },
}


async def send_welcome_email(to_email: str, first_name: str, lang: str = "ro") -> None:
    content = _WELCOME_EMAIL_CONTENT.get(lang, _WELCOME_EMAIL_CONTENT["ro"])
    terms_url = f"{FRONTEND_URL}/terms"
    privacy_url = f"{FRONTEND_URL}/privacy"
    logo_html = f'<img src="{EMAIL_LOGO_URL}" alt="The Block Gym" style="height:40px;margin-bottom:24px;" />' if EMAIL_LOGO_URL else '<p style="font-size:18px;font-weight:bold;margin:0 0 24px;">The Block Gym</p>'
    resend.Emails.send({
        "from": FROM_EMAIL,
        "to": to_email,
        "subject": content["subject"],
        "html": f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;color:#fff;background:#0a0a0a;padding:32px;border-radius:12px;">
          {logo_html}
          <h2 style="margin:0 0 8px;">{content["greeting"].format(first_name=first_name)}</h2>
          <p style="color:#aaa;margin:0 0 16px;">{content["body"]}</p>
          <p style="color:#aaa;font-size:13px;margin:0 0 20px;">{content["terms_notice"].format(terms_url=terms_url, privacy_url=privacy_url)}</p>
          <a href="{FRONTEND_URL}"
             style="display:inline-block;background:#dc2626;color:#fff;text-decoration:none;padding:12px 28px;border-radius:8px;font-weight:600;font-size:14px;">
            {content["button"]}
          </a>
          <p style="color:#666;font-size:12px;margin-top:24px;">{content["footer"]}</p>
        </div>
        """,
    })


async def send_password_reset_email(to_email: str, first_name: str, token: str, lang: str = "ro") -> None:
    content = _EMAIL_CONTENT.get(lang, _EMAIL_CONTENT["ro"])
    reset_url = f"{FRONTEND_URL}/reset-password?token={token}"
    logo_html = f'<img src="{EMAIL_LOGO_URL}" alt="The Block Gym" style="height:40px;margin-bottom:24px;" />' if EMAIL_LOGO_URL else '<p style="font-size:18px;font-weight:bold;margin:0 0 24px;">The Block Gym</p>'
    resend.Emails.send({
        "from": FROM_EMAIL,
        "to": to_email,
        "subject": content["subject"],
        "html": f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;color:#fff;background:#0a0a0a;padding:32px;border-radius:12px;">
          {logo_html}
          <h2 style="margin:0 0 8px;">{content["greeting"].format(first_name=first_name)}</h2>
          <p style="color:#aaa;margin:0 0 24px;">{content["body"]}</p>
          <a href="{reset_url}"
             style="display:inline-block;background:#dc2626;color:#fff;text-decoration:none;padding:12px 28px;border-radius:8px;font-weight:600;font-size:14px;">
            {content["button"]}
          </a>
          <p style="color:#666;font-size:12px;margin-top:24px;">
            {content["footer"]}
          </p>
        </div>
        """,
    })
