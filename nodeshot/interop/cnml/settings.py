from django.conf import settings


STATUS_MAPPING = getattr(settings, 'NODESHOT_STATUS_MAPPING', {
    'planned': 'active',
    'working': 'active',
    'testing': 'active',
    'building': 'active',
    'reserved': 'active',
    'dropped': 'active',
    'inactive': 'active'
})

DEFAULT_LAYER = getattr(settings, 'NODESHOT_CNML_DEFAULT_LAYER', 1)
