"""
Disposable email domain checker.

Maintains a set of known disposable email domains.
Check is O(1) hash lookup - fast enough for request-time validation.
"""

DISPOSABLE_DOMAINS: set[str] = {
    "mailinator.com",
    "guerrillamail.com",
    "guerrillamail.net",
    "guerrillamail.org",
    "tempmail.com",
    "tempmail.net",
    "throwaway.email",
    "temp-mail.org",
    "temp-mail.io",
    "fakeinbox.com",
    "sharklasers.com",
    "guerrillamailblock.com",
    "grr.la",
    "dispostable.com",
    "yopmail.com",
    "yopmail.fr",
    "cool.fr.nf",
    "jetable.fr.nf",
    "nospam.ze.tc",
    "nomail.xl.cx",
    "mega.zik.dj",
    "speed.1s.fr",
    "courriel.fr.nf",
    "moncourrier.fr.nf",
    "monemail.fr.nf",
    "monmail.fr.nf",
    "trashmail.com",
    "trashmail.me",
    "trashmail.net",
    "trashmail.org",
    "trashymail.com",
    "10minutemail.com",
    "10minutemail.net",
    "minutemail.com",
    "tempinbox.com",
    "tempinbox.net",
    "binkmail.com",
    "bobmail.info",
    "chammy.info",
    "devnullmail.com",
    "emailondeck.com",
    "emailthe.net",
    "getairmail.com",
    "getnada.com",
    "harakirimail.com",
    "inboxbear.com",
    "mailcatch.com",
    "maildrop.cc",
    "mailexpire.com",
    "mailnesia.com",
    "mailscrap.com",
    "mailseal.de",
    "mailtemp.info",
    "mobi.web.id",
    "nwytg.net",
    "objectmail.com",
    "proxymail.eu",
    "rcpt.at",
    "reallymymail.com",
    "recode.me",
    "spamavert.com",
    "spamfree24.org",
    "spoofmail.de",
    "tmail.ws",
    "tmails.net",
    "tmpmail.net",
    "tmpmail.org",
    "wegwerfmail.de",
    "wegwerfmail.net",
    "wh4f.org",
    "willhackforfood.biz",
    "zoemail.org",
    "mailnator.com",
    "mailtothis.com",
    "mailzilla.com",
    "pookmail.com",
    "filzmail.com",
    "letthemeatspam.com",
    "spamevader.com",
    "spamspot.com",
    "e4ward.com",
    "gishpuppy.com",
    "kasmail.com",
    "mailblocks.com",
    "spamex.com",
    "spamgourmet.com",
    "temporaryemail.net",
    "temporaryforwarding.com",
    "temporaryinbox.com",
    "thankyou2010.com",
    "trash-mail.at",
    "trashdevil.com",
    "trashdevil.de",
    "mailforspam.com",
    "safetymail.info",
    "soodonims.com",
    "spam4.me",
    "spaml.com",
    "uggsrock.com",
    "mailmoat.com",
    "mytempemail.com",
    "incognitomail.org",
    "emailigo.de",
    "emailsensei.com",
    "guerrillamail.de",
    "guerrillamail.biz",
    "cuvox.de",
    "armyspy.com",
    "dayrep.com",
    "einrot.com",
    "fleckens.hu",
    "gustr.com",
    "jourrapide.com",
    "rhyta.com",
    "superrito.com",
    "teleworm.us",
}


def is_disposable_email(email: str) -> bool:
    """
    Check if an email address uses a known disposable domain.

    Args:
        email: Full email address (e.g., "user@mailinator.com")

    Returns:
        True if the domain is a known disposable email provider
    """
    try:
        domain = email.strip().lower().split("@")[1]
    except (IndexError, AttributeError):
        return False

    return domain in DISPOSABLE_DOMAINS


def get_email_domain(email: str) -> str:
    """Extract domain from email address."""
    try:
        return email.strip().lower().split("@")[1]
    except (IndexError, AttributeError):
        return ""
