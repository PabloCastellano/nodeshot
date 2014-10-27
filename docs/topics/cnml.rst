*********************
Import data from CNML
*********************

The new version provides an integrated tool that allows to import data from CNML
file format.

=====================
Internal dependencies
=====================

For the **cnml** module to work, the following apps must be listed in
``settings.INSTALLED_APPS``:

 * nodeshot.core.nodes
 * nodeshot.core.layers
 * nodeshot.networking.net
 * nodeshot.networking.links
 * nodeshot.community.mailing
 * nodeshot.community.profiles

By default these apps are included in ``nodeshot.conf.settings`` so you won't need to do anything.

===========
Preparation
===========

----------------
1. Create Layers
----------------

Then ensure you have some layers defined in your admin interface.
Open the browser and go to **/admin/layers/layer** (or follow the links from the
admin index), if you see any layer defined, you are ready to proceed, if not you
should create one or more layers.

If you **specify the area of each layer**, the importer will be able to insert the
old nodes into the right layer. It's a good thing to do it!

If you don't want to lose any node, you should create a **default layer** in which
the script will automatically put all those old nodes which have coordinates that
are not comprised in any of your newly created layers.

**If no default layer is specified the nodes which have coordinates not comprised
in any layer will be discarded.**

=====================
Enable in settings.py
=====================

Uncomment the following section in ``settings.py`` and tweak the settings
``ENGINE``, ``NAME``, ``USER``, ``PASSWORD``, ``HOST`` and ``PORT``
according to your configuration:

And set ``NODESHOT_OLDIMPORTER_DEFAULT_LAYER`` (object id/primary key) to your default layer:

.. code-block:: python

    NODESHOT_OLDIMPORTER_DEFAULT_LAYER = <id>

Replace ``<id>`` with the id of your default layer.

If you followed exactly the instructions in this document you can leave the default
``NODESHOT_CNML_STATUS_MAPPING`` setting unchanged.

===========
Import data
===========

.. warning::
    The first import should start with a clean database

First of all, enable your python-virtualenv if you haven't already::

    workon nodeshot

Ready? Go!::

    python manage.py import_cnml

If you want to see what the importer is doing behind the scenes raise the verbosity level::

    python manage.py import_cnml --verbosity=2

If you want to save the output for later inspection try this::

    python manage.py import_cnml --verbosity=2 | tee import_result.txt

Wait for the importer to import your data, when it finishes it will ask you if you
are satisfied with the results or not, if you enter "No" the importer will delete all
the imported records.

**If the importer runs into an uncaught exception it will automatically delete all the imported data**.

If you get such an error notify us and we'll try to fix it.

In case you don't want the importer data to be deleted you can use the ``--nodelete`` option.

===============
Command options
===============

 * ``--verbosity``: verbosity level, can be 0 (no output), 1 (default), 2 (verbose), 3 (very verbose)
 * ``--noinput``: suppress all user prompts
 * ``--nodelete``: do not delete imported data in case of errors

=============
Periodic sync
=============

You can run the importer periodically and it will try to import new data.

This process can be handy while you test the new version but before you launch
your service to your audience we advise to reset everything and run the importer
again on a clean database.

It is better to specify the ``--nodelete`` option in order to avoid automatic deletion of data in case of errros::

    python manage.py import_old_nodeshot --nodelete

To automate the periodic import add the following dictionary in your ``CELERYBEAT_SCHEDULE`` setting::

    CELERYBEAT_SCHEDULE = {

        # ...

        'import_old_nodeshot': {
           'task': 'nodeshot.interop.oldimporter.tasks.import_old_nodeshot',
           'schedule': timedelta(hours=12),
           # pass --noinput and --nodelete options
           'kwargs': { 'noinput': True, 'nodelete': True }
        },

        # ...

    }

This assumes that celery and celerybeat are configured and running correctly.

===========================
How does the importer work?
===========================

Let's explain some technical details, the flow can be divided in 7 steps.

--------------------------
1. Retrieve all nodes
--------------------------

The first thing the script will do is to retrieve all the nodes from the old database
and convert the queryset in a python list that will be used in the next steps.

-------------------------------
2. Extract user data from nodes
-------------------------------

Since in old nodeshot there are no users but each node contains data
such as name, email, and stuff like that, the script will create user accounts:

 * loop over nodes and extract a list of unique emails
 * each unique email will be a new user in the new database
 * each new user will have a random password set
 * save users, email addresses

---------------
3. Import nodes
---------------

    * **USER**: assign owner (the link is the email)
    * **LAYER**: assign layer (layers must be created by hand first!):
        1. if node has coordinates comprised in a specified layer choose that
        2. if node has coordinates comprised in more than one layer prompt the user which one to choose
        3. if node does not have coordinates comprised in any layer:
            1. use default layer if specified (configured in settings)
            2. discard the node if no default layer specified
    * **STATUS**: assign status depending on configuration:
        ``settings.NODESHOT_OLDIMPORTER_STATUS_MAPPING`` must be a dictionary in which the
        key is the old status value while the value is the new status value
        if ``settings.NODESHOT_OLDIMPORTER_STATUS_MAPPING`` is False the default status will be used
    * **HOSTPOT**: if status is hotspot or active and hotspot add this info in the *HSTORE* data field

-----------------
4. Import devices
-----------------

In this step the script will import devices and create any missing routing protocol.

-----------------------------------------
5. Import interfaces, ip addresses, vaps
-----------------------------------------

In this step the script will import all interfaces, ip addresses and other detailed device info.

----------------
6. Import links
----------------

In this step the script will import all the available links.

-------------------
7. Import Contacts
-------------------

In this step the script will import the contact logs.
