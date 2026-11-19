import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Tuple

logger = logging.getLogger("resolva")


def send_invitation_email(
    to_email: str,
    recipient_name: str,
    role: str,
    company_name: str,
    invite_url: str,
    smtp_host: Optional[str] = None,
    smtp_port: int = 587,
    smtp_user: Optional[str] = None,
    smtp_password: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Dispatches a branded workspace invitation email with password setup link.
    Returns (success: bool, message: str).
    """
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f5f7; margin: 0; padding: 24px; }}
        .card {{ max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 16px; padding: 36px; border: 1px solid #e5e7eb; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
        .header {{ font-size: 20px; font-weight: 700; color: #111827; margin-bottom: 8px; }}
        .subtitle {{ font-size: 14px; color: #6b7280; line-height: 1.5; margin-bottom: 24px; }}
        .role-badge {{ display: inline-block; padding: 4px 12px; background: #EEF2FF; color: #4F46E5; border-radius: 9999px; font-weight: 600; font-size: 12px; text-transform: uppercase; margin-bottom: 20px; }}
        .btn {{ display: inline-block; background-color: #0f172a; color: #ffffff !important; padding: 12px 28px; border-radius: 10px; font-weight: 600; text-decoration: none; font-size: 14px; margin: 16px 0 24px; }}
        .footer {{ font-size: 12px; color: #9ca3af; margin-top: 24px; border-top: 1px solid #f3f4f6; padding-top: 16px; line-height: 1.4; }}
        .link-text {{ word-break: break-all; color: #4F46E5; font-size: 12px; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header">Join {company_name} on Resolva</div>
        <p class="subtitle">Hello {recipient_name or 'there'},</p>
        <p class="subtitle">You have been invited to join the <strong>{company_name}</strong> workspace as:</p>
        <div class="role-badge">{role.replace('_', ' ').title()}</div>
        <p class="subtitle">Click the button below to set up your password and access your workspace portal:</p>
        <div>
          <a href="{invite_url}" class="btn" target="_blank">Set Up Password & Sign In</a>
        </div>
        <p class="subtitle" style="font-size: 12px; color: #6b7280;">
          This link will expire in 7 days. If you did not expect this invitation, you can safely ignore this email.
        </p>
        <div class="footer">
          If the button doesn't work, copy and paste this link into your browser:<br>
          <a href="{invite_url}" class="link-text">{invite_url}</a>
        </div>
      </div>
    </body>
    </html>
    """

    if not smtp_host or not smtp_user or not smtp_password:
        msg = f"No SMTP credentials provided. Setup Link: {invite_url}"
        logger.warning(f"[EMAIL NOT CONFIGURED] {msg}")
        return False, "No outgoing email account configured in Settings > Email Accounts."

    # Clean password (strip spaces if user copied with spaces e.g. 'abcd efgh ijkl mnop')
    clean_password = smtp_password.replace(" ", "").strip()
    clean_user = smtp_user.strip()

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Invitation to join {company_name} on Resolva"
        msg["From"] = f"{company_name} Support <{clean_user}>"
        msg["To"] = to_email

        text_fallback = (
            f"You are invited to join {company_name} as {role}.\n"
            f"Set up your password here: {invite_url}"
        )
        msg.attach(MIMEText(text_fallback, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
                server.login(clean_user, clean_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(clean_user, clean_password)
                server.send_message(msg)

        logger.info(f"Invitation email successfully delivered to {to_email} via {clean_user}")
        return True, f"Invitation email sent to {to_email} via {clean_user}."
    except smtplib.SMTPAuthenticationError as auth_err:
        err_msg = (
            "Gmail authentication failed. If 2-Step Verification is enabled on your Google account, "
            "you must use a 16-character Google App Password (generate one at myaccount.google.com/apppasswords)."
        )
        logger.error(f"SMTP Auth error sending to {to_email}: {auth_err}")
        return False, err_msg
    except smtplib.SMTPServerDisconnected as disc_err:
        err_msg = (
            "Gmail server disconnected unexpectedly. This usually happens when an invalid password or "
            "regular account password is used instead of a 16-character Google App Password (myaccount.google.com/apppasswords)."
        )
        logger.error(f"SMTP Server Disconnected sending to {to_email}: {disc_err}")
        return False, err_msg
    except Exception as exc:
        err_str = str(exc)
        if "Connection unexpectedly closed" in err_str or "Broken pipe" in err_str:
            err_msg = (
                "Gmail connection closed. Please ensure 2-Step Verification is enabled and a valid 16-character "
                "Google App Password is used (myaccount.google.com/apppasswords)."
            )
        else:
            err_msg = f"Failed to send email: {err_str}"
        logger.error(f"SMTP error sending to {to_email}: {exc}")
        return False, err_msg


def send_otp_email(
    to_email: str,
    recipient_name: str,
    otp_code: str,
    company_name: str = "Resolyx",
    smtp_host: Optional[str] = None,
    smtp_port: int = 587,
    smtp_user: Optional[str] = None,
    smtp_password: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Dispatches a branded 6-digit OTP verification email for customer account registration.
    Returns (success: bool, message: str).
    """
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f5f7; margin: 0; padding: 24px; }}
        .card {{ max-width: 520px; margin: 0 auto; background: #ffffff; border-radius: 16px; padding: 36px; border: 1px solid #e5e7eb; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
        .header {{ font-size: 20px; font-weight: 700; color: #111827; margin-bottom: 8px; }}
        .subtitle {{ font-size: 14px; color: #6b7280; line-height: 1.5; margin-bottom: 24px; }}
        .otp-box {{ background: #f8fafc; border: 2px dashed #6366f1; border-radius: 12px; padding: 20px; text-align: center; margin: 24px 0; }}
        .otp-code {{ font-family: 'SF Mono', Monaco, Consolas, monospace; font-size: 32px; font-weight: 800; color: #4338ca; letter-spacing: 8px; }}
        .otp-label {{ font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 600; letter-spacing: 1px; margin-top: 6px; }}
        .footer {{ font-size: 12px; color: #9ca3af; margin-top: 24px; border-top: 1px solid #f3f4f6; padding-top: 16px; line-height: 1.4; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header">Verify Your Email Address</div>
        <p class="subtitle">Hello {recipient_name or 'there'},</p>
        <p class="subtitle">Thank you for registering for the <strong>{company_name}</strong> Customer Portal. Please enter the verification code below to verify your email address:</p>
        
        <div class="otp-box">
          <div class="otp-code">{otp_code}</div>
          <div class="otp-label">6-Digit Verification Passcode</div>
        </div>
        
        <p class="subtitle" style="font-size: 12px; color: #6b7280;">
          This code is valid for 10 minutes. If you did not request this verification code, please ignore this email.
        </p>
        <div class="footer">
          &copy; {company_name} Support &bull; Automated Identity Verification
        </div>
      </div>
    </body>
    </html>
    """

    if not smtp_host or not smtp_user or not smtp_password:
        msg = f"No SMTP credentials configured. OTP code for {to_email}: {otp_code}"
        logger.warning(f"[EMAIL NOT CONFIGURED] {msg}")
        return False, "No outgoing email account configured in Settings > Email Accounts."

    clean_password = smtp_password.replace(" ", "").strip()
    clean_user = smtp_user.strip()

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"{otp_code} is your {company_name} verification code"
        msg["From"] = f"{company_name} Support <{clean_user}>"
        msg["To"] = to_email

        text_fallback = (
            f"Hello {recipient_name or 'there'},\n\n"
            f"Your verification code for {company_name} is: {otp_code}\n\n"
            f"This code will expire in 10 minutes.\n"
        )
        msg.attach(MIMEText(text_fallback, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
                server.login(clean_user, clean_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(clean_user, clean_password)
                server.send_message(msg)

        logger.info(f"Verification OTP email successfully delivered to {to_email} via {clean_user}")
        return True, f"Verification code sent to {to_email}."
    except Exception as exc:
        logger.error(f"Failed to send OTP email to {to_email}: {exc}")
        return False, f"Failed to send verification email: {str(exc)}"

