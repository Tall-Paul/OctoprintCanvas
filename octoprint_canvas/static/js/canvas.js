function CanvasViewModel(parameters) {
  var self = this;

  self.settings = parameters[0];
  self.appearance = parameters[1];
  self.files = parameters[2];

  self.iotConnected = ko.observable(false);
  self.userLinked = ko.observable(false);
  self.users = ko.observable(null);

  self.applyTheme = ko.observable();
  self.brand = ko.computed(function () {
    return self.applyTheme() ? "Canvas Hub" : "OctoPrint";
  });

  self.modifyAppearanceVM = () => {
    self.appearance.name.subscribe(function () {
      if (self.appearance.name() === "Canvas Hub" || self.appearance.name() === "OctoPrint") {
        self.appearance.name("");
      }
    });

    self.appearance.brand = ko.pureComputed(function () {
      if (self.applyTheme()) {
        if (self.appearance.name()) {
          return self.brand() + " (" + self.appearance.name() + ")";
        } else {
          return self.brand();
        }
      } else {
        if (self.appearance.name()) {
          return self.appearance.name();
        } else {
          return self.brand();
        }
      }
    });

    self.appearance.fullbrand = ko.pureComputed(function () {
      if (self.applyTheme()) {
        if (self.appearance.name()) {
          return self.brand() + ": " + self.appearance.name();
        } else {
          return self.brand();
        }
      } else {
        if (self.appearance.name()) {
          return self.brand() + ": " + self.appearance.name();
        } else {
          return self.brand();
        }
      }
    });

    self.appearance.title = ko.pureComputed(function () {
      if (self.applyTheme()) {
        if (self.appearance.name()) {
          return self.appearance.name() + " [" + self.brand() + "]";
        } else {
          return self.brand();
        }
      } else {
        if (self.appearance.name()) {
          return self.appearance.name() + " [" + self.brand() + "]";
        } else {
          return self.brand();
        }
      }
    });
  };

  self.modifyFilesVM = () => {
    self.files.getSuccessClass = function (data) {
      if (!data["prints"] || !data["prints"]["last"]) {
        if (data.name.includes(".mcf.gcode")) {
          return "palette-tag";
        } else {
          return "";
        }
      } else {
        if (data.name.includes(".mcf.gcode")) {
          return data["prints"]["last"]["success"] ? "text-success palette-tag" : "text-error palette-tag";
        } else {
          return data["prints"]["last"]["success"] ? "text-success" : "text-error";
        }
      }
    };
  };

  self.modifyAppearanceVM();
  self.modifyFilesVM();

  self.onBeforeBinding = () => {
    self.applyTheme(self.settings.settings.plugins.canvas.applyTheme());
  };

  self.onAfterBinding = () => {
    self.toggleTheme();
    self.files.requestData();
  };

  self.onStartupComplete = () => {
    CanvasUI.removePopup();
    CanvasUI.addNotificationList();
  };

  self.onEventFileAdded = payload => {
    let name = payload.name.split('.')
    if (name[name.length - 1] === 'gcode') {
      name.pop()
      if (name[name.length - 1] === 'mcf') {
        name.pop()
      }
    }
    name = name.join('.')
    if ($("body").find(`.canvas-progress-bar .file-download-name:contains("${name}")`)) {
      CanvasUI.updateFileReady(name);
    }
  };

  self.onDataUpdaterReconnect = () => {
    CanvasUI.removePopup();
    CanvasUI.addNotificationList();
  };

  self.onEventConnected = () => {
    self.toggleTheme();
  };

  self.addUser = () => {
    CanvasUI.loadingOverlay(true, 'addUser');
    const payload = {
      command: "addUser",
      data: {
      },
    };
    self.ajaxRequest(payload).always(() => {
      CanvasUI.loadingOverlay(false);
    })
  };

  $('.reset-canvas-data-button').on('click', () => {
    console.log('ready to reset canvas data');
    const payload = {
      command: "resetCanvasData",
      data: {
      },
    };
    self.ajaxRequest(payload).then((res) => {
      CanvasAlerts.resetCanvasData();
    })
  });

  self.showUnlinkModal = () => {
    $("body").on("click", (event) => {
      if (event.target.classList.contains('unlink-confirm-btn')) {
        CanvasUI.loadingOverlay(true, 'unlinkUser');
        const payload = {
          command: "unlinkUser",
          data: {
          },
        };
        self.ajaxRequest(payload).always(() => {
          CanvasUI.loadingOverlay(false);
        })
      }
    });
    CanvasAlerts.unlinkUser()
  };

  self.toggleTheme = () => {
    let applyTheme = self.settings.settings.plugins.canvas.applyTheme();

    // Apply theme immediately
    if (applyTheme) {
      self.applyTheme(true);
      $("html").addClass("canvas-theme");
      CanvasUI.toggleLogo(applyTheme);
    } else {
      self.applyTheme(false);
      $("html").removeClass("canvas-theme");
      CanvasUI.toggleLogo(applyTheme);
    }

    // Event listener for when user changes the theme settings
    $(".theme-input").on("change", event => {
      applyTheme = self.settings.settings.plugins.canvas.applyTheme();

      if (applyTheme) {
        self.applyTheme(true);
        $("html").addClass("canvas-theme");
        CanvasUI.toggleLogo(applyTheme);
      } else {
        self.applyTheme(false);
        $("html").removeClass("canvas-theme");
        CanvasUI.toggleLogo(applyTheme);
      }
    });
  };

  self.changeImportantUpdateSettings = condition => {
    displayImportantUpdateAlert = !condition;

    const payload = {
      command: "changeImportantUpdateSettings",
      condition: displayImportantUpdateAlert
    };

    self.ajaxRequest(payload).then(res => {
      self.settings.saveData();
    });
  };

  self.ajaxRequest = payload => {
    return $.ajax({
      url: API_BASEURL + "plugin/canvas",
      type: "POST",
      dataType: "json",
      data: JSON.stringify(payload),
      contentType: "application/json; charset=UTF-8"
    });
  };

  // Receive messages from the OctoPrint server
  self.onDataUpdaterPluginMessage = (pluginIdent, message) => {
    console.log(message);
    if (pluginIdent === "canvas") {
      switch (message.command) {
        case "UpdateIotConnection":
          console.log('UpdateIotConnection', message.data.iotConnected);
          self.iotConnected(message.data.iotConnected);
          self.userLinked(message.data.userLinked);
          break;
        case "DeviceRegistrationError":
          CanvasAlerts.DeviceRegistrationError();
          break;
        case "UpdateLinkedUsers":
          console.log('UpdateLinkedUsers', message.data.users);
          self.users(message.data.users);
          break;
        case "AccountLinked":
          CanvasAlerts.userAddedSuccess(message.data.username);
          self.userLinked(true);
          break;
        case "AccountUnlinked":
          CanvasAlerts.userDeletedSuccess(message.data.username);
          self.userLinked(false);
          break;
        case "AccountUnlinkError":
          CanvasAlerts.AccountUnlinkError();
          break;
        case "CanvasDownload":
          if (message.status === "starting") {
            CanvasUI.startDownload(message.data.filename);
          } else if (message.status === "downloading") {
            CanvasUI.updateDownloadProgress(message.data.filename, message.data.progress);
          } else if (message.status === "received") {
            CanvasUI.updateFileReceived(message.data.filename);
          }
          break;
        case "importantUpdate":
          $("body").on("click", ".update-checkbox input", event => {
            self.changeImportantUpdateSettings(event.target.checked);
          });
          CanvasAlerts.importantUpdate(message.data);
          break;
        case "newActivationCode":
          CanvasAlerts.newActivationCode(message.data);
          break;
        default:
        // do nothing
      }
    }
  };
}

/* ======================
  RUN
  ======================= */


$(function () {
  OCTOPRINT_VIEWMODELS.push({
    // This is the constructor to call for instantiating the plugin
    construct: CanvasViewModel, // This is a list of dependencies to inject into the plugin. The order will correspond to the "parameters" arguments above
    dependencies: ["settingsViewModel", "appearanceViewModel", "filesViewModel"], // Finally, this is the list of selectors for all elements we want this view model to be bound to.
    elements: ["#tab_plugin_canvas"]
  });
});
