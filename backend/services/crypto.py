from cryptography.fernet import Fernet
from config import settings

fernet = Fernet(settings.ENCRYPTION_KEY.encode())

def encrypt(text: str) -> str:
    return fernet.encrypt(text.encode()).decode()

def decrypt(token: str) -> str:
    return fernet.decrypt(token.encode()).decode()
