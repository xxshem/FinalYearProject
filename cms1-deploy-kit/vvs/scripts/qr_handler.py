import hashlib, hmac, os

class QRHandler:
    def __init__(self, secret_key=None):
        self.secret_key = secret_key or os.environ.get("VVS_QR_SECRET")
        if not self.secret_key:
            raise RuntimeError("VVS_QR_SECRET must be configured")

    def generate_signature(self, vid, token):
        msg = f"{vid}:{token}"
        return hmac.new(self.secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()

    def verify_signature(self, vid, token, signature):
        return hmac.compare_digest(self.generate_signature(vid, token), signature)