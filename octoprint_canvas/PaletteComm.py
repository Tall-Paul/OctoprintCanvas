import octoprint.server

def getLog(msg, module='canvas-to-palette-2-comm', topic=''):
    if topic == '':
        return '{ "msg": "' + msg + '", "module": "' + module +'" }'
    else:
        return '{ "msg": "' + msg + '", "module": "' + module +'", "topic": "' + topic +'" }'


class PaletteComm:
    def __init__(self, plugin):
        self.plugin = plugin
        self.logger = plugin.logger
        self._plugin_manager = plugin._plugin_manager
        self._register_message_receiver()

    def _register_message_receiver(self):
        self.logger.info(getLog('setting canvas plugin message reciever'))
        version = octoprint.server.VERSION
        version = version.split('.')
        if int(version[1]) > 3:
            self._plugin_manager.register_message_receiver(self._on_message_v1_4)
        else:
            self._plugin_manager.register_message_receiver(self._on_message)

    def _on_message_v1_4(self, plugin, data, permissions=None):
        if plugin == 'palette2':
            if 'connection' in data:
                if data['connection'] == 'palette 2 connected':
                    port = data['port']
                    port = port.split('\\')
                    port = port[-1]
                    self.plugin.canvas.mqtt.mqttRouter.palette_connected = True
                    self.plugin.canvas.mqtt.mqttRouter.palette_port = port[-1]
                elif data['connection'] == 'palette 2 disconnected':
                    self.plugin.canvas.mqtt.mqttRouter.palette_connected = False
                    self.plugin.canvas.mqtt.mqttRouter.palette_port = ''
        pass

    def _on_message(self, plugin, data):
        if plugin == 'palette2':
            if 'connection' in data:
                self.logger.info(getLog('connect to canvas', topic='connect'))
                if data['connection'] == 'palette 2 connected':
                    port = data['port']
                    port = port.split('\\')
                    port = port[-1]
                    self.plugin.canvas.mqtt.mqttRouter.palette_connected = True
                    self.plugin.canvas.mqtt.mqttRouter.palette_port = port[-1]
                elif data['connection'] == 'palette 2 disconnected':
                    self.logger.info(getLog('disconnect from canvas', topic='disonnect'))
                    self.plugin.canvas.mqtt.mqttRouter.palette_connected = False
                    self.plugin.canvas.mqtt.mqttRouter.palette_port = ''
        pass

    def send_message(self, data):
        self.logger.info(getLog(str(data),  topic='send-to-palette-2'))
        self._plugin_manager.send_plugin_message(plugin="canvas-plugin", data=data)
        pass
