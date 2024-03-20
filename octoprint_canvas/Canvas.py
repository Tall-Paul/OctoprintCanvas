import os
import zipfile
import json
from random import randrange
from . import MQTT
import requests
import time
import socket
import threading
from subprocess import call
from dotenv import load_dotenv
from io import BytesIO, open  # for Python 2 & 3
from future.utils import listvalues, lmap  # for Python 2 & 3
from . import constants
from . import PaletteComm
import jwt
try:
    from ruamel.yaml import YAML
except ImportError:
    from ruamel.yaml.main import YAML
yaml = YAML(typ="safe")
yaml.default_flow_style = False

env_path = os.path.abspath(".") + "/.env"
if os.path.abspath(".") is "/":
    env_path = "/home/pi/.env"
load_dotenv(env_path)
BASE_URL_API = os.getenv("DEV_BASE_URL_API", "api.canvas3d.io/")
def getLog(msg, module='canvas'):
    return '{ "msg": "' + msg + '", "module": "' + module +'" }'

class Canvas:
    def __init__(self, plugin):
        self.logger = plugin.logger
        self._plugin_manager = plugin._plugin_manager
        self._identifier = plugin._identifier
        self._settings = plugin._settings
        self._plugin_version = plugin._plugin_version
        self.hub_registered = False
        self.get_hub_yaml = plugin.get_hub_yaml
        self.replace_hub_yaml = plugin.replace_hub_yaml
        self.save_hub_yaml = plugin.save_hub_yaml
        self.isHubS = False
        self.registerThread = None
        self.plugin = plugin
        self.device_registered = False
        self.path = os.path.expanduser('~') + "/.mosaicdata/"
        self.cert_path = self.path + "certificate.pem.crt"
        self.private_path = self.path + "private.pem.key"
        self.public_path = self.path + "public.pem.key"
        self.palette_comm = PaletteComm.PaletteComm(plugin)
        self.mqtt_connected = False
        if ("mqtt" in self.get_hub_yaml()
                and "broker" in self.get_hub_yaml()["mqtt"]
                and "endpoint" in self.get_hub_yaml()["mqtt"]["broker"]):
            self.mqtt = MQTT.MQTT(plugin, self.downloadPrintFiles)
            self.mqtt.on_connection_status_change = self.onMqttConnectionChange
            self.mqtt.mqtt_connect()
        self._getLinkedAccount()
        self.syncHostname()

    ##############
    # PRIVATE
    ##############
    def _readFile(self, path):
        data = open(path, "r")
        return data.read()

    def _writeFile(self, path, content):
        data = open(path, "w")
        data.write(content)
        data.close()

    def _getAuthorizationHeader(self):
        hub_token = self.get_hub_yaml()["canvas-hub"]["token"]
        headers = {"Authorization": "Bearer %s" % hub_token}
        return headers

    def _startRegisterThread(self):
        self.logger.debug(getLog('starting registartion thread'))
        if self.registerThread is None:
            self.registerThread = threading.Thread(target=self._registerDevice)
            self.registerThread.daemon = True
            self.registerThread.start()

    def _registerDevice(self):
        self.logger.info(getLog("registering device"))
        while not self.device_registered:
            if not "serial-number" in self.get_hub_yaml()["canvas-hub"]:
                self.logger.debug(getLog("No serial-number found"))
                name = str(randrange(10000000000000)) + \
                    yaml.load(self._settings.config_yaml)["server"]["secretKey"]
                payload = {
                    "name": name,
                    "type": "canvas-hub",
                    "model": "diy"
                }
            else:
                self.logger.debug(getLog("Found hub serial-number"))
                name = self.get_hub_yaml()["canvas-hub"]["serial-number"]
                serialNumber = self.get_hub_yaml()["canvas-hub"]["serial-number"]
                payload = {
                    "hostname": serialNumber + "-canvas-hub.local/",
                    "name": name,
                    "serialNumber": serialNumber,
                    "type": "canvas-hub",
                    "model": "mosaic"
                }
            self.logger.debug(getLog("update hostname"))
            hostname = self._getHostname()
            if hostname:
                payload["hostname"] = hostname
            if self.isHubS:
                payload["model"] = "mosaic-s"
            self.logger.debug(getLog("Generating url"))
            url = "https://" + BASE_URL_API + "devices"
            try:
                response = requests.put(url, json=payload)
                response_body = response.json()
                if response.status_code >= 400:
                    self.logger.error(getLog("error getting response"))
                    time.sleep(30)
                else:
                    self.logger.debug(getLog("response successful"))
                    self._saveDeviceRegistrationResponse(response_body)
                    self.device_registered = True
            except requests.exceptions.RequestException as e:
                self.logger.info(getLog(e))
                time.sleep(30)
        return

    def _saveDeviceRegistrationResponse(self, response):
        self.logger.info(getLog("Saving registration response"))
        if "refreshToken" in response:
            self.logger.debug(getLog("found refresh token ... saving"))
            self.get_hub_yaml()["canvas-hub"].update(refreshToken=response["refreshToken"],
                                               accessToken=response["accessToken"],
                                               clientId=response["clientId"],
                                               device=response["device"])

            # create certs
            self._writeFile(self.cert_path, response["certificate"]["pem"])
            self._writeFile(self.private_path, response["certificate"]["privateKey"])
            self._writeFile(self.public_path, response["certificate"]["publicKey"])

            # construct topics
            device_id = self.get_hub_yaml()["canvas-hub"]["device"]["id"]
            topic_prefix = self.get_hub_yaml()["mqtt"]["publish"]["topicPrefix"]
            origin_name = self.get_hub_yaml()["mqtt"]["publish"]["originName"]
            all_devices = topic_prefix + '/devices'
            all_canvas_hubs = topic_prefix + '/devices/canvas-hub'
            device_topic_prefix = topic_prefix + '/devices/' + device_id
            device_request_topic_prefix = device_topic_prefix + '/' + origin_name + '/request/#'
            broadcast_health_topic = device_topic_prefix + '/' + origin_name + '/broadcast/health'
            broadcast_state_topic = device_topic_prefix + '/' + origin_name + '/broadcast/state'
            self.get_hub_yaml()["mqtt"]["topics"]['requests'].update(allDevices=all_devices,
                                                   allCanvasHubs=all_canvas_hubs,
                                                   deviceTopicPrefix=device_topic_prefix,
                                                   deviceRequestTopicPrefix=device_request_topic_prefix)
            self.get_hub_yaml()["mqtt"]["topics"]['broadcasts'].update(healthTopic=broadcast_health_topic,
                                                                 stateTopic=broadcast_state_topic)
            self.device_registered = True
            self.get_hub_yaml()["canvas-hub"].update(version=3)
            self.save_hub_yaml()
            if self.mqtt_connected == False:
                self.mqtt = MQTT.MQTT(self.plugin, self.downloadPrintFiles)
                self.mqtt.on_connection_status_change = self.onMqttConnectionChange
                self.logger.debug(getLog('calling mqtt_connect'))
                self.mqtt.mqtt_connect()
            self._getLinkedAccount()

        else:
            self.logger.debug(getLog("no refresh token from response"))

    def _getDevice(self):
        device = self.get_hub_yaml()["canvas-hub"]["device"]
        if "id" in device:
            url = "https://" + BASE_URL_API + "devices/" + device["id"]
            headers = {
                "content-type": "application/json",
                "Authorization": "Bearer " + self.get_hub_yaml()["canvas-hub"]["accessToken"]
            }
            try:
                self.logger.debug(getLog("getting device by id"))
                response = requests.get(url, headers=headers)
                if response.status_code >= 400:
                    self.logger.error(getLog("error getting device by id"))
                    time.sleep(30)
                else:
                    self.logger.debug(getLog("device response successful"))
                    self._connectMQTTClient()
            except requests.exceptions.RequestException as e:
                self.logger.error(getLog('request exception: ' + str(e)))
                time.sleep(30)

    def _getLinkedAccount(self):
        if "canvas-hub" in self.get_hub_yaml() and "device" in self.get_hub_yaml()["canvas-hub"]:
            device = self.get_hub_yaml()["canvas-hub"]["device"]
            if "id" in device:
                if not self.accessTokenValid():
                    self.get_hub_yaml()["canvas-hub"]["accessToken"] = self.getNewAccessToken()
                    self.save_hub_yaml()
                url = "https://" + BASE_URL_API + "devices/" + device["id"] + "/link"
                headers = {
                    "content-type": "application/json",
                    "Authorization": "Bearer " + self.get_hub_yaml()["canvas-hub"]["accessToken"]
                }
                try:
                    self.logger.debug(getLog("getting linked account data"))
                    response = requests.get(url, headers=headers)
                    if response.status_code == 200:
                        self.logger.debug(getLog("got linked account data"))
                        # linked user ID and username available in response
                        response_body = response.json()
                        self.get_hub_yaml()["canvas-user"]["id"] = response_body["user"]["id"]
                        self.get_hub_yaml()["canvas-user"]["username"] = response_body["user"][
                            "username"]
                        self.save_hub_yaml()
                        self.updateUsersOnUI()
                    elif response.status_code == 204:
                        self.logger.debug(getLog("no linked account"))
                        # no linked user exists
                        self.get_hub_yaml()["canvas-user"] = {}
                        self.save_hub_yaml()
                        self.updateUsersOnUI()
                    else:
                        self.logger.error(getLog("error getting linked account data"))
                except requests.exceptions.RequestException as e:
                    self.logger.error(getLog('request exception: ' + str(e)))

    def syncHostname(self):
        self.logger.info(getLog('check for hostname'))
        try:
            if "canvas-hub" in self.get_hub_yaml() and "device" in self.get_hub_yaml()["canvas-hub"]:
                device = self.get_hub_yaml()["canvas-hub"]["device"]
                if "hostname" in device:
                    if not self.accessTokenValid():
                        self.get_hub_yaml()["canvas-hub"]["accessToken"] = self.getNewAccessToken()
                        self.save_hub_yaml()
                    hostname = self._getHostname();
                    self.logger.info(getLog('hostname: ' + str(hostname)))
                    if hostname != self.get_hub_yaml()["canvas-hub"]['device']['hostname']:
                        self.logger.info(getLog('hostname mismatch'))
                        self.get_hub_yaml()["canvas-hub"]['device']['hostname'] = hostname;
                        self.save_hub_yaml()
                        url = "https://" + BASE_URL_API + "devices/" + device["id"]
                        headers = {
                            "content-type": "application/json",
                            "Authorization": "Bearer " + self.get_hub_yaml()["canvas-hub"]["accessToken"]
                        }
                        try:
                            self.logger.debug(getLog("updating hostname"))
                            payload = {"hostname": hostname}
                            response = requests.post(url, headers=headers, json=payload)
                            if response.status_code >= 200 and response.status_code < 300:
                                self.logger.debug(getLog("successfully updated hostname"))
                            else:
                                self.logger.error(getLog("error updating hostname"))
                        except requests.exceptions.RequestException as e:
                            self.logger.error(getLog('request exception: ' + str(e)))
        except Exception as e:
            self.logger.error(getLog('Failed to sync hostname: ' + str(e)))

    def updateUsersOnUI(self):
        self.logger.info(getLog('updating usernames in UI'))
        users = []
        if ("canvas-user" in self.get_hub_yaml()
                and "username" in self.get_hub_yaml()["canvas-user"]):
            users = [{
                "username": self.get_hub_yaml()["canvas-user"]["username"]
            }]
        self.updateUI({
            "command": "UpdateLinkedUsers",
            "data": {
                "users": users
            },
        })

    def onMqttConnectionChange(self, connected):
        self.logger.info(getLog('IoT status change'))
        self.mqtt_connected = connected
        self.updateIotConnectionOnUI()

    def updateIotConnectionOnUI(self):
        self.logger.info(getLog('updating IoT connection status in UI'))
        userLinked = False
        if 'canvas-user' in self.get_hub_yaml() and 'id' in self.get_hub_yaml()["canvas-user"]:
            userLinked = True
        self.updateUI({
            "command": "UpdateIotConnection",
            "data": {
                "iotConnected": self.mqtt_connected,
                "userLinked": userLinked
            },
        })

    def _streamFileProgress(self, response, filename):
        self.logger.debug(getLog("Starting stream buffer"))
        self.updateUI({
            "command": "CanvasDownload",
            "data": {
                "filename": filename
            },
            "status": "starting"
        })
        buffer = BytesIO()
        total_bytes = int(response.headers.get("content-length"))
        chunk_size = total_bytes // 100  # for Python 2 & 3

        for data in response.iter_content(chunk_size=chunk_size):
            buffer.write(data)
            downloaded_bytes = float(len(buffer.getvalue()))
            percentage_completion = int((downloaded_bytes / total_bytes) * 100)
            self.logger.info(getLog("%s%% downloaded" % percentage_completion))
            self.updateUI({
                "command": "CanvasDownload",
                "data": {
                    "filename": filename,
                    "progress": percentage_completion
                },
                "status": "downloading"
            }, False)
        return buffer

    def _extractZipfile(self, buffer_file, name):
        zip_file = zipfile.ZipFile(buffer_file)
        watched_path = self._settings.global_get_basefolder("watched")
        self.updateUI({
            "command": "CanvasDownload",
            "data": {
                "filename": name
            },
            "status": "received"
        })
        self.logger.info(getLog("Extracting zip file"))
        zip_file.extractall(watched_path)
        zip_file.close()

    def _getHostname(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('10.255.255.255', 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            self.logger.error(getLog("Unable to get hostname"))
            return None

    def _updateHostname(self, new_hostname):
        self.logger.debug(getLog("Updating Hostname: " + new_hostname))
        hub_id = self.get_hub_yaml()["canvas-hub"]["id"]
        url = "https://" + BASE_URL_API + "hubs/" + hub_id
        headers = self._getAuthorizationHeader()
        payload = {"hostname": new_hostname}
        try:
            response = requests.post(url, json=payload, headers=headers)
            response_body = response.json()
            if response.status_code >= 400:
                self.logger.error(getLog('error getting response'))
            else:
                if new_hostname:
                    self.logger.debug(getLog("Hostname updated: %s" % new_hostname))
                    self.get_hub_yaml()["canvas-hub"]["hostname"] = new_hostname
                    self.save_hub_yaml()
                else:
                    self.logger.debug(getLog("Deleting hostname"))
                    del self.get_hub_yaml()["canvas-hub"]["hostname"]
                    self.save_hub_yaml()
        except requests.exceptions.RequestException as e:
            self.logger.error(getLog(e))

    ##############
    # PUBLIC
    ##############

    # 1. SERVER STARTUP FUNCTIONS
    def checkForRuamelVersion(self):
        for path in constants.PROBLEMATIC_YAML_FILES_PATHS:
            if os.path.exists(path):
                self.logger.debug(getLog("Deleting file/directory"))
                call(["rm -rf %s" % path], shell=True)

    def checkFor0cf0(self):
        if (
            os.path.isdir("/home/pi/.mosaicdata/turquoise/") and
            "hub" in self.get_hub_yaml()["canvas-hub"] and
            self.get_hub_yaml()["canvas-hub"]["hub"]["name"] == constants.PROBLEMATIC_HUB_VALUES["name"] and
            self.get_hub_yaml()["canvas-hub"]["hub"]["id"] == constants.PROBLEMATIC_HUB_VALUES["id"] and
            self.get_hub_yaml()["canvas-hub"]["token"] == constants.PROBLEMATIC_HUB_VALUES["token"]
        ):
            self.logger.debug(getLog("0cf0 found."))
            del self.get_hub_yaml()["canvas-hub"]["hub"]
            del self.get_hub_yaml()["canvas-hub"]["token"]
            self.save_hub_yaml()

    def checkIfRootCertExists(self):
        root_ca_path = os.path.expanduser('~') + "/.mosaicdata/root-ca.crt"
        if not os.path.isfile(root_ca_path):
            self.logger.debug(getLog("downloading root-ca cert"))
            try:
                response = requests.get(constants.ROOT_CA_CERTIFICATE)
                self._writeFile(root_ca_path, response.content.decode())
                self.logger.debug(getLog("successfully downloaded root-ca"))
            except requests.exceptions.RequestException as e:
                self.logger.error(getLog('request exception: ' + str(e)))
        else:
            self.logger.debug(getLog("root-ca already exists"))

    def checkForRegistrationAndVersion(self):
        if self.get_hub_yaml()["canvas-hub"]:
            if "version" in self.get_hub_yaml()["canvas-hub"] and self.get_hub_yaml()["canvas-hub"]["version"] == 3:
                self.logger.info(getLog("HUB version: 3"))
                self.device_registered = True
            if not "version" in self.get_hub_yaml()["canvas-hub"] or \
                    ("version" in self.get_hub_yaml()["canvas-hub"] and
                     self.get_hub_yaml()["canvas-hub"]["version"] == 2):
                self.logger.info(getLog("HUB version: 2"))
                self.replace_hub_yaml(constants.DEFAULT_YAML)
        else:
            self.replace_hub_yaml(constants.DEFAULT_YAML)

        if not self.device_registered:
            self._startRegisterThread()

    def updatePluginVersions(self):
        updated = False
        if "versions" in self.get_hub_yaml():
            # canvas
            if self.get_hub_yaml()["versions"]["canvas-plugin"] != self._plugin_version:
                self.get_hub_yaml()["versions"]["canvas-plugin"] = self._plugin_version
                updated = True
            # palette 2
            if self._plugin_manager.get_plugin_info("palette2") and self.get_hub_yaml()["versions"]["palette-plugin"] != self._plugin_manager.get_plugin_info("palette2").version:
                self.get_hub_yaml()["versions"]["palette-plugin"] = self._plugin_manager.get_plugin_info("palette2").version
                updated = True
            if updated:
                self.save_hub_yaml()

    def determineHubVersion(self):
        hub_yaml = self.get_hub_yaml()
        hub_rank = hub_yaml["versions"]["global"]
        if hub_rank == "0.2.0":
            return True
        return False

    # 3. USER FUNCTIONS
    def addUser(self):
        if self.device_registered:
            linked = self.checkLinkedAccount()
            if not linked:
                self.logger.info(getLog('device is registered and not linked'))
                self.getActivationCode()
        else:
            self.updateUI({"command": "DeviceRegistrationError"})
            raise Exception(constants.HUB_NOT_REGISTERED)

    def unlinkUser(self):
        if self.device_registered:
            linked = self.checkLinkedAccount()
            if linked:
                if not self.accessTokenValid():
                    self.get_hub_yaml()["canvas-hub"]["accessToken"] = self.getNewAccessToken()
                    self.save_hub_yaml()
                url = "https://" + BASE_URL_API + "devices/" + self.get_hub_yaml()["canvas-hub"]["device"]["id"] + \
                      "/link"
                access_token = self.get_hub_yaml()["canvas-hub"]["accessToken"]
                headers = {"Authorization": "Bearer %s" % access_token}
                response = requests.delete(url, headers=headers)
                response_body = response.json()
                if response.status_code >= 400:
                    self.logger.error(getLog('Error unlinking account: ', response_body))
                    self.updateUI({"command": "AccountUnlinkError"})
                else:
                    username = self.get_hub_yaml()["canvas-user"]["username"]
                    self.get_hub_yaml()["canvas-user"] = {}
                    self.save_hub_yaml()
                    self.updateUsersOnUI()
                    self.updateUI({
                        "command": "AccountUnlinked",
                        "data": {
                            "username": username
                        }
                    })
            else:
                self.logger.info(getLog('device is not linked to any account'))
        else:
            self.updateUI({"command": "AccountUnlinkError"})
            raise Exception(constants.HUB_NOT_REGISTERED)

    def downloadPrintFiles(self, s3url, name):
        try:
            self.logger.debug(getLog("Starting download"))
            response = requests.get(s3url, stream=True)
            downloaded_file = self._streamFileProgress(response, name)
            self._extractZipfile(downloaded_file, name)
        except requests.exceptions.RequestException as e:
            self.logger.error(getLog(e))

    def changeImportantUpdateSettings(self, condition):
        self.logger.debug(getLog("Changing Important Update Settings"))
        self._settings.set(["importantUpdate"], condition, force=True)
        self.logger.debug(getLog(self._settings.get(["importantUpdate"])))

    def updateUI(self, data, displayLog=True):
        if displayLog:
            self.logger.debug(getLog("Sending ui update"))
        self._plugin_manager.send_plugin_message(self._identifier, data)

    def checkLinkedAccount(self):
        return "username" in self.get_hub_yaml()["canvas-user"]

    def getActivationCode(self):
        self.logger.debug(getLog('getting activation code'))
        if not self.accessTokenValid():
            self.get_hub_yaml()["canvas-hub"]["accessToken"] = self.getNewAccessToken()
            self.save_hub_yaml()
        url = "https://" + BASE_URL_API + "devices/" + self.get_hub_yaml()["canvas-hub"]["device"]["id"] + \
              "/activation-code"
        access_token = self.get_hub_yaml()["canvas-hub"]["accessToken"]
        headers = {"Authorization": "Bearer %s" % access_token}
        try:
            response = requests.get(url, headers=headers)
            response_body = response.json()
            if response.status_code >= 400:
                self.logger.error(getLog('Error getting response: ', response_body))
            else:
                self.updateUI({"command": "newActivationCode", "data": response_body["activationCode"]})

        except requests.exceptions.RequestException as e:
            raise Exception(e)
        except jwt.ExpiredSignature:
            self.logger.debug(getLog("invalid token"))

    def getNewAccessToken(self):
        self.logger.debug(getLog('getting new access token'))
        url = "https://" + BASE_URL_API + "devices/" + self.get_hub_yaml()["canvas-hub"]["device"]["id"] + "/token"
        payload = {"token": self.get_hub_yaml()["canvas-hub"]["refreshToken"]}
        try:
            response = requests.post(url, json=payload)
            response_body = response.json()
            if not response.status_code >= 400:
                return response_body['accessToken']
        except requests.exceptions.RequestException as e:
            raise Exception(e)

    def accessTokenValid(self):
        self.logger.info(getLog('access token valid'))
        public_string = "b" + self._readFile(self.public_path)
        return False

    def resetCanvasData(self):
        self.logger.info(getLog('resetting canvas data'))
        try:
            if "canvas-hub" in self.get_hub_yaml() and "device" in self.get_hub_yaml()["canvas-hub"]:
                device = self.get_hub_yaml()["canvas-hub"]["device"]
                if "id" in device:
                    if not self.accessTokenValid():
                        self.get_hub_yaml()["canvas-hub"]["accessToken"] = self.getNewAccessToken()
                        self.save_hub_yaml()
                    url = "https://" + BASE_URL_API + "devices/" + device["id"]
                    headers = {
                        "content-type": "application/json",
                        "Authorization": "Bearer " + self.get_hub_yaml()["canvas-hub"]["accessToken"]
                    }
                    try:
                        self.logger.debug(getLog("deleting device"))
                        payload = {"id": device['id']}
                        response = requests.delete(url, headers=headers, json=payload)
                        if response.status_code >= 200 and response.status_code < 300:
                            self.logger.debug(getLog("successfully deleted device"))
                        else:
                            self.logger.error(getLog("error deleting device"))
                    except requests.exceptions.RequestException as e:
                        self.logger.error(getLog('request exception: ' + str(e)))
            path = os.path.join(os.path.expanduser('~'), '.mosaicdata', 'canvas-hub-data.yml')
            if os.path.isfile(path):
                self.logger.info(getLog('found canvas hub data file'))
                os.remove(path)
                self.logger.info(getLog('canvas hub data file deleted'))
                self.canvas.updateUI({"command": "resetCanvasData", "data": 'true'})
            else:
                self.logger.info(getLog('canvas hub data file not found'))
                self.canvas.updateUI({"command": "resetCanvasData", "data": 'false'})
        except Exception as e:
            self.logger.error(getLog('Failed to delete device: ' + str(e)))


