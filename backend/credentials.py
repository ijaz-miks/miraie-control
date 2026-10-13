"""Account secrets are kept in the user's GNOME Keyring."""
import json
import gi
gi.require_version('Secret', '1')
from gi.repository import Secret

SCHEMA = Secret.Schema.new('io.github.ijaz.Miraie.Account', Secret.SchemaFlags.NONE,
                           {'application': Secret.SchemaAttributeType.STRING})
ATTRIBUTES = {'application': 'miraie-control'}

def load():
    secret = Secret.password_lookup_sync(SCHEMA, ATTRIBUTES, None)
    if not secret:
        return None
    data = json.loads(secret)
    if not isinstance(data.get('username'), str) or not isinstance(data.get('password'), str):
        return None
    return data['username'], data['password']

def save(username, password):
    stored = Secret.password_store_sync(SCHEMA, ATTRIBUTES, Secret.COLLECTION_DEFAULT,
        'Panasonic MirAIe account', json.dumps({'username': username, 'password': password}), None)
    if not stored:
        raise RuntimeError('GNOME Keyring could not save the account.')

def clear():
    Secret.password_clear_sync(SCHEMA, ATTRIBUTES, None)
