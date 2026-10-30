"""凭据与协议接受记录。"""

from core.credential.eula import (
    accepted_flags,
    acceptance_status,
    record_acceptance,
    revoke_acceptance,
)
from core.credential.store import (
    get_credential,
    load_credentials,
    save_credentials,
    set_credential,
)

__all__ = [
    "accepted_flags",
    "acceptance_status",
    "record_acceptance",
    "revoke_acceptance",
    "load_credentials",
    "save_credentials",
    "get_credential",
    "set_credential",
]
