import traceback
def getLog(msg, module='canvas-error'):
    return '{ "msg": "' + str(msg) + '", "module": "' + module +'" }'

class CanvasErrors:
    def __init__(self, plugin):
        self.logger = plugin.logger

    def errorHandler(self, error):
        self.logger.info(getLog(error))
        self.logger.info(traceback.format_exc())
