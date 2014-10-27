import sys
import string
import random
import traceback
from optparse import make_option

from netaddr import ip

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ImproperlyConfigured
from django.contrib.gis.geos import Point
from django.utils.text import slugify
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
User = get_user_model()

import libcnml
from libcnml import Status as OldStatus

from ...settings import settings, STATUS_MAPPING_CNML, DEFAULT_LAYER_CNML

if 'emailconfirmation' in settings.INSTALLED_APPS:
    EMAIL_ADDRESS_APP_INSTALLED = True
    from nodeshot.community.emailconfirmation.models import EmailAddress
else:
    EMAIL_ADDRESS_APP_INSTALLED = False

# TODO: this check is useless because nodeshot.core.layer is required as a dependency!
if 'nodeshot.core.layers' in settings.INSTALLED_APPS:
    from nodeshot.core.layers.models import Layer
    LAYER_APP_INSTALLED = True
else:
    LAYER_APP_INSTALLED = False

from nodeshot.core.base.utils import pause_disconnectable_signals, resume_disconnectable_signals
from nodeshot.core.nodes.models import Node, Status
from nodeshot.networking.net.models import *
from nodeshot.networking.net.models.choices import INTERFACE_TYPES
from nodeshot.networking.links.models import Link
from nodeshot.networking.links.models.choices import LINK_STATUS, LINK_TYPES, METRIC_TYPES
from nodeshot.community.mailing.models import Inward
from nodeshot.interop.oldimporter.models import *


class Command(BaseCommand):
    """
    Will try to import data from CNML.

    Requirements for settings:
        * nodeshot.interop.cnml must be in INSTALLED_APPS
        * database routers directives must be uncommented?

    Steps:

    1.  Retrieve all nodes
        Retrieve all nodes from old db and convert queryset in a python list.

    2.  Extract user data from nodes

        (Since in old nodeshot there are no users but each node contains data
        such as name, email, and stuff like that)

            * loop over nodes and extract a list of unique emails
            * each unique email will be a new user in the new database
            * each new user will have a random password set
            * save users, email addresses

    3.  Import nodes

            * USER: assign owner (the link is the email)
            * LAYER: assign layer (layers must be created by hand first!):
                1. if node has coordinates comprised in a specified layer choose that
                2. if node has coordinates comprised in more than one layer prompt the user which one to choose
                3. if node does not have coordinates comprised in any layer:
                    1. use default layer if specified (configured in settings.NODESHOT_CNML_DEFAULT_LAYER)
                    2. discard the node if no default layer specified
            * STATUS: assign status depending on configuration:
                settings.NODESHOT_CNML_STATUS_MAPPING must be a dictionary in which the
                key is the old status value while the value is the new status value
                if settings.NODESHOT_CNML_STATUS_MAPPING is False the default status will be used

    4.  Import devices
        Create any missing routing protocol

    5.  Import interfaces, ip addresses, vaps

    6.  Import links

    7.  Import Contacts

    TODO: Decide what to do with statistics and hna.
    """
    help = 'Import CNML data. Layers and Status must be created first? FIXME'

    status_mapping = STATUS_MAPPING_CNML
    # if no default layer some nodes might be discarded
    default_layer = DEFAULT_LAYER_CNML

    old_nodes = []
    saved_nodes = []
    saved_devices = []
    routing_protocols_added = []
    saved_interfaces = []
    saved_vaps = []
    saved_ipv4 = []
    saved_ipv6 = []
    saved_links = []
    saved_contacts = []

    option_list = BaseCommand.option_list + (
        make_option(
            '--noinput',
            action='store_true',
            dest='noinput',
            default=False,
            help='Do not prompt for user intervention and use default settings'
        ),
        make_option(
            '--nodelete',
            action='store_true',
            dest='nodelete',
            default=False,
            help='Do not delete imported data if any uncaught exception occurs'
        ),
    )

    def message(self, message):
        self.stdout.write('%s\n\r' % message)

    def verbose(self, message):
        if self.verbosity == 2:
            self.message(message)

    def handle(self, *args, **options):
        """ execute synchronize command """
        self.options = options
        delete = False

        try:
            # blank line
            self.stdout.write('\r\n')
            # store verbosity level in instance attribute for later use
            self.verbosity = int(self.options.get('verbosity'))

            self.verbose('disabling signals (notififcations, websocket alerts)')
            pause_disconnectable_signals()

            self.check_status_mapping()
            self.retrieve_nodes()
            self.import_users()
            self.import_nodes()
            self.import_devices()
            self.import_interfaces()
            self.import_links()

            self.confirm_operation_completed()

            resume_disconnectable_signals()
            self.verbose('re-enabling signals (notififcations, websocket alerts)')

        except KeyboardInterrupt:
            self.message('\n\nOperation cancelled...')
            delete = True
        except Exception as e:
            tb = traceback.format_exc()
            delete = True
            # rollback database transaction
            transaction.rollback()
            self.message('Got exception:\n\n%s' % tb)

        if delete:
            self.delete_imported_data()

    def confirm_operation_completed(self):
        # if noinput param do not ask for confirmation
        if self.options.get('noinput') is True:
            return

        self.message("Are you satisfied with the results? If not all imported data will be deleted\n\n[Y/n]")

        while True:
            answer = raw_input().lower()
            if answer == '':
                answer = "y"

            if answer in ['y', 'n']:
                break
            else:
                self.message("Please respond with one of the valid answers\n")

        if answer == 'n':
            self.delete_imported_data()
        else:
            self.message('Operation completed!')

    def delete_imported_data(self):
        if self.options.get('nodelete') is True:
            self.message('--nodelete option specified, won\'t delete the imported data')
            return

        self.message('Going to delete all the imported data...')

        for interface in self.saved_interfaces:
            try:
                interface.delete()
            except Exception as e:
                tb = traceback.format_exc()
                self.message('Got exception while deleting interface %s\n\n%s' % (interface.mac, tb))

        for device in self.saved_devices:
            try:
                device.delete()
            except Exception as e:
                tb = traceback.format_exc()
                self.message('Got exception while deleting device %s\n\n%s' % (device.name, tb))

        for routing_protocol in self.routing_protocols_added:
            try:
                routing_protocol.delete()
            except Exception as e:
                tb = traceback.format_exc()
                self.message('Got exception while deleting routing_protocol %s\n\n%s' % (routing_protocol.name, tb))

        for node in self.saved_nodes:
            try:
                node.delete()
            except Exception as e:
                tb = traceback.format_exc()
                self.message('Got exception while deleting node %s\n\n%s' % (node.name, tb))

        try:
            User.objects.filter(username='guifinet').delete()
        except Exception as e:
            tb = traceback.format_exc()
            self.message('Got exception while deleting user %s\n\n%s' % (user.username, tb))

    def prompt_layer_selection(self, node, layers):
        """Ask user what to do when an old node is contained in more than one layer.
        Possible answers are:
            * use default layer (default answer if pressing enter)
            * choose layer
            * discard node
        """
        valid = {
            "default": "default",
            "def":     "default",
            "discard": "discard",
            "dis":     "discard",
        }
        question = """Cannot automatically determine layer for node "%s" because there \
are %d layers available in that area, what do you want to do?\n\n""" % (node.name, len(layers))

        available_layers = ""
        for layer in layers:
            available_layers += "%d (%s)\n" % (layer.id, layer.name)
            valid[str(layer.id)] = layer.id

        prompt = """\
choose (enter the number of) one of the following layers:
%s
"default"    use default layer (if no default layer specified in settings node will be discarded)
"discard"    discard node

(default action is to use default layer)\n\n""" % available_layers

        sys.stdout.write(question + prompt)

        while True:
            if self.options.get('noinput') is True:
                answer = 'default'
                break

            answer = raw_input().lower()
            if answer == '':
                answer = "default"

            if answer in valid:
                answer = valid[answer]
                break
            else:
                sys.stdout.write("Please respond with one of the valid answers\n")

        sys.stdout.write("\n")
        return answer

    def check_status_mapping(self):
        """ ensure status map does not contain status values which are not present in DB """
        self.verbose('checking status mapping...')

        if not self.status_mapping:
            self.message('no status mapping found')
            return

        for old_val, new_val in self.status_mapping.iteritems():
            try:
                # look up by slug if new_val is string
                if isinstance(new_val, basestring):
                    lookup = { 'slug': new_val }
                # lookup by primary key otherwise
                else:
                    lookup = { 'pk': new_val }
                status = Status.objects.get(**lookup)
                self.status_mapping[old_val] = status.id
            except Status.DoesNotExist:
                raise ImproperlyConfigured('Error! Status with slug %s not found in the database' % new_val)

        self.verbose('status map correct')

    # TODO: adaptar value
    def get_status(self, value):
        return self.status_mapping.get(value, self.status_mapping['default'])

    def retrieve_nodes(self):
        """ retrieve nodes from CNML file """
        self.verbose('retrieving nodes from CNML file...')

        filename = '26494.cnml'
        self.cnmlp = libcnml.CNMLParser(filename)
        self.old_nodes = self.cnmlp.getNodes()
        self.message('retrieved %d nodes' % len(self.old_nodes))

    # TODO: Borrar referencias a email_set y users_dict
    def extract_users(self):
        """ extract user info """
        email_set = set()
        users_dict = {}

    # Como CNML no contiene usuarios, importar con un único usuario "guifinet"
    def import_users(self):
        """ create guifinet user """
        self.message('creating guifinet user')

        email = 'invalid@guifi.net'
        username = 'guifinet'

        # check if user exists first
        try:
            # try looking by email
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            try:
                # try looking by username
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                # otherwise init new
                user = User()
                # generate new password only for new users
                user.password = username

        # we'll create one unique user for guifi.net
        user.username = username
        user.first_name = 'GUIFI.NET'
        user.last_name = 'GUIFI.NET'
        user.email = email
        user.is_active = True
        user.date_joined = None  # TODO: DateTimeField

        # be sure username is unique
        counter = 1
        original_username = username
        while True:
            # do this check only if user is new
            if not user.pk and User.objects.filter(username=user.username).count() > 0:
                counter += 1
                user.username = '%s%d' % (original_username, counter)
            else:
                break

        try:
            # validate data and save
            user.full_clean()
            user.save()
        except Exception as e:
            # if user already exists use that instance
            if(User.objects.filter(email=email).count() == 1):
                user = User.objects.get(email=email)
            # otherwise report error
            else:
                user = None
                tb = traceback.format_exc()
                self.message('Could not save user %s, got exception:\n\n%s' % (user.username, tb))

        # mark email address as confirmed if feature is enabled
        if EMAIL_ADDRESS_APP_INSTALLED and EmailAddress.objects.filter(email=user.email).count() is 0:
            try:
                email_address = EmailAddress(user=user, email=user.email, verified=True, primary=True)
                email_address.full_clean()
                email_address.save()
            except Exception as e:
                tb = traceback.format_exc()
                self.message('Could not save email address for user %s, got exception:\n\n%s' % (user.username, tb))

        self.message('created user into local DB')

    def import_nodes(self):
        """ import nodes into local DB """
        self.message('saving nodes into local DB...')

        saved_nodes = []

        # loop over all old node and create new nodes
        for old_node in self.old_nodes:

            # TODO: Comprobar también el nombre (no solo id)
            try:
                node = Node.objects.get(pk=old_node.id)
            except Node.DoesNotExist:
                node = Node(id=old_node.id)
                node.data = {}

            node.user_id = 'guifinet'
            node.name = old_node.title
            node.slug = old_node.title
            node.geometry = Point(old_node.longitude, old_node.latitude)
            node.elev = 0  # TODO: Altitud en CNMLs!!
            node.description = 'Description'  # TODO: Descripción en CNMLs!
            node.notes = 'Notes'
            node.added = None  # TODO: DateTimeField
            node.updated = None  # TODO: DateTimeField

            if LAYER_APP_INSTALLED:
                intersecting_layers = node.intersecting_layers
                # if more than one intersecting layer
                if len(intersecting_layers) > 1:
                    # prompt user
                    answer = self.prompt_layer_selection(node, intersecting_layers)
                    if isinstance(answer, int):
                        node.layer_id = answer
                    elif answer == 'default' and self.default_layer is not False:
                        node.layer_id = self.default_layer
                    else:
                        self.message('Node %s discarded' % node.name)
                        continue
                # if one intersecting layer select that
                elif 2 > len(intersecting_layers) > 0:
                    node.layer = intersecting_layers[0]
                # if no intersecting layers
                else:
                    if self.default_layer is False:
                        # discard node if no default layer specified
                        self.message("""Node %s discarded because is not contained
                                     in any specified layer and no default layer specified""" % node.name)
                        continue
                    else:
                        node.layer_id = self.default_layer

            # determine status according to settings
            if self.status_mapping:
                node.status_id = self.get_status(old_node.status)

            try:
                node.full_clean()
                node.save(auto_update=False)
                saved_nodes.append(node)
                self.verbose('Saved node %s in layer %s with status %s' % (node.name, node.layer, OldStatus.statusToStr(node.status))
            except Exception as e:
                tb = traceback.format_exc()
                self.message('Could not save node %s, got exception:\n\n%s' % (node.name, tb))

        self.message('saved %d nodes into local DB' % len(saved_nodes))
        self.saved_nodes = saved_nodes

    def import_devices(self):
        self.verbose('retrieving devices from CNML...')
        self.old_devices = self.cnmlp.getDevices()
        self.message('retrieved %d devices' % len(self.old_devices))

        saved_devices = []
        routing_protocols_added = []

        for old_device in self.old_devices:
            try:
                device = Device.objects.get(pk=old_device.id,)
            except Device.DoesNotExist:
                device = Device(id=old_device.id)

            device.node_id = old_device.parentNode.id
            device.type = 'radio'
            device.name = old_device.name
            device.description = 'Description'
            device.added = None  # DateTimeField
            device.updated = None  # DateTimeField
            device.data = {
                "model": old_device.type,
                "cname": old_device.name  # cname?
            }

            try:
                device.full_clean()
                device.save(auto_update=False)
                saved_devices.append(device)
                self.verbose('Saved device %s' % device.name)
            except Exception as e:
                tb = traceback.format_exc()
                self.message('Could not save device %s, got exception:\n\n%s' % (device.name, tb))

            try:
                # FIXME: CNML no incluye protocolo de routing :(( ??
                routing_protocol = RoutingProtocol.objects.filter(name__icontains='olsr')[0]
            except IndexError:
                routing_protocol = RoutingProtocol.objects.create(name='olsr')
                routing_protocols_added.append(routing_protocol)
            device.routing_protocols.add(routing_protocol)

        self.message('saved %d devices into local DB' % len(saved_devices))
        self.saved_devices = saved_devices
        self.routing_protocols_added = routing_protocols_added

    def import_interfaces(self):
        self.verbose('retrieving interfaces from CNML...')
        self.old_interfaces = self.cnmlp.getInterfaces()
        self.message('retrieved %d interfaces' % len(self.old_interfaces))

        saved_interfaces = []
        saved_vaps = []
        saved_ipv4 = []
        saved_ipv6 = []

        for old_interface in self.old_interfaces:
            interface_dict = {
                "id": old_interface.id,
                "device_id": int(old_interface.parentRadio.parentDevice.id),
                "mac": old_interface.mac,
                "name": 'Una interfaz',
                "added": None,  # TODO: DateTimeField
                "updated": None,  # TODO: DateTimeField
                "data": {}
            }
            vap = None
            ipv4 = None
            ipv6 = None

            # determine interface type and specific fields
            if old_interface.type == 'Lan':
                interface_dict['standard'] = 'fast'
                interface_dict['duplex'] = 'full'
                InterfaceModel = Ethernet
            elif old_interface.type == 'wLan/Lan':
                interface_dict['mode'] = old_interface.parentRadio.mode  # 'ap'. igual no es esto...
                interface_dict['channel'] = old_interface.parentRadio.channel
                InterfaceModel = Wireless
                # determine ssid

                #if old_interface.essid or old_interface.bssid:
                if old_interface.parentRadio.ssid:  # bssid??
                    vap = Vap(**{
                        "interface_id": old_interface.id,
                        "essid": old_interface.parentRadio.ssid,
                        "bssid": '11:11:11:11:11:11'  # FIXME
                    })
                    # if vap already exists flag it for UPDATE instead of INSERT
                    try:
                        v = Vap.objects.get(
                            Q(interface_id=old_interface.id) & (
                                Q(essid=old_interface.parentRadio.ssid) |
                                Q(bssid='11:11:11:11:11:11')
                            )
                        )
                        # trick to make django do an update query instead of an insert
                        # working on django 1.6
                        vap.id = v.id
                        vap._state.adding = False
                    except Vap.DoesNotExist:
                        pass
                if old_interface.parentRadio.ssid:
                    interface_dict['data']['essid'] = old_interface.parentRadio.ssid
                #if old_interface.bssid:
                #    interface_dict['data']['bssid'] = old_interface.bssid
            #elif old_interface.type == 'bridge':
            #    InterfaceModel = Bridge
            #elif old_interface.type == 'vpn':
            #    InterfaceModel = Tunnel
            else:
                interface_dict['type'] = INTERFACE_TYPES.get('virtual')
                # FIXME: get_type_display() ?!
                # interface_dict['data']['old_nodeshot_interface_type'] = old_interface.get_type_display()
                InterfaceModel = Interface

            interface = InterfaceModel(**interface_dict)
            # if interface already exists flag it for UPDATE instead of INSERT
            try:
                InterfaceModel.objects.get(pk=old_interface.id)
                interface._state.adding = False
            except InterfaceModel.DoesNotExist:
                pass

            if old_interface.ipv4:
                ipv4 = Ip(**{
                    "interface_id": old_interface.id,
                    "address": old_interface.ipv4
                })
                # if ip already exists flag it for UPDATE instead of INSERT
                try:
                    ipv4.id = Ip.objects.get(address=old_interface.ipv4).id
                    ipv4._state.adding = False
                except Ip.DoesNotExist:
                    pass
                # ensure ipv4 is valid
                try:
                    ip.IPAddress(old_interface.ipv4)
                except (ip.AddrFormatError, ValueError):
                    self.message('Invalid IPv4 address %s' % (old_interface.ipv4))
                    ipv4 = None

            # No IPv6

            try:
                interface.full_clean()
                interface.save(auto_update=False)
                saved_interfaces.append(interface)
                self.verbose('Saved interface %s' % interface.name)
            except Exception as e:
                tb = traceback.format_exc()
                self.message('Could not save interface %s, got exception:\n\n%s' % (interface.mac, tb))
                continue

            if vap:
                try:
                    vap.full_clean()
                    vap.save()
                    saved_vaps.append(vap)
                    self.verbose('Saved vap %s' % vap.essid or vap.bssid)
                except Exception as e:
                    tb = traceback.format_exc()
                    self.message('Could not save vap %s, got exception:\n\n%s' % (vap.essid or vap.bssid, tb))

            if ipv4:
                try:
                    ipv4.full_clean()
                    ipv4.save()
                    saved_ipv4.append(ipv4)
                    self.verbose('Saved ipv4 %s' % ipv4.address)
                except Exception as e:
                    tb = traceback.format_exc()
                    self.message('Could not save ipv4 %s, got exception:\n\n%s' % (ipv4.address, tb))

        self.message('saved %d interfaces into local DB' % len(saved_interfaces))
        self.message('saved %d vaps into local DB' % len(saved_vaps))
        self.message('saved %d ipv4 addresses into local DB' % len(saved_ipv4))
        self.saved_interfaces = saved_interfaces
        self.saved_vaps = saved_vaps
        self.saved_ipv4 = saved_ipv4

    def import_links(self):
        self.verbose('retrieving links from CNML...')
        self.old_links = self.cnmlp.getLinks()
        self.message('retrieved %d links' % len(self.old_links))

        saved_links = []

        for old_link in self.old_links:

            skip = False

            try:
                interface_a = Interface.objects.get(pk=old_link.interfaceA)
                # FIXME: ????
                if interface_a.type != INTERFACE_TYPES.get('wireless'):
                    interface_a.type = INTERFACE_TYPES.get('wireless')
                    interface_a.save()
            except Interface.DoesNotExist:
                self.message('Interface #%s does not exist, probably link #%s is orphan!' % (old_link.from_interface_id, old_link.id))
                skip = True

            try:
                interface_b = Interface.objects.get(pk=old_link.interfaceB)
                # FIXME: ????
                if interface_b.type != INTERFACE_TYPES.get('wireless'):
                    interface_b.type = INTERFACE_TYPES.get('wireless')
                    interface_b.save()
            except Interface.DoesNotExist:
                self.message('Interface #%s does not exist, probably link #%s is orphan!' % (old_link.to_interface_id, old_link.id))
                skip = True

            if skip:
                self.verbose('Skipping to next cycle')
                continue

            #old_bandwidth = [old_link.sync_tx, old_link.sync_rx]  # No en CNML
            old_bandwidth = [50, 50]

            link = Link(**{
                "id": old_link.id,
                "interface_a": interface_a,
                "interface_b": interface_b,
                "status": LINK_STATUS.get('active'),  # TODO
                "type": LINK_TYPES.get('radio'),  # TODO
                "metric_type": 'etx',
                #"metric_value": old_link.etx,  # TODO
                "metric_value": '123',
                #"dbm": old_link.dbm,  # TODO
                "dbm": '16',
                "min_rate": min(old_bandwidth),
                "max_rate": max(old_bandwidth),
            })
            # if link already exists flag it for UPDATE instead of INSERT
            try:
                Link.objects.get(pk=old_link.id)
                link._state.adding = False
            except Link.DoesNotExist:
                pass

            # FIXME: ???
            #if old_link.hide:
            #    link.access_level = 3

            try:
                link.full_clean()
                link.save()
                saved_links.append(link)
                self.verbose('Saved link %s' % link)
            except Exception as e:
                tb = traceback.format_exc()
                self.message('Could not save link %s, got exception:\n\n%s' % (old_link.id, tb))

        self.message('saved %d links into local DB' % len(saved_links))
        self.saved_links = saved_links
