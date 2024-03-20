if (!document.getElementById("sweetalert2-styling")) {
  let link = document.createElement("link");
  link.id = "sweetalert2-styling";
  link.href = "https://cdnjs.cloudflare.com/ajax/libs/limonte-sweetalert2/7.29.0/sweetalert2.min.css";
  link.rel = "stylesheet";
  document.head.appendChild(link);
}
if (!document.getElementById("sweetalert2-script")) {
  let script = document.createElement("script");
  script.id = "sweetalert2-script";
  script.src = "https://cdnjs.cloudflare.com/ajax/libs/limonte-sweetalert2/7.29.0/sweetalert2.min.js";
  document.head.appendChild(script);
}

const CanvasAlerts = {
  userAddedSuccess: username => {
    return swal({
      type: "success",
      title: "Canvas user successfully added",
      text: `${username} is now linked to this Canvas Hub.`
    });
  },
  userDeletedSuccess: username => {
    return swal({
      type: "success",
      title: "Canvas user successfully removed",
      text: `${username} is now removed from this Canvas Hub.`
    });
  },
  DeviceRegistrationError: () => {
    return swal({
      type: "info",
      title: "Unable to register Hub with Canvas",
      text: `There seems to be an issue registering your Canvas Hub with the server. Please make sure you are connected to the Internet and try again.`
    });
  },
  importantUpdate: version => {
    return swal({
      type: "info",
      title: `Important Update (Version ${version})`,
      html: `Canvas Plugin - Version ${version} is available for download.
      <br /><br />This version of the plugin contains important changes that allow a more stable connection to Canvas. Due to changes on the Canvas servers to facilitate these improvements, this update is required for 'Send to Canvas Hub' functionality.
      <br /><br />We apologize for the inconvenience.`,
      input: "checkbox",
      inputClass: "update-checkbox",
      inputPlaceholder: "Don't show me this again"
    });
  },
  newActivationCode: activationCode => {
    return swal({
      type: "success",
      title: `Your Activation Code is ${activationCode}`,
      html: `Visit <a href="https://canvas3d.io/connect?code=${activationCode}" target="_blank">Canvas</a> to link your account`
    });
  },
  resetCanvasData: data => {
    return swal({
      type: "success",
      title: `Canvas data reset successfully`,
      html: `Your Canvas data was reset successfully. Please restart OctoPrint now.`
    });
  },
  unlinkUser: () => {
    return swal({
      type: "info",
      title: "Unlink Canvas account",
      text: "Are you sure you would like to unlink this account?",
      confirmButtonText: "Unlink",
      showCancelButton: true,
      confirmButtonClass: "unlink-confirm-btn",
    });
  },
  AccountUnlinkError: () => {
    return swal({
      type: "info",
      title: "Unable to unlink account from Hub",
      text: `There seems to be an issue unlinking the account from your Canvas Hub. Please make sure you are connected to the Internet and try again.`
    });
  },
};