import hashlib, hmac

class QRHandler:
    def __init__(self, secret_key="dkut-vvs-2026"):
        self.secret_key = secret_key

    def generate_signature(self, vid, token):
        msg = f"{vid}:{token}"
        return hmac.new(self.secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()

    def verify_signature(self, vid, token, signature):
        return hmac.compare_digest(self.generate_signature(vid, token), signature)