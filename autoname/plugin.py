'''
Reference
https://docs.gtk.org/gio/class.Settings.html
https://docs.gtk.org/gio/struct.SettingsSchemaSource.html
https://github.com/Brick85/rhythmbox-vk/blob/master/vk.py
https://github.com/johnfactotum/gedit-restore-minimap/blob/master/restore_minimap.py
https://github.com/seanh/gedit-autoname
https://github.com/theawless/Dict-O-nator/blob/93d14a161f2bcccf44670902fa0864af88786653/dictonator/settings.py#L102
https://gitlab.gnome.org/GNOME/gedit-plugins/-/blob/master/plugins/translate/translate/settings.py
https://pygobject.readthedocs.io/en/latest/guide/gtk_template.html 
https://pygobject.readthedocs.io/en/latest/index.html?highlight=Gio#
https://python-gtk-3-tutorial.readthedocs.io/en/latest/builder.html
https://wiki.gnome.org/Apps/Gedit/PythonPluginHowTo
https://wiki.gnome.org/HowDoI/ChooseApplicationID
https://www.geek-share.com/detail/2657458446.html
https://www.micahcarrick.com/gsettings-python-gnome-3.html
'''


# standard libs
import datetime
import logging
import os
import pathlib
import re
import uuid

# gedit/gnome libs
import gi
from gi.repository import (
    Gedit,
    Gio,
    GObject,
    Gtk,
    PeasGtk
)
gi.require_version("Gtk", "3.0")

__all__ = ["AutonamePlugin"]

UI_FILEPATH = os.path.join(os.path.dirname(__file__), "settings.ui")

# Set $DEV_MODE environment variable to show debug logging messages
DEV_MODE = os.environ.get("GEDIT_PLUGINS_AUTONAME_DEV_MODE")
if DEV_MODE:
    logging.getLogger().setLevel(logging.DEBUG)


class AutonamePlugin(GObject.Object, Gedit.WindowActivatable, PeasGtk.Configurable):
    '''
    On a side note, I found it interesting (frustrating) that I could not set instance attributes
    during `__init__` (or even  `do_activate`), and have those attributes available on the instance
    later on (e.g. during `do_create_configure_widget`).  It appears that this class is 
    instantiated multiple times. Once when it is loaded, and then every time you press the 
    "Preferences" button in the Plugins tab of the gedit preferences.
    '''
    __gtype_name__ = "AutonamePlugin"

    APP_ID = 'org.gnome.gedit.plugins.autoname'

    REGEX = r'^{dirpath}/autoname-\d{{8}}-\w{{6}}$'  # e.g. ~/autoname-20210918-ef3s2g

    window = GObject.property(type=Gedit.Window)

    def __init__(self):
        GObject.Object.__init__(self)

    # ------------------------------------------------
    # Interface Methods
    # ------------------------------------------------

    def do_activate(self):
        '''
        This method is automatically called by gedit when loading the plugin.

        Register handers for specific gedit events. Specifically..
            - When a tab (document) is created
            - When a tab (document) is closed
        '''
        logging.debug("AutonamePlugin.do_activate")
        self.window.autoname_plugin_handler_ids = (
            self.window.connect("tab-added", self._tab_added),
            self.window.connect("tab-removed", self._tab_removed),
        )

    def do_deactivate(self):
        '''
        This method is automatically called by gedit when unloading the plugin.
        '''
        for handler_id in self.window.autoname_plugin_handler_ids:
            self.window.disconnect(handler_id)

    def do_create_configure_widget(self):
        '''
        This method is called by gedit to fetch a Gtk widget of config options for the plugin.
        '''
        logging.debug("AutonamePlugin.do_create_configure_widget")
        return SettingsBox(self._get_settings())

    # ------------------------------------------------
    # Event Handlers
    # ------------------------------------------------

    def _tab_added(self, window, tab):
        '''
        Handler for 'tab-added' event.
        '''
        logging.debug("AutonamePlugin.tab_added")
        document = tab.get_document()
        if document.is_untitled():
            self._autoname_document(document)
            Gedit.commands_save_document_async(document, window)

    def _tab_removed(self, window, tab):
        '''
        Handler for 'tab-removed' event.

        When an autonamed document is closed, delete if it's empty.
        '''
        logging.debug("AutonamePlugin.tab_removed")
        document = tab.get_document()
        filepath = location.get_path() if (location := document.get_file().get_location()) else None
        logging.debug("\ttab_removed: filepath: %s", filepath)

        if filepath and self._is_autonamed(filepath) and self._is_empty(document):
            try:
                logging.debug("\tDeleting empty autonamed document: %s", filepath)
                os.remove(filepath)
            except FileNotFoundError:
                logging.exception("Failed to delete autonamed document: %s" % filepath)

    # ------------------------------------------------
    # HELPERS
    # ------------------------------------------------

    def _get_settings(self):
        '''
        Fetch the settings for autoname plugin. 
        '''
        logging.debug("AutonamePlugin.get_settings")
        # Find the location of settings schema file
        schema_source = Gio.SettingsSchemaSource.new_from_directory(
            self.plugin_info.get_data_dir(),  # The directory to search for the schema file
            Gio.SettingsSchemaSource.get_default(),  # the parent schema to use if schema file is not found.
            False,  # Whether or not to "trust" that the schema is present/valid.
        )
        # Instantiate and return the Settings for the app
        schema = schema_source.lookup(self.APP_ID, False)
        return Gio.Settings.new_full(
            schema,
            None,  # settingsBackend
            None,  # The path to the app's settings (if not found in the schema file).
        )

    def _get_newfile_directory(self):
        '''
        Fetch the new-file directory that has speen specified in the user settings.
        '''
        logging.debug("AutonamePlugin._get_newfile_directory")
        return self._get_settings().get_string(SettingsBox._SETTING_NEW_FILE_DIRPATH) or pathlib.Path.home()

    def _autoname_document(self, document):
        '''
        Generate a filepath for the given document, assign it to the document (location), and 
        write the file to disk (must write to disk in order for autosave to work).
        '''
        logging.debug("AutonamePlugin._autoname_document")
        filepath = os.path.join(
            self._get_newfile_directory(),
            "autoname-%s-%s" % (datetime.datetime.now().strftime("%Y%m%d"), str(uuid.uuid4())[-6:])
        )
        logging.debug("\tAutonaming document as: %s", filepath)
        document.get_file().set_location(Gio.file_new_for_path(filepath))

    def _is_empty(self, document):
        '''
        Check whether the given document is "empty". A document of only whitespace is considered
        empty.
        '''
        logging.debug("AutonamePlugin._is_empty")
        char_count = document.get_char_count()

        # if there's less than 100 characters in the document, see if it's just whitespace.
        if char_count < 100:
            text = document.get_text(
                document.get_start_iter(),
                document.get_iter_at_offset(char_count),
                False,
            )
            # Return False if there's no chars (after stripping whitespace)
            return not bool(text.strip())

    def _is_autonamed(self, filepath):
        '''
        Return True if the given filepath has been using the autonaming template
        '''
        logging.debug("AutonamePlugin._is_autonamed")
        regex = self.REGEX.format(dirpath=self._get_newfile_directory())
        logging.debug("\tregex: %s", regex)
        logging.debug("\tfilepath: %s", filepath)
        return re.match(regex, filepath)


@Gtk.Template(filename=UI_FILEPATH)
class SettingsBox(Gtk.Box):
    '''
    Box widget for showing/setting plugin settings
    '''
    _SETTING_NEW_FILE_DIRPATH = "new-file-dirpath"

    __gtype_name__ = "SettingsBox"

    _folder_chooser = Gtk.Template.Child("folder_chooser")

    def __init__(self, settings, **kwargs):
        '''
        settings: GtkSettings object.  The user's settings object to store/load plugin 
        '''
        self._settings = settings
        super().__init__(**kwargs)

        # Initialize the folder-chooser button by populating it with whatever the user had in
        # their settings (otherwise set it to their home directory).
        dirpath = self._settings.get_string(self._SETTING_NEW_FILE_DIRPATH) or pathlib.Path.home()
        self._folder_chooser.set_current_folder(dirpath)

        # Connect the folder-chooser button to update our plugin settings every time the user
        # selects a new folder to save to.
        self._folder_chooser.connect("file-set", self._directory_selected)

    def _directory_selected(self, chooser):
        '''
        Handler for "file-set" event (when the user selects a directory to save to).  Save the
        user's selection the user settings.  
        '''
        self._settings.set_string(self._SETTING_NEW_FILE_DIRPATH, chooser.get_filename())
