import base64
from solders.transaction import VersionedTransaction
from solders.keypair import Keypair


class EphemeralSigner:
    """In-memory signer. Swappable for KMS/HSM later."""

    def __init__(self, private_key_base58: str):
        self._keypair = Keypair.from_base58_string(private_key_base58)

    @property
    def pubkey_str(self) -> str:
        return str(self._keypair.pubkey())

    def sign_b64(self, transaction_b64: str) -> str:
        tx = VersionedTransaction.from_bytes(base64.b64decode(transaction_b64))
        signed = VersionedTransaction(tx.message, [self._keypair])
        return base64.b64encode(bytes(signed)).decode('utf-8')




