"""Keychain handles only; credential values never enter connection documents."""
class CredentialStore:
    def __init__(self,player):self.player=player
    def get(self,account):
        value=self.player.secret_get(account.encode())
        return value.decode() if value else ''
    def set(self,account,value):
        if not isinstance(value,str) or len(value)>16384 or '\0' in value:raise ValueError('Invalid API key.')
        if not self.player.secret_set(account.encode(),value.encode()):raise ValueError('Keychain could not save the API key.')
    def delete(self,account):
        if not self.player.secret_delete(account.encode()):raise ValueError('Keychain could not remove the API key.')
