from django.conf.urls import patterns, url


urlpatterns = patterns('nodeshot.ui.default.views',
    url(r'^$', 'open311', name='open311'),
)