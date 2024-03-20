import sys
import traceback
from collections import deque
import time
import json
import six
import os
import ssl
from . import constants
import paho.mqtt.client as mqtt
from paho.mqtt.client import topic_matches_sub
from . import MQTTRouter

try:
    from ruamel.yaml import YAML
except ImportError:
    from ruamel.yaml.main import YAML
yaml = YAML(typ="safe")
yaml.default_flow_style = False
def getLog(msg, module='mqtt'):
    return '{ "msg": "' + str(msg) + '", "module": "' + module +'" }'

class MQTT:
    def __init__(self, plugin, downloadPrintFiles):
        self._mqtt = None
        self.logger = plugin.logger
        self._mqtt_connected = False
        self.logger.debug(getLog("self._mqtt_connected = " + str(self._mqtt_connected)))
        self.get_hub_yaml = plugin.get_hub_yaml
        self.replace_hub_yaml = plugin.replace_hub_yaml
        self.save_hub_yaml = plugin.save_hub_yaml
        self._mqtt_subscriptions = []
        self._mqtt_publish_queue = deque()
        self._mqtt_subscribe_queue = deque()
        self.lastTemp = {}
        self.broker_endpoint = ''
        self.broker_port = ''
        self.retain = ''
        self.clean_session = ''
        self.health_topic = ''
        self.client_id = ''
        self.on_connection_status_change = None
        try:
            if "mqtt" in self.get_hub_yaml():
                self.broker_endpoint = self.get_hub_yaml()["mqtt"]["broker"]["endpoint"]
                self.broker_port = self.get_hub_yaml()["mqtt"]["broker"]["port"]
                self.retain = self.get_hub_yaml()["mqtt"]["broker"]["retain"]
                self.clean_session = self.get_hub_yaml()["mqtt"]["broker"]["cleanSession"]
                self.health_topic = self.get_hub_yaml()["mqtt"]["topics"]["broadcasts"]["healthTopic"]

            if "canvas-hub" in self.get_hub_yaml():
                self.client_id = self.get_hub_yaml()["canvas-hub"]["clientId"]
        except KeyError as keyError:
            self.logger.error(str(keyError))
        self.mqttRouter = MQTTRouter.Router(self, plugin, downloadPrintFiles)

    def mqtt_connect(self):
        if self.broker_endpoint is None:
            self.logger.warn(getLog("Broker URL is None, can't connect to broker"))
            return

        if self._mqtt is None:
            try:
                if "mqtt" in self.get_hub_yaml():
                    self.logger.debug(getLog(" found mqtt in yaml"))
                    self.broker_endpoint = self.get_hub_yaml()["mqtt"]["broker"]["endpoint"]
                    self.broker_port = self.get_hub_yaml()["mqtt"]["broker"]["port"]
                    self.retain = self.get_hub_yaml()["mqtt"]["broker"]["retain"]
                    self.clean_session = self.get_hub_yaml()["mqtt"]["broker"]["cleanSession"]
                    self.health_topic = self.get_hub_yaml()["mqtt"]["topics"]["broadcasts"]["healthTopic"]

                if "canvas-hub" in self.get_hub_yaml() and "clientId" in self.get_hub_yaml()["canvas-hub"]:
                    self.logger.debug(getLog("found client id"))
                    self.client_id = self.get_hub_yaml()["canvas-hub"]["clientId"]
                self._mqtt = mqtt.Client(client_id=self.client_id, clean_session=self.clean_session)
                self._mqtt.tls_set_context(context=self._ssl_alpn())
                self._mqtt.will_set(self.health_topic, self._get_alive_message(False), qos=0, retain=self.retain)
                self._mqtt.on_connect = self._on_mqtt_connect
                self._mqtt.on_disconnect = self._on_mqtt_disconnect
                self._mqtt.on_message = self._on_mqtt_message
                self.logger.debug(getLog("connect with mqtt"))
                self.logger.info(getLog("connecting to canvas"))
                self._mqtt.connect(self.broker_endpoint, port=self.broker_port)
                self.logger.debug(getLog("mqtt connection successful"))
                self.logger.info(getLog("successfully connected to canvas"))
                if self._mqtt.loop_start() == mqtt.MQTT_ERR_INVAL:
                    self.logger.error(getLog("Could not start mqtt connection, loop_start returned "
                                 "MQTT_ERR_INVAL"))
            except Exception as e:
                self.logger.error(getLog("exception main()"))
                self.logger.error(getLog("e obj:{}".format(vars(e))))
                self.logger.error(getLog("message:{}".format(str(e))))
                traceback.print_exc(file=sys.stdout)

    def mqtt_disconnect(self, force=False):
        if self._mqtt is None:
            return

        self._mqtt.loop_stop()

        if force:
            time.sleep(1)
            self._mqtt.loop_stop(force=True)

    def mqtt_publish_with_timestamp(self, topic, payload, qos=0, allow_queueing=False, timestamp=None):
        if not payload:
            payload = dict()
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")

        if timestamp is None:
            timestamp = time.time()

        timestamp_fieldname = self._settings.get(["timestamp_fieldname"])
        payload[timestamp_fieldname] = int(timestamp)

        return self.mqtt_publish(topic, payload, qos=qos, allow_queueing=allow_queueing)

    def mqtt_publish(self, topic, payload, qos=0, allow_queueing=False):
        if not isinstance(payload, six.string_types):
            if (sys.version_info[:3]) > (3, 0):
                payload = json.dumps(self._convert_py3_dict_to_json(payload))
            else:
                payload = json.dumps(payload)

        if not self._mqtt_connected:
            if allow_queueing:
                self.logger.debug(getLog("Not connected, enqueuing message: {topic} - {payload}".format(
                    **locals())))
                self._mqtt_publish_queue.append((topic, payload, qos))
                return True
            else:
                return False
        retain = self.retain
        self._mqtt.publish(topic, payload=payload, retain=retain, qos=qos)
        # self.logger.debug("Sent message: {topic} - {payload}, retain={retain}".format(**locals()))
        return True

    def mqtt_subscribe(self, topic, callback, args=None, kwargs=None):
        if args is None:
            args = []
        if kwargs is None:
            kwargs = dict()

        self._mqtt_subscriptions.append((topic, callback, args, kwargs))

        if not self._mqtt_connected:
            self._mqtt_subscribe_queue.append(topic)
        else:
            self._mqtt.subscribe(topic)

    def mqtt_unsubscribe(self, callback, topic=None):
        subbed_topics = [subbed_topic for subbed_topic, subbed_callback, _, _ in self._mqtt_subscriptions if callback == subbed_callback and (topic is None or topic == subbed_topic)]

        def remove_sub(entry):
            subbed_topic, subbed_callback, _, _ = entry
            return not (callback == subbed_callback and (topic is None or subbed_topic == topic))

        self._mqtt_subscriptions = filter(remove_sub, self._mqtt_subscriptions)

        if self._mqtt_connected and subbed_topics:
            self._mqtt.unsubscribe(*subbed_topics)

        ##~~ mqtt client callbacks

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if not client == self._mqtt:
            return

        if not rc == 0:
            reasons = [
                None,
                "Connection to mqtt broker refused, wrong protocol version",
                "Connection to mqtt broker refused, incorrect client identifier",
                "Connection to mqtt broker refused, server unavailable",
                "Connection to mqtt broker refused, bad username or password",
                "Connection to mqtt broker refused, not authorised"
            ]

            if rc < len(reasons):
                reason = reasons[rc]
            else:
                reason = None

            self.logger.error(getLog(reason if reason else "Connection to mqtt broker refused, "
                                                  "unknown error"))
            return

        self.logger.debug(getLog("Connected to mqtt broker"))
        lw_topic = self.health_topic
        if lw_topic:
            self.logger.debug(getLog("publishing alive to last will"))
            self._mqtt.publish(lw_topic, self._get_alive_message(True), qos=0, retain=self.retain)

        if self._mqtt_publish_queue:
            try:
                while True:
                    topic, payload, qos = self._mqtt_publish_queue.popleft()
                    self._mqtt.publish(topic, payload=payload, retain=self.retain, qos=qos)
            except IndexError:
                # that's ok, queue is just empty
                pass

        # subscribe to topics
        subbed_topics = self.get_hub_yaml()["mqtt"]["topics"]["requests"]
        subbed_topics = list(subbed_topics.values())
        subbed_topics = list(map(lambda t: (t, 0), {topic for topic in subbed_topics}))
        if subbed_topics:
            self._mqtt.subscribe(subbed_topics)
        self._mqtt_connected = True
        self.logger.debug(getLog("self._mqtt_connected = " + str(self._mqtt_connected)))
        if self.on_connection_status_change is not None:
            self.on_connection_status_change(self._mqtt_connected)

    def _on_mqtt_disconnect(self, client, userdata, rc):
        if not client == self._mqtt:
            return

        if not rc == 0:
            self.logger.error(getLog("Disconnected from mqtt broker for unknown reasons (network error?), "
                         "rc = {}".format(rc)))
        else:
            self.logger.info(getLog("Disconnected from mqtt broker"))

        self._mqtt_connected = False
        self.logger.debug(getLog("self._mqtt_connected = " + str(self._mqtt_connected)))
        if self.on_connection_status_change is not None:
            self.on_connection_status_change(self._mqtt_connected)

    def _on_mqtt_message(self, client, userdata, msg):
        if (sys.version_info[:3]) > (3, 0):
            self.mqttRouter.router(msg.topic, json.loads(msg.payload.decode("utf-8")))
        else:
            self.mqttRouter.router(msg.topic, json.loads(str(msg.payload)))
        if not client == self._mqtt:
            return

        for subscription in self._mqtt_subscriptions:
            topic, callback, args, kwargs = subscription
            if topic_matches_sub(topic, msg.topic):
                args = [msg.topic, msg.payload] + args
                self.logger.info(getLog('on message' + str(args)))
                kwargs.update(dict(retained=msg.retain, qos=msg.qos))
                try:
                    callback(*args, **kwargs)
                except:
                    self.logger.exception(getLog("Error while calling mqtt callback"))

    def _get_topic(self, topic_type):
        sub_topic = self.get_hub_yaml()["mqtt"]["publish"][topic_type + "Topic"]
        topic_active = self.get_hub_yaml()["mqtt"]["publish"][topic_type + "Active"]

        if not sub_topic or not topic_active:
            return None

        return self.get_hub_yaml()["mqtt"]["publish"]["topicPrefix"] + \
               self.get_hub_yaml()["mqtt"]["publish"]["baseTopic"] + sub_topic

    def _is_event_active(self, event):
        for event_class, events in self.EVENT_CLASS_TO_EVENT_LIST.items():
            if event in events:
                return self._settings.get_boolean(["publish", "events", event_class])
        return self._settings.get_boolean(["publish", "events", "unclassified"])

    def _ssl_alpn(self):
        try:
            #debug print opnessl version
            self.logger.debug(getLog("open ssl version: {}".format(ssl.OPENSSL_VERSION)))
            root_ca_path = os.path.expanduser('~') + "/.mosaicdata/root-ca.crt"
            cert_path = os.path.expanduser('~') + "/.mosaicdata/certificate.pem.crt"
            private_path = os.path.expanduser('~') + "/.mosaicdata/private.pem.key"
            ssl_context = ssl.create_default_context()
            ssl_context.set_alpn_protocols([self.get_hub_yaml()["mqtt"]["broker"]["protocol"]])
            ssl_context.load_verify_locations(cafile=root_ca_path)
            ssl_context.load_cert_chain(certfile=cert_path, keyfile=private_path)

            return ssl_context
        except Exception as e:
            self.logger.error(getLog("exception ssl_alpn()"))
            raise e

    def _get_alive_message(self, alive):
        return json.dumps({
            "header": {
                "originID":  self.get_hub_yaml()["mqtt"]["publish"]["originName"]
            },
            "payload": {
                "body": {
                    'alive': alive
                }
            }
        })

    def _convert_py3_dict_to_json(self, data):
        if isinstance(data, bytes):  return data.decode('ascii')
        if isinstance(data, dict):   return dict(map(self._convert_py3_dict_to_json, data.items()))
        if isinstance(data, tuple):  return map(self._convert_py3_dict_to_json, data)
        return data
