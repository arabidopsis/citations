# Import smtplib for the actual sending function

import smtplib
from email.mime.text import MIMEText
from typing import List, Optional, Union

from . import config


def sendmail(
    html: str,
    you: str,
    replyto: Optional[Union[str, List[str]]] = None,
    me=config.EMAIL,
    mailhost=config.MAIL_SERVER,
    subject="citations monitor",
):
    msg = MIMEText(html, "html")

    msg["Subject"] = subject
    msg["From"] = me
    msg["To"] = you
    if replyto:
        msg["Reply-To"] = replyto if isinstance(replyto, str) else ",".join(replyto)

    with smtplib.SMTP() as s:
        s.connect(mailhost)
        s.sendmail(me, [you], msg.as_string())
