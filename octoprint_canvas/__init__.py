# coding=utf-8
from __future__ import absolute_import
from distutils.version import LooseVersion

import re
import requests
import octoprint.plugin
import flask
import datetime
import time
from . import Canvas
from . import constants
from . import MQTT
import os
from . import CanvasErrors
import platform
import logging
import sys
import octoprint.server
import threading
import subprocess
from subprocess import call

try:
    from ruamel.yaml import YAML
except ImportError:
    from ruamel.yaml.main import YAML
yaml = YAML(typ="safe")
yaml.default_flow_style = False

reM106 = re.compile("^M106.* S(\d+\.?\d*).*")

def getLog(msg, module='init canvas'):
    return '{ "msg": "' + msg + '", "module": "' + module +'" }'

 # For executing a shell command

def ping(host):
    """
    Returns True if host (str) responds to a ping request.
    Remember that a host may not respond to a ping (ICMP) request even if the host name is valid.
    """

    # Option for the number of packets as a function of
    param = '-n' if platform.system().lower()=='windows' else '-c'

    # Building the command. Ex: "ping -c 1 google.com"
    command = ['ping', param, '1', host]

    return subprocess.call(command) == 0


class CanvasPluginVersionChecker:
    def __init__(self):
        pass

    def get_latest(self, target, check, full_data=False, online=True):
        resp = requests.get(constants.GET_RELEASES_URL)
        version_data = resp.json()
        latest_version_info = version_data[0]
        version = latest_version_info['name']
        current_version = check.get("current")
        information = dict(
            local=dict(
                name=current_version,
                value=current_version,
            ),
            remote=dict(
                name=version,
                value=version
            )
        )
        needs_update = LooseVersion(current_version) < LooseVersion(version)

        return information, not needs_update


class CanvasPlugin(octoprint.plugin.TemplatePlugin,
                   octoprint.plugin.AssetPlugin,
                   octoprint.plugin.StartupPlugin,
                   octoprint.plugin.ShutdownPlugin,
                   octoprint.plugin.SimpleApiPlugin,
                   octoprint.plugin.EventHandlerPlugin,
                   octoprint.plugin.SettingsPlugin):

    def __init__(self):
        self.hub_yaml = None
        self.logger = None
        self.initialized = False
        self.connectionThread = None

    # STARTUPPLUGIN
    def on_after_startup(self):
            mosaic_log = os.path.join(self._settings.getBaseFolder('logs'), 'mosaic.log')
            self.logger = self._setup_logger("canvas", mosaic_log)
            self.logger.info(getLog('---------- Starting Canvas Plugin ----------'))
            system = platform.system()
            try:
                if system == 'Linux':
                    self.logger.critical(getLog(str(platform.system()) + ' - ' + str(
                        platform.linux_distribution())))
                elif system == 'Windows':
                    self.logger.critical(getLog(str(platform.system()) + ' - ' + str(platform.version())))
            except Exception as e:
                self.logger.error(getLog('Error on after startup: ', str(e)))
            self.logger.critical(getLog('Python - ' + sys.version))
            self.logger.critical(getLog('OctoPrint: ' + str(octoprint.server.VERSION)))
            self.errors = CanvasErrors.CanvasErrors(self)
            self.logger.debug('ping google: ' + str(ping('google.com')))
            if ping('google.com') and not self.initialized:
                self.init_canvas()
            else:
                self._startConnectionThread()

    def _startConnectionThread(self):
        self.logger.debug(getLog('starting connection thread'))
        if self.connectionThread is None:
            self.connectionThread = threading.Thread(target=self._connection)
            self.connectionThread.daemon = True
            self.connectionThread.start()

    def _connection(self):
        self.logger.debug('connection thread started')
        self.logger.debug('connection 0: ' + str(ping('google.com')))
        while ping('google.com') == False:
            time.sleep(3)
            self.logger.debug('connection: ' + str(ping('google.com')))
            if ping('google.com') and not self.initialized:
                self.init_canvas()
        return

    def init_canvas(self):
        self.canvas = Canvas.Canvas(self)
        self.canvas.checkForRuamelVersion()
        self.canvas.isHubS = self.canvas.determineHubVersion()
        self.canvas.checkFor0cf0()
        self.canvas.checkIfRootCertExists()
        self.canvas.updatePluginVersions()
        self.canvas.checkForRegistrationAndVersion()
        self.canvas.updateUsersOnUI()
        self.canvas.updateIotConnectionOnUI()
        self.initialized = True
        self.logger.debug('done initializing canvas')


    #SHUTDOWNPLUGIN
    def on_shutdown(self):
        self.canvas.mqtt.mqtt_disconnect(force=True)

    # TEMPLATEPLUGIN
    def get_template_configs(self):
        return [
            dict(type="navbar", custom_bindings=False),
            dict(type="settings", custom_bindings=False)
        ]

    # ASSETPLUGIN
    def get_assets(self):
        return dict(
            css=["css/canvas.css"],
            js=["js/canvas.js", "js/utils/alerts.js", "js/utils/ui.js"],
            less=["less/canvas.less"]
        )

    def get_settings_defaults(self):
        return dict(applyTheme=True, importantUpdate=True)

    def get_update_information(self):
        # Define the configuration for your plugin to use with the Software Update
        # Plugin here. See https://github.com/foosel/OctoPrint/wiki/Plugin:-Software-Update
        # for details.
        return dict(
            canvas=dict(
                displayName="Canvas Plugin",
                displayVersion=self._plugin_version,
                current=self._plugin_version,
                type="python_checker",
                python_checker=CanvasPluginVersionChecker(),
                pip=constants.RELEASE_ZIP_URL
            )
        )

    def handle_gcode_sent(self, comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None):
        try:
            if gcode == "M107":
                # M107 -- set tracked fan speed to 0
                self.canvas.mqtt.mqttRouter.fan_state = 0
                self.canvas.mqtt.mqttRouter.broadcast_counter = -1
                return
            if gcode == "M106":
                # M106 Snn -- nn in [0..255] -- set tracked speed in [0..100]
                match = reM106.match(cmd)
                if match:
                    pwm_str = match.group(1)
                    pwm = float(pwm_str)
                    percentage = round(pwm * 100.0 / 255.0)
                    self.canvas.mqtt.mqttRouter.fan_state = percentage
                    self.canvas.mqtt.mqttRouter.broadcast_counter = -1
                return
            if gcode == "M17":
                self.canvas.mqtt.mqttRouter.motor_state = True
                self.canvas.mqtt.mqttRouter.broadcast_counter = -1
                return
            if gcode == "M18":
                self.canvas.mqtt.mqttRouter.motor_state = False
                self.canvas.mqtt.mqttRouter.broadcast_counter = -1
                return
        except Exception as e:
            if self.logger is not None:
                self.logger.info(e)


    # SIMPLEAPIPLUGIN POST, runs first before on_api_commands, responds to commands from palette,js, any strings inside array = mandatory
    def get_api_commands(self):
        return dict(
            addUser=["data"],
            unlinkUser=["data"],
            changeImportantUpdateSettings=["condition"],
            resetCanvasData=["data"]
        )

    # SIMPLEAPIPLUGIN POST, to handle commands listed in get_api_commands
    def on_api_command(self, command, payload):
        self.logger.info('onapicommand: ' + 'command: ' + str(command) + ', payload: ' + str(
            payload))
        try:
            if command == "addUser":
                self.canvas.addUser()
            elif command == "unlinkUser":
                self.canvas.unlinkUser()
            elif command == "changeImportantUpdateSettings":
                self.canvas.changeImportantUpdateSettings(payload["condition"])
            elif command == "resetCanvasData":
                self.canvas.resetCanvasData()
            response = "POST request (%s) successful" % command
            return flask.jsonify(response=response, status=constants.API_SUCCESS), constants.API_SUCCESS
        except Exception as e:
            error = str(e)
            self.logger.info("Exception message: %s" % str(e))
            return flask.jsonify(error=error, status=constants.API_FAILURE), constants.API_FAILURE

    # EVENTHANDLERPLUGIN
    def on_event(self, event, payload):
        try:
            if "Startup" in event:
                self.displayImportantUpdateAlert = False
            elif "ConnectivityChanged" in event:
                if self.logger:
                    self.logger.debug('connectivity changed: event: ' + str(event) + ', '
                                                                                     'payload: ' + str(payload))
                if not self.initialized and payload['new']:
                    self.init_canvas();
                    self.logger.info('done initializing canvas after connectivity changed')

            elif "ClientOpened" in event:
                if self.canvas:
                    if self.displayImportantUpdateAlert and self._settings.get(["importantUpdate"]):
                        self.canvas.updateUI({"command": "importantUpdate", "data": "x.x.x"})
                    self.canvas.updateUsersOnUI()
                    self.canvas.updateIotConnectionOnUI()
            elif "Shutdown" in event:
                pass
            elif "PrintStarted" in event:
                self.canvas.mqtt.mqttRouter._print_job_start_time = datetime.datetime.utcnow().isoformat()[:-3] + 'Z'
                self.canvas.mqtt.mqttRouter._print_job_file_path = 'device/' + payload['path']
                self.canvas.mqtt.mqttRouter._print_job_file_size = payload['size']
                local_path = os.path.join(self.canvas._settings.getBaseFolder('uploads'), payload['path'])
                timestamp = os.path.getmtime(local_path)
                self.canvas.mqtt.mqttRouter._print_job_file_modified = datetime.datetime.fromtimestamp(
                    timestamp).isoformat()[:-3] + 'Z'
                self.canvas.mqtt.mqttRouter._print_job_file_name = payload['name']
                self.canvas.mqtt.mqttRouter._print_job_status_name = 'start'
            elif "PrintFailed" in event:
                if payload['reason'] != 'cancelled':
                    pass
            elif "PrintCancelling" in event:
                self.canvas.mqtt.mqttRouter._print_job_status_name = 'cancelling'
            elif ("PrintDone" in event) or ("PrintCancelled" in event):
                self.canvas.mqtt.mqttRouter._print_job_start_time = ''
                self.canvas.mqtt.mqttRouter._print_job_file_path = ''
                self.canvas.mqtt.mqttRouter._print_job_file_size = ''
                self.canvas.mqtt.mqttRouter._print_job_file_modified = ''
                self.canvas.mqtt.mqttRouter._print_job_file_name = ''
                self.canvas.mqtt.mqttRouter._print_job_status_name = ''
            elif "PrintPaused" in event:
                self.canvas.mqtt.mqttRouter._print_job_status_name = 'paused'
            elif "PrintResumed" in event:
                self.canvas.mqtt.mqttRouter._print_job_status_name = 'start'
            elif "PrinterStateChanged" in event:
                self.canvas.mqtt.mqttRouter.broadcast_counter = -1
                time.sleep(0.5)
                if payload['state_id'] == 'DETECT_SERIAL' or payload['state_id'] == 'CONNECTING' \
                        or payload['state_id'] == 'NONE' or payload['state_id'] == 'UNKNOWN' \
                        or payload['state_id'] == 'CLOSED' or payload['state_id'] == 'ERROR' \
                        or payload['state_id'] == 'CLOSED_WITH_ERROR' or payload['state_id'] == 'OFFLINE':
                    self.canvas.mqtt.mqttRouter.connected_state = False
                else:
                    self.canvas.mqtt.mqttRouter.connected_state = True
        except Exception as e:
            if self.logger:
                self.logger.error(getLog(str(e)))
                self.logger.error(getLog(str(event)))

    def _loadYAMLFile(self, yaml_file_path):
        yaml_file = open(yaml_file_path, "r")
        yaml_data = yaml.load(yaml_file)
        yaml_file.close()
        return yaml_data

    def _writeYAMLFile(self, yaml_file_path, data):
        yaml_file = open(yaml_file_path, "w")
        yaml.dump(data, yaml_file)
        yaml_file.close()

    def _updateYAMLInfo(self):
        hub_data_path = os.path.expanduser('~') + "/.mosaicdata/canvas-hub-data.yml"
        self._writeYAMLFile(hub_data_path, self.hub_yaml)

    def get_hub_yaml(self):
        if not self.hub_yaml:
            hub_dir_path = os.path.expanduser('~') + "/.mosaicdata"
            hub_file_path = hub_dir_path + "/canvas-hub-data.yml"

            # if /.mosaicdata doesn't exist yet, make the directory
            if not os.path.exists(hub_dir_path):
                os.mkdir(hub_dir_path)

            # if the YML file doesn't exist, make the file
            if not os.path.isfile(hub_file_path):
                self._writeYAMLFile(hub_file_path, constants.DEFAULT_YAML)

            # access yaml file with all the info
            self.hub_yaml = self._loadYAMLFile(hub_file_path)

            # if the yaml file is somehow empty
            if not self.hub_yaml:
                self._writeYAMLFile(hub_file_path, constants.DEFAULT_YAML)
                self.hub_yaml = self._loadYAMLFile(hub_file_path)

            # for compatibility with older hub zero, if the yaml file doesn't have a "canvas-users" key
            if not "canvas-user" in self.hub_yaml and all(key in self.hub_yaml for key in ("canvas-hub", "versions")):
                self.hub_yaml["canvas-user"] = {}
                self._writeYAMLFile(hub_file_path, self.hub_yaml)
                hub_yaml = self._loadYAMLFile(hub_file_path)

            # if, for some reason, yaml file is still missing a property
            if not all(key in self.hub_yaml for key in ("canvas-user", "canvas-hub", "versions")):
                self.logger.info(getLog("Resetting YAML file to default"))
                self._writeYAMLFile(hub_file_path, constants.DEFAULT_YAML)
                hub_yaml = self._loadYAMLFile(hub_file_path)

        return self.hub_yaml

    def replace_hub_yaml(self, data):
        self.hub_yaml = data
        self._updateYAMLInfo()

    def save_hub_yaml(self):
        self._updateYAMLInfo()

    def _writeFile(self, path, content):
        data = open(path, "w")
        data.write(content)
        data.close()

    def _readFile(self, path):
        data = open(path, "r")
        data.read()
        data.close()

    def _setup_logger(self, name, log_file, level=logging.DEBUG):
        formatter = logging.Formatter(
            fmt='{ "product": "canvas-hub", "datetime": "%(asctime)s.%(msecs)03dZ", "plugin": "%('
                'name)s", '
                '"type": "%(levelname)s", "details": %(message)s }', datefmt='%Y-%m-%dT%H:%M:%S')
        handler = logging.FileHandler(log_file)
        handler.setFormatter(formatter)
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.addHandler(handler)
        return logger

# If you want your plugin to be registered within OctoPrint under a different name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here. Same goes for the other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties. See the documentation for that.
__plugin_name__ = "Canvas"
__plugin_description__ = "A plugin to handle communication with Canvas"
__plugin_pythoncompat__ = ">=2.7,<4"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = CanvasPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
        "octoprint.comm.protocol.gcode.sent": __plugin_implementation__.handle_gcode_sent
    }
