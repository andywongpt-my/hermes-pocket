"""Versioned Pocket model choices. Device input never supplies runtime settings."""
from .schema import ProtocolError


def snapshot(choice):
    if not isinstance(choice, str):
        raise ProtocolError('invalid model choice')
    choices = {
        'default': (None, None),
        'opus': ('claude-subscription-directsdk-experimental', 'claude-opus-5-5[1m]'),
        'glm_flash': ('ollama-cloud', 'glm-5.3-flash'),
    }
    if choice not in choices:
        raise ProtocolError('unknown model choice')
    provider, model = choices[choice]
    return {'choice_id': choice, 'provider': provider, 'model': model}


def validate_snapshot(value, choice=None):
    if not isinstance(value, dict) or set(value) != {'choice_id', 'provider', 'model'}:
        raise ProtocolError('invalid model selection snapshot')
    expected = snapshot(value['choice_id'])
    if value != expected or (choice is not None and expected['choice_id'] != choice):
        raise ProtocolError('model selection snapshot changed')
    return expected


def catalog(explicit_supported):
    models = [{'id': 'default', 'name': 'Follow default', 'verified': True}]
    if explicit_supported:
        models.extend([{'id': 'opus', 'name': 'Claude Opus', 'verified': True},
            {'id': 'glm_flash', 'name': 'GLM 5.3 Flash', 'verified': False}])
    return {'version': 2, 'default_id': 'default', 'models': models}
