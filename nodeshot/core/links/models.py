from django.db import models
from django.utils.translation import ugettext_lazy as _
from nodeshot.core.base.models import BaseAccessLevel
from nodeshot.core.base.choices import METRIC_TYPES, LINK_STATUS, LINK_TYPE
from nodeshot.core.network.models import Interface

class Link(BaseAccessLevel):
    interface_a = models.ForeignKey(Interface, related_name='interface_a')
    interface_b = models.ForeignKey(Interface, related_name='interface_b')
    type = models.SmallIntegerField(_('type'), max_length=10, choices=LINK_TYPE, default=LINK_TYPE[0][0])
    metric_type = models.CharField(_('metric type'), max_length=6, choices=METRIC_TYPES, default=METRIC_TYPES[0][0])
    metric_value = models.FloatField(_('metric value'), blank=True)
    tx_rate = models.IntegerField(_('TX rate average'), null=True, default=None, blank=True)
    rx_rate = models.IntegerField(_('RX rate average'), null=True, default=None, blank=True)
    status = models.SmallIntegerField(_('status'), choices=LINK_STATUS, default=LINK_STATUS[3][0])
    
    class Meta:
        permissions = (('can_view_links', 'Can view links'),)

class LinkRadio(Link):
    dbm = models.IntegerField(_('dBm average'), null=True, default=None, blank=True)
    noise = models.IntegerField(_('noise average'), null=True, default=None, blank=True)
    
    class Meta:
        db_table = 'links_radio_link'
        verbose_name = _('radio link')
        verbose_name_plural = _('radio links')