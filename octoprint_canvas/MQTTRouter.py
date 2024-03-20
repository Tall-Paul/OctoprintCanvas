import os
import string
import datetime
import threading
import time
import requests
import zipfile
import io
import json
from dictdiffer import diff
from math import log
import platform
import traceback
from shutil import copyfile

def getLog(msg, module='mqtt-router', topic=''):
    if topic == '':
        return '{ "msg": "' + msg + '", "module": "' + module +'" }'
    else:
        return '{ "msg": "' + msg + '", "module": "' + module +'", "topic": "' + topic +'" }'
try:
    from ruamel.yaml import YAML
except ImportError:
    from ruamel.yaml.main import YAML
yaml = YAML(typ="safe")
yaml.default_flow_style = False

class Router:
    def __init__(self, mqtt, plugin, downloadPrintFiles):
        self.get_hub_yaml = plugin.get_hub_yaml
        self.replace_hub_yaml = plugin.replace_hub_yaml
        self.save_hub_yaml = plugin.save_hub_yaml
        self.downloadPrintFiles = downloadPrintFiles
        self.device_id = ''
        if "device" in  self.get_hub_yaml()["canvas-hub"] and \
                "id" in self.get_hub_yaml()["canvas-hub"]["device"]:
            self.device_id = self.get_hub_yaml()["canvas-hub"]["device"]["id"]
        self._printer = plugin._printer
        self.count = 0
        self.plugin = plugin
        self.errors = plugin.errors
        self.mqtt = mqtt
        self._file_manager = plugin._file_manager
        self._broadcastStateThread = None
        self._stateWatcherThread = None
        self.logger = plugin.logger
        self._startBroadcastStateThread()
        self._startStateWatcherThread()
        self._most_recent_broadcast_state = {}
        self._print_job_start_time = ''
        self._print_job_file_path = ''
        self._print_job_file_size = ''
        self._print_job_file_modified = ''
        self.motor_state = False
        self._print_job_file_name = ''
        self.fan_state = 0
        self._print_job_status_name = ''
        self._activeSetupId = ''
        self._filament_length = ''
        self.palette_connected = False
        self.palette_port = ''
        self.broadcast_counter = 0
        connection = self._printer.get_current_connection()
        self.connected_state = connection[1] and connection[2]

        self._temps = {
            "tool0": {
                "actual": 0,
                "target": 0,
            },
            "bed": {
                "actual": 0,
                "target": 0,
            },
            "chamber": {
                "actual": 0,
                "target": 0,
            }
        }

    def _startBroadcastStateThread(self):
        if self._broadcastStateThread is None:
            self._broadcastStateThread = threading.Thread(target=self._broadcast_state)
            self._broadcastStateThread.daemon = True
            self._broadcastStateThread.start()

    def _startStateWatcherThread(self):
        if self._stateWatcherThread is None:
            self._stateWatcherThread = threading.Thread(target=self._watch_state)
            self._stateWatcherThread.daemon = True
            self._stateWatcherThread.start()

    def _resetBroadcastStateThread(self):
        self.broadcast_counter = -1

    def _watch_state(self):
        time.sleep(5)
        while True:
            time.sleep(2)
            new_state = self._get_state()
            result = list(diff(self._most_recent_broadcast_state, new_state))
            if result:
                has_significant_change = False
                for item in result:
                    type, path, change = item
                    before_value, after_value = change
                    if type != 'change':
                        has_significant_change = True
                        break
                    path_str = '.'.join(str(x) for x in path)
                    if path_str == 'state.printer.data.temperature.nozzle.0.actual':
                        if abs(before_value - after_value) > 1:
                            has_significant_change = True
                            break
                    elif path == 'state.printer.data.temperature.bed.actual':
                        if abs(before_value - after_value) > 1:
                            has_significant_change = True
                            break
                    elif path == 'state.printer.job.progress':
                        if abs(before_value - after_value) > 0.1:
                            has_significant_change = True
                            break
                    elif path == 'state.printer.job.data.timeRemaining':
                        threshold = 30;
                        if after_value < 60:
                            threshold = 5;
                        if abs(before_value - after_value) > threshold:
                            has_significant_change = True
                            break
                    elif path == 'state.printer.job.data.totalTime':
                        if abs(before_value - after_value) > 30:
                            has_significant_change = True
                            break
                    else:
                        has_significant_change = True
                        break
                if has_significant_change:
                    self.logger.info(getLog('state changed significantly: ' + str(result)))
                    self.broadcast_counter = -1

    def _broadcast_state(self):
        time.sleep(5)
        while True:
            self.broadcast_counter += 1
            count = self.broadcast_counter
            if count == 0 or self._check_kth_power(count, 5):
                broadcast_state_topic = self.get_hub_yaml()["mqtt"]["topics"]["broadcasts"]["stateTopic"]
                self.logger.info(getLog("broadcasting state - " + str(count) + 's',
                                        topic='broadcast/state'))
                state = self._get_state()
                self._most_recent_broadcast_state = state
                response_msg = {
                    "header": {
                        "originID": "simcoe",
                        "msgID": datetime.datetime.utcnow().isoformat()[:-3] + 'Z'
                    },
                    "payload": {
                        "status": 200,
                        "body": state,
                    },
                }
                self.mqtt.mqtt_publish(broadcast_state_topic, response_msg)
            time.sleep(1)

    def _get_state(self):
        progress_percent = 0
        time_remaining = 0
        time_total = 0
        try:
            data = self._printer.get_current_data()
            job = self._printer.get_current_job()

            if ((data is not None) and ('progress' in data)
                    and (data['progress']['printTime'] is not None)
                    and (data['progress']['printTimeLeft'] is not None)):
                progress_percent = data['progress']['completion']
                time_elapsed = data['progress']['printTime']
                time_remaining = data['progress']['printTimeLeft']
                time_total = time_elapsed + time_remaining
            if (job != None) and ('filament' in job) and (job['filament'] != None) and ('tool0' in \
                    job['filament']):
                self._filament_length = job['filament']['tool0']['length']
        except Exception as e:
            self.logger.error(getLog('error reading job: ' + str(e), topic="get/state"))
        try:
            get_temps = self._printer.get_current_temperatures()
            if get_temps != {}:
                self._temps = get_temps
            if "active-setup" in self.get_hub_yaml()["canvas-user"]:
                self._activeSetupId = self.get_hub_yaml()["canvas-user"]["active-setup"]["id"]
        except KeyError as e:
            self.logger.error(getLog('broadcast key error: ' + str(e), topic="get/state"))

        connection = self._printer.get_current_connection()
        try:
            return {
                "state": {
                    "simcoe": {
                        "data": {
                            "activeSetup": {
                                "id": self._activeSetupId
                            }
                        },
                    },
                    "palette": {
                        "data": {
                            "connected": self.palette_connected,
                            "serial": {
                                "port": self.palette_port
                            }
                        }
                    },
                    "printer": {
                        "data": {
                            "connected": self.connected_state,
                            "serial": {
                                "port": connection[1],
                                "baud": connection[2],
                            },
                            "temperature": {
                                "nozzle": [
                                    {
                                        "actual": self._temps["tool0"]["actual"],
                                        "target": self._temps["tool0"]["target"],
                                    },
                                ],
                                "bed": {
                                    "actual": self._temps["bed"]["actual"],
                                    "target": self._temps["bed"]["target"],
                                },
                                "chamber": {
                                    "actual": self._temps["chamber"]["actual"],
                                    "target": self._temps["chamber"]["target"],
                                },
                            },
                            "fan": self.fan_state,
                            "motor": self.motor_state,
                        },
                        "job": {
                            "name": self._print_job_file_name,
                            "progress": progress_percent,
                            "time": {
                                "start": self._print_job_start_time
                            },
                            "status": {
                                "name": self._print_job_status_name,
                            },
                            "data": {
                                "totalTime": time_total,
                                "timeRemaining": time_remaining,
                                "filament": {
                                    "length": self._filament_length,
                                },
                                "file": {
                                    "path": self._print_job_file_path,
                                    "size": self._print_job_file_size,
                                    "date": self._print_job_file_modified,
                                },
                            },
                        },
                    }
                }
            }
        except Exception as error:
            self.logger.error(getLog('key error: ' + str(error), topic="get/state"))

    def router(self, topic, msg):
        try:
            subbed_topics = self.get_hub_yaml()["mqtt"]["topics"]["requests"]
            request_prefix = str(subbed_topics.get("deviceRequestTopicPrefix")).split('/#')[0]
            request_topic = self._removePrefix(topic, request_prefix)
            if topic == subbed_topics.get('allCanvasHubs'):
                pass
            elif topic == subbed_topics.get('allDevices'):
                pass
            elif topic.startswith(request_prefix):
                self._requestRouter(request_topic, msg)
            elif topic == subbed_topics.get('deviceTopicPrefix'):
                if msg["type"] == "ACCOUNT_LINKED":
                    self.get_hub_yaml()["canvas-user"] = msg["payload"]["user"]
                    self.save_hub_yaml()
                    self.plugin.canvas.updateUsersOnUI()
                    # show Account Linked modal after adding user to YAML
                    self.plugin.canvas.updateUI({
                        "command": "AccountLinked",
                        "data": {
                            "username": self.get_hub_yaml()["canvas-user"]["username"]
                        }
                    })
                elif msg["type"] == "ACCOUNT_UNLINKED":
                    # show Account Unlinked modal before removing user from YAML
                    self.plugin.canvas.updateUI({
                        "command": "AccountUnlinked",
                        "data": {
                            "username": self.get_hub_yaml()["canvas-user"]["username"]
                        }
                    })
                    self.get_hub_yaml()["canvas-user"] = {}
                    self.save_hub_yaml()
                    self.plugin.canvas.updateUsersOnUI()
                pass
        except ValueError as valError:
            self.errors.errorHandler(valError)
            pass

    def _requestRouter(self, topic, msg):
        try:
            req_origin_id = msg["header"]["originID"]
            device_id = self.get_hub_yaml()["canvas-hub"]["device"]["id"]
            topic_prefix = self.get_hub_yaml()["mqtt"]["publish"]["topicPrefix"]
            response_topic = topic_prefix + '/devices/' + device_id + '/' + req_origin_id + \
                                          '/response' + topic
            req_msg_id = msg["header"]["msgID"]
            response_status = 204
            response_body = {
                "response": "No Content"
            }
            query = ''
            if 'payload' in msg and 'query' in msg['payload']:
                query += ': ' + str(msg['payload']['query'])
            self.logger.info(getLog('new message: ' + topic + query))
            if topic == '/state':
                response_body = self._get_state()
                pass
            elif topic == '/printer/move':
                self.logger.debug(getLog('move payload: ', str(msg["payload"]), topic=topic))
                f, x, y, z, e = 51, 0, 0, 0, 0
                if "f" in msg["payload"]["query"]:
                    f = msg["payload"]["query"]["f"] * 100
                self._printer.feed_rate(f)
                if "x" in msg["payload"]["query"]:
                    x = msg["payload"]["query"]["x"]
                if "y" in msg["payload"]["query"]:
                    y = msg["payload"]["query"]["y"]
                if "z" in msg["payload"]["query"]:
                    z = msg["payload"]["query"]["z"]
                self._printer.jog(axes={"x": x, "y": y, "z": z}, speed=20000, relative=True)
                if "e" in msg["payload"]["query"]:
                    self.logger.info(getLog('extruding/retracting: ' + str(e), topic=topic))
                    e = msg["payload"]["query"]["e"]
                self._printer.extrude(e)
                time.sleep(1)
                pass
            elif topic == '/printer/home':
                axes = msg["payload"]["query"]["axes"]
                if not axes:
                    axes = ['x', 'y', 'z']
                self._printer.home(axes)
                time.sleep(1)
                pass
            elif topic == '/printer/fan':
                speed = msg["payload"]["query"]["speed"]
                if speed == 0:
                    self._printer.commands("M107")
                    time.sleep(1)
                elif 0 < speed <= 100:
                    self._printer.commands("M106 S" + str(255*speed*.01))
                time.sleep(1)
                pass
            elif topic == '/printer/motor':
                if msg["payload"]["query"]["on"]:
                    self._printer.commands("M17")  # enable motors
                    time.sleep(1)
                else:
                    self._printer.commands("M18")  # disable motors
                    time.sleep(1)
                pass
            elif topic == '/printer/temperature':

                if "query" in msg["payload"]:
                    if "bed" in msg["payload"]["query"]:
                        self._printer.set_temperature(heater='bed', value=msg["payload"]["query"][
                            "bed"])
                        time.sleep(1)
                    if "chamber" in msg["payload"]["query"]:
                        self._printer.set_temperature(heater='chamber', value=msg["payload"][
                            "query"]["chamber"])
                        time.sleep(1)
                    if "nozzle" in msg["payload"]["query"]:
                        self._printer.set_temperature(heater='tool0', value=msg["payload"]["query"][
                            "nozzle"][0])
                        time.sleep(1)
                pass
            elif topic == '/printer/start':
                path = msg["payload"]["query"]["path"]
                legs = path.split('/')
                basename = ''
                if legs[0] == 'device':
                    print_path = path
                    legs = legs[1:]
                    local_path = os.path.join(self.plugin._settings.getBaseFolder('uploads'))
                    for leg in legs:
                        local_path = os.path.join(local_path, leg)
                    basename = os.path.basename(local_path)
                else:
                    self.logger.debug(getLog('start print from external drive', topic=topic))
                    system = platform.system()
                    if system == 'Linux':
                        self.logger.debug(getLog('copying file to linux local', topic=topic))
                        basename = os.path.basename(path)
                        local_path = os.path.join(self.plugin._settings.getBaseFolder('uploads'), basename)
                        if legs[1] == 'dev':
                            ext_rel_legs = legs[3:]
                            external_abs_path = '/mnt/mosaic/'
                            for leg in ext_rel_legs:
                                external_abs_path = os.path.join(external_abs_path, leg)
                            copyfile(external_abs_path, local_path)
                            print_path = 'device/' + basename
                    elif system == 'Windows':
                        self.logger.debug(getLog('copying file to windows local', topic=topic))
                        basename = os.path.basename(path)
                        local_path = os.path.join(self.plugin._settings.getBaseFolder('uploads'), basename)
                        copyfile(path, local_path)
                        print_path = 'device/' + basename
                self._printer.select_file(local_path, sd=False, printAfterSelect=True)
                self._print_job_start_time = datetime.datetime.utcnow().isoformat()[:-3] + 'Z'
                self._print_job_file_path = print_path
                self._print_job_file_name = basename
                self._print_job_file_size = os.path.getsize(local_path)
                timestamp = os.path.getmtime(local_path)
                self._print_job_status_name = 'start'

                self._print_job_file_modified = datetime.datetime.utcfromtimestamp(timestamp).strftime(
                    '%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

                self._print_job_file_name = legs[len(legs) - 1]
                self.logger.info(getLog('starting print: ' + self._print_job_file_name, topic=topic))

                time.sleep(1)
                pass
            elif topic == '/printer/cancel':
                self._printer.cancel_print()
                for count in range(0, 29):
                    if self._print_job_status_name == 'cancelling':
                        time.sleep(1)
                time.sleep(1)
                pass
            elif topic == '/printer/pause':
                printer_state = self._printer.get_state_id()
                self._print_job_status_name = 'pausing'
                self._printer.pause_print()
                time.sleep(1)
                pass
            elif topic == '/printer/resume':
                printer_state = self._printer.get_state_id()
                self._printer.resume_print()
                time.sleep(1)
                self._print_job_status_name = ''
                pass
            elif topic == '/command':
                self._printer.commands(msg["payload"]["query"]["command"])
                time.sleep(1)
                pass
            elif topic == '/storage':
                path = None

                if "query" in msg["payload"] and "path" in msg["payload"]["query"]:
                    path = msg["payload"]["query"]["path"]
                method = msg["payload"]["method"]
                if method == 'put':
                    name = ""
                    if "name" in msg["payload"]["query"]:
                        name = msg["payload"]["query"]["name"]
                    if "s3path" in msg["payload"]["query"]:
                        self.downloadPrintFiles(msg["payload"]["query"]["s3path"], name)
                        response_status = 200
                        response_body = {
                            path: path
                        }
                    pass
                elif method == 'get':
                    drives = ["device/"]
                    system = platform.system()
                    if system == 'Linux':
                        temp_drives = os.popen('sudo blkid /dev/sd*').read()
                        temp_drives = temp_drives.split('\n')
                        self.logger.debug(getLog('temp_drives: ' + str(temp_drives), topic=topic))
                        temp_drives.remove('')
                        for drive in temp_drives:
                            drive = drive.split(':')
                            try:
                                if 'PARTUUID' in drive[1]:
                                    temp_drives2 = []
                                    temp_drives2.append(drive[0])
                                    for idx, drive in enumerate(temp_drives2):
                                        if drive != '':
                                            drives.append(drive)
                            except Exception as e:
                                self.logger.error(getLog('error finding drive: ' + str(e), topic=topic))
                    elif system == 'Windows':
                        available_drives = ['%s:' % d for d in string.ascii_uppercase if os.path.exists('%s:' % d)]
                        for drive in available_drives:
                            if drive != 'C:':
                                drives.append(drive)
                    response_status = 200
                    response_body = drives
                    pass
                elif method == 'post':
                    # get folder content
                    if "path" in msg["payload"]["query"] and "newPath" not in msg["payload"][\
                            "query"]:
                        files = self._get_folder_content(msg, topic)
                        response_body = files
                        response_status = 200

                    # edit file name
                    if "newPath" in msg["payload"]["query"]:
                        path = msg["payload"]["query"]["path"]
                        new_path = msg["payload"]["query"]["newPath"]
                        old_legs = path.split('/')
                        new_legs = new_path.split('/')
                        if old_legs[0] == 'device':
                            old_legs.pop(0)
                            path = '/'.join(old_legs[:])
                            new_legs.pop(0)
                            new_path = '/'.join(new_legs)
                            new_abs_path = os.path.join(self.plugin._settings.getBaseFolder('uploads'),
                                                        new_path)
                            os.rename(os.path.join(self.plugin._settings.getBaseFolder(
                                'uploads'), path), new_abs_path)
                        else:
                            system = platform.system()
                            if system == 'Linux':
                                old_abs_path = '/mnt/mosaic'
                                for idx, leg in enumerate(old_legs):
                                    if idx > 2:
                                        old_abs_path = os.path.join(old_abs_path, leg)
                                new_abs_path = '/mnt/mosaic'
                                for idx, leg in enumerate(new_legs):
                                    if idx > 2:
                                        new_abs_path = os.path.join(new_abs_path, leg)
                                os.popen('sudo mv ' + old_abs_path + ' ' + new_abs_path).read()
                                pass
                            elif system == 'Windows':
                                new_abs_path = new_path
                                os.rename(path, new_path)
                            pass
                        response_status = 200
                        timestamp = os.path.getmtime(new_abs_path)
                        timestamp = datetime.datetime.utcfromtimestamp(timestamp).strftime(
                            '%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                        response_body = {
                            "name": new_legs[len(new_legs) - 1],
                            "dateModified":  timestamp
                        }
                    else:
                        pass
                elif method == 'delete':
                    pass
                pass
            elif topic == '/printer/connect':
                port = None
                baudrate = None
                if "baudrate" in msg["payload"]["query"]:
                    if msg["payload"]["query"]["baudrate"] != "auto":
                        baudrate = int(msg["payload"]["query"]["baudrate"])
                if "comPort" in msg["payload"]["query"]:
                    if msg["payload"]["query"]["comPort"] != "auto":
                        port = msg["payload"]["query"]["comPort"]
                self._printer.connect(port=port, baudrate=baudrate)
                for count in range(0, 29):
                    if self.connected_state == False:
                        time.sleep(1)
                if self.connected_state == False:
                    response_status = 504
                    response_body = {
                        "response": "The server was acting as a gateway or proxy and did not receive a timely response from the upstream server"
                    }
                    pass
                self.broadcast_counter = -1
                pass
            elif topic == '/palette/connect':
                self.logger.info(getLog('palette connect', topic=topic))
                self.plugin.canvas.palette_comm.send_message('connect')
                for count in range(0, 29):
                    if self.palette_connected == False:
                        time.sleep(1)
                if self.palette_connected == False:
                    response_status = 504
                    response_body = {
                        "response": "The server was acting as a gateway or proxy and did not receive a timely response from the upstream server"
                    }
                self.broadcast_counter = -1
            elif topic == '/palette/disconnect':
                self.plugin.canvas.palette_comm.send_message('disconnect')
                time.sleep(1)
            elif topic == '/printer/disconnect':
                self._printer.disconnect()
                time.sleep(1)
                pass
            elif topic == '/scan-com-ports':
                connection_options = self._printer.get_connection_options()
                response_status = 200
                response_body = {
                    "ports": connection_options["ports"]
                }
                pass
            elif topic == '/update-active-setup':
                setup_id = msg["payload"]["query"]["id"]
                self.get_hub_yaml()["canvas-user"]["active-setup"] = {}
                self.get_hub_yaml()["canvas-user"]["active-setup"]["id"] = setup_id
                self.logger.info(getLog('active setup updated', topic=topic))
                self._activeSetupId = self.get_hub_yaml()["canvas-user"]["active-setup"]["id"]
                self.save_hub_yaml()
            self._publishResponse(response_topic, req_msg_id, response_body, response_status)
            self._resetBroadcastStateThread()
        except ValueError as valError:
            self.errors.errorHandler(valError)
            pass

    def _removePrefix(self, text, prefix):
        if text.startswith(prefix):
            return text[len(prefix):]
        return text

    def _publishResponse(self, response_topic, req_msg_id, response_body, response_status):
        response_msg = {
            "header": {
                "originID": "simcoe",
                "msgID": req_msg_id
            },
            "payload": {
                "status": response_status,
                "body": response_body,
            },
        }
        self.mqtt.mqtt_publish(response_topic, response_msg)

    def _check_kth_power(self, n, k):
        candidate = k ** int(log(n, k))
        return candidate == n or k * candidate == n

    def _get_folder_content(self, msg, topic):
        files = []
        try:
            path = ''
            drive = "Device"
            path = msg["payload"]["query"]["path"]
            legs = path.split('/')
            # local home for win and linux
            if legs[0] == 'device' and legs[len(legs) - 1] == '':
                legs[0] = '';
                if path[0:6] == 'device':
                    path = path[6:]
                content = self._file_manager.list_files(path=path)
                if "local" in content:
                    for name, file in enumerate(content["local"]):
                        if str(file).split('.')[-1] == 'gcode' or str(file).split('.')[-1] == 'g' or \
                                str(file).split('.')[-1] == 'gco':
                            date = ''
                            if "date" in content["local"][file]:
                                date = datetime.datetime.utcfromtimestamp(int(
                                    content["local"][file]["date"])).strftime(
                                    '%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                                files.append({
                                    "name": file,
                                    "dateModified": date
                                })
                        if content["local"][file]["type"] == 'folder':
                            directory = os.path.join(self.plugin._settings.getBaseFolder('uploads'))
                            for leg in legs:
                                directory = os.path.join(directory, leg)
                            date = datetime.datetime.utcfromtimestamp(os.stat(directory).st_mtime).strftime(
                                '%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                            file = file + '/'
                            files.append({
                                "name": file,
                                "dateModified": date
                            })
            # external on linux
            if legs[0] == '' and legs[len(legs) - 1] == '':
                legs = legs[1:-1]
                # external home on linux
                if legs and legs[0] == 'dev' and len(legs) == 2:
                    mounts = os.popen('mount').read()
                    mounts = mounts.split('\n')
                    mounted = False
                    for mount in mounts:
                        mount = mount.split(' ')
                        if mount[0] == path:
                            self.logger.debug(getLog('found mounted path', topic=topic))
                            mounted = True
                    if mounted:
                        self.logger.debug(getLog('drive is mounted', topic=topic))
                        unmount = os.popen('sudo umount /' + legs[0] + '/' + legs[1])
                        self.logger.debug(getLog(unmount, topic=topic))
                    if os.path.exists('/mnt/mosaic'):
                        self.logger.debug(getLog('mount path exists', topic=topic))
                    else:
                        self.logger.debug(getLog('creating mount path', topic=topic))
                        os.popen('sudo mkdir -p /mnt/mosaic')
                    self.logger.debug(getLog('ready to mount drive', topic=topic))
                    os.popen('sudo mount ' + path[0:-1] + ' /mnt/mosaic')
                    self.logger.debug(getLog('drive mounted', topic=topic))
                    directory = '/mnt/mosaic'
                    temp_files = os.listdir(directory)
                    for item in temp_files:
                        test_path = os.path.join(directory, item)
                        date = datetime.datetime.utcfromtimestamp(os.stat(directory).st_mtime).strftime(
                            '%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                        if os.path.isdir(test_path):
                            item = item + '/'
                            files.append({
                                "name": item,
                                "dateModified": date
                            })
                        elif item.split('.')[-1] == 'gcode' or item.split('.')[-1] == 'g' or \
                                item.split('.')[-1] == 'gco':
                            files.append({
                                "name": item,
                                "dateModified": date
                            })
                    pass
                # external sub folder on linux
                elif legs and legs[0] == 'dev' and len(legs) > 2:
                    directory = '/mnt/mosaic'
                    for idx, leg in enumerate(legs):
                        if idx > 1:
                            directory = os.path.join(directory, str(leg))
                    temp_files = os.listdir(directory)
                    for item in temp_files:
                        test_path = os.path.join(directory, item)
                        date = datetime.datetime.utcfromtimestamp(os.stat(directory).st_mtime).strftime(
                            '%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                        if os.path.isdir(test_path):
                            item = item + '/'
                            files.append({
                                "name": item,
                                "dateModified": date
                            })
                        elif item.split('.')[-1] == 'gcode' or item.split('.')[-1] == 'g' or \
                                item.split('.')[-1] == 'gco':
                            files.append({
                                "name": item,
                                "dateModified": date
                            })
                    pass
            # external on windows
            elif legs[len(legs) - 1] == '':
                system = platform.system()
                if system == 'Windows':
                    directory = ''
                    for leg in legs:
                        directory = os.path.join(directory, str(leg))
                    temp_files = os.listdir(directory)
                    for item in temp_files:
                            test_path = os.path.join(directory, item)
                            date = datetime.datetime.utcfromtimestamp(os.stat(directory).st_mtime).strftime(
                                '%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                            if os.path.isdir(test_path):
                                item = item + '/'
                                files.append({
                                    "name": item,
                                    "dateModified": date
                                })
                            elif item.split('.')[-1] == 'gcode' or item.split('.')[-1] == 'g' or \
                                    item.split('.')[-1] == 'gco':
                                files.append({
                                    "name": item,
                                    "dateModified": date
                                })
        except Exception as e:
            self.logger.error(getLog(str(traceback.print_exc())))
            self.logger.error(getLog(str(e)))
        return files